# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Site Password Rotation Workflow Definition."""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError

from nv_config_manager_workflows.decorators import run_nv_config_manager_workflow
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
)
from nv_config_manager_workflows.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    stage_executor,
)
from nv_config_manager_workflows.ui import (
    Dependency,
    FormSchema,
    OptionSource,
    api_options,
    location_field,
)
from nv_config_manager_workflows.workflow_references import LocationReference
from nv_config_manager_workflows.workflows._form_sources import (
    DEVICE_STATUS_SOURCE,
    LOCATION_SOURCE,
    MANAGED_ROLE_SOURCE,
    MANAGED_TENANT_SOURCE,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_dcim import (
        DCIMLocationIdentifier,
        DCIMLocationType,
        dcim_location_reference,
    )

    from nv_config_manager_workflows.activities.config import get_ui_base_url
    from nv_config_manager_workflows.activities.dcim import (
        GetNetworkDevicesInput,
        GetNetworkDevicesOutput,
        get_network_devices,
    )
    from nv_config_manager_workflows.activities.dcim.models import NetworkDeviceData, Platform
    from nv_config_manager_workflows.activities.device_password_rotation import (
        FormatPasswordRotationResultsInput,
        format_password_rotation_results,
    )
    from nv_config_manager_workflows.mixins import ArchiveMixin, DeviceMixin
    from nv_config_manager_workflows.workflows.device_password_rotation import (
        DevicePasswordRotationInput,
        DevicePasswordRotationWorkflow,
    )


__all__ = [
    "CLONE_SEARCH_ATTRS",
    "DEFAULT_ACTIVITY_RETRY_POLICY",
    "DEFAULT_CONFIG_MANAGER_STATUS",
    "DEFAULT_CONFIG_MANAGER_TENANT",
    "PasswordRotationResultData",
    "SUPPORTED_PLATFORMS",
    "SitePasswordRotationInput",
    "SitePasswordRotationWorkflow",
]

# Default configurations
DEFAULT_CONFIG_MANAGER_STATUS = ["Active", "Provisioned"]
DEFAULT_CONFIG_MANAGER_TENANT = None
SUPPORTED_PLATFORMS = [Platform.CUMULUS_LINUX, Platform.NV_OS, Platform.JUNIPER_JUNOS]

# Search attributes to clone from parent to child workflows
CLONE_SEARCH_ATTRS = [
    USER_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
]

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ConfigSyntaxException", "DiffChangedException"],
)


class SitePasswordRotationInput(BaseModel):
    """Site Password Rotation Workflow Input Definition."""

    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
        "ui:order": [
            "location",
            "roles",
            "status",
            "tenant",
            "selected_secret",
            "location_type",
        ],
        "ui:globalOptions": {"hideSchemaDescriptions": True},
        "location": location_field(LOCATION_SOURCE, type_field="location_type"),
        "location_type": {"ui:widget": "hidden"},
        "roles": api_options(MANAGED_ROLE_SOURCE),
        "status": {**api_options(DEVICE_STATUS_SOURCE), "ui:title": "Device Status"},
        "tenant": api_options(MANAGED_TENANT_SOURCE),
        "selected_secret": {
            **api_options(
                OptionSource(
                    "/v1/parameter/password-users",
                    "label",
                    "value",
                    params={"managed_only": True},
                    depends_on={
                        "location": Dependency("location"),
                        "location_type": Dependency("location_type", required=False),
                        "role": Dependency("roles", required=False),
                        "status": Dependency("status"),
                        "tenant": Dependency("tenant", required=False),
                    },
                    clear_on_change=True,
                    response="options-v1",
                ),
                show_descriptions=True,
                meta_text={"key": "matching_device_count", "label": "Matching devices"},
            ),
            "ui:title": "Secret to Rotate",
        },
    }

    location: LocationReference = Field(
        min_length=1,
        description="Location containing the devices to update.",
    )
    location_type: DCIMLocationType | None = Field(
        default=None, description="DCIM location type for the location identifier."
    )
    selected_secret: Annotated[str, FormSchema(min_length=1)] = Field(
        description="Name of the managed secret containing the replacement password."
    )
    roles: list[str] = Field(
        default=[],
        description="Device roles used to filter the selected network devices.",
    )
    status: Annotated[list[str], FormSchema(min_items=1)] = Field(
        default=DEFAULT_CONFIG_MANAGER_STATUS,
        description="Device statuses used to filter the selected network devices.",
    )
    tenant: str | None = Field(
        default=DEFAULT_CONFIG_MANAGER_TENANT,
        description="Tenant used to filter the selected network devices.",
    )


class PasswordRotationResultData(BaseModel):
    """Password Rotation Result Data."""

    device: NetworkDeviceData | None = None
    success: bool
    error: str | None = None
    child_workflow_id: str | None = None


@workflow.defn
class SitePasswordRotationWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Site-wide password rotation workflow for security management."""

    # Workflow metadata
    workflow_name = "Site Password Rotation"
    workflow_group = "Lifecycle & Security"
    workflow_description = (
        "Rotate passwords across all devices in a site with coordinated deployment"
    )
    workflow_input_class = SitePasswordRotationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/site_password_rotation"
    workflow_namespace = "ngc"
    workflow_required_activities = (
        get_network_devices,
        get_ui_base_url,
        format_password_rotation_results,
    )

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_devices",
            description="Get list of devices for password rotation from the site.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="rotate_passwords",
            description="Execute password rotation on all devices.",
            requires_approval=False,
            depends_on=["get_devices"],
        )
        self.define_stage(
            name="format_result",
            description="Format the result of the password rotation.",
            requires_approval=False,
            depends_on=["rotate_passwords"],
        )

    class GetDevicesStageInput(StageInput):
        """Get Devices Stage Input."""

        location: DCIMLocationIdentifier
        roles: list[str] = []
        tenant: str | None = DEFAULT_CONFIG_MANAGER_TENANT
        status: list[str] = DEFAULT_CONFIG_MANAGER_STATUS

    class GetDevicesStageOutput(StageOutput):
        """Get Devices Stage Output."""

        devices: list[NetworkDeviceData]

    @stage_executor("get_devices")
    async def get_devices(self, stage_input: GetDevicesStageInput) -> GetDevicesStageOutput:
        """Get devices from the site that match the criteria."""
        result: GetNetworkDevicesOutput = await workflow.execute_activity(
            get_network_devices,
            GetNetworkDevicesInput(
                site=stage_input.location,
                roles=stage_input.roles,
                tenant=stage_input.tenant,
                status=stage_input.status,
                managed_only=True,
                platforms=SUPPORTED_PLATFORMS,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return SitePasswordRotationWorkflow.GetDevicesStageOutput(
            devices=result.devices,
            display=self.device_markdown_table(result.devices),
        )

    class RotatePasswordsStageInput(StageInput):
        """Rotate Passwords Stage Input."""

        devices: list[NetworkDeviceData]
        selected_secret: str

    class RotatePasswordsStageOutput(StageOutput):
        """Rotate Passwords Stage Output."""

        successful_devices: dict[str, PasswordRotationResultData]
        failed_devices: dict[str, PasswordRotationResultData]

    @stage_executor("rotate_passwords")
    async def rotate_passwords(
        self, stage_input: RotatePasswordsStageInput
    ) -> RotatePasswordsStageOutput:
        """Execute password rotation on all devices using child workflows."""
        handles = {}

        # Clone search attributes from parent to child workflows
        search_attrs = {
            k: v for k, v in workflow.info().search_attributes.items() if k in CLONE_SEARCH_ATTRS
        }

        # Start child workflows for each device
        for device in stage_input.devices:
            search_attrs.update({DEVICE_ID_SEARCH_ATTRIBUTE: [device.id]})

            handles[device.name] = await workflow.start_child_workflow(
                DevicePasswordRotationWorkflow.run,
                DevicePasswordRotationInput(
                    device_id=device.id, selected_secret=stage_input.selected_secret
                ),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                search_attributes=search_attrs,
            )
            self.append_child_workflow("rotate_passwords", handles[device.name].id)

        # Process completions as they happen to show progress
        successful_devices = {}
        failed_devices = {}
        remaining_items = list(handles.items())
        total_count = len(handles)

        while remaining_items:
            # Extract handles from remaining items
            remaining_handles = [handle for _, handle in remaining_items]

            # Wait for at least one to complete
            done, pending = await asyncio.wait(
                remaining_handles, return_when=asyncio.FIRST_COMPLETED
            )

            # Process completed handles
            for completed_handle in done:
                # Find the device_name for this handle
                device_name = None
                for i, (name, handle) in enumerate(remaining_items):
                    if handle is completed_handle:
                        device_name = name
                        remaining_items.pop(i)
                        break

                if device_name is None:
                    continue  # Shouldn't happen

                device_data = next((d for d in stage_input.devices if d.name == device_name), None)

                try:
                    completed_handle.result()
                    successful_devices[device_name] = PasswordRotationResultData(
                        device=device_data,
                        success=True,
                        child_workflow_id=completed_handle.id,
                    )
                except ChildWorkflowError as exc:
                    failed_devices[device_name] = PasswordRotationResultData(
                        device=device_data,
                        success=False,
                        error=str(exc.cause),
                        child_workflow_id=completed_handle.id,
                    )

            # Update the display with current progress
            completed_count = len(successful_devices) + len(failed_devices)
            in_progress_count = total_count - completed_count

            display_lines = [
                f"**Password rotation in progress ({completed_count}/{total_count} completed):**",
                f"- {len(successful_devices)} devices updated",
                f"- {len(failed_devices)} devices failed",
                f"- {in_progress_count} devices in progress",
            ]

            # Update stage output with current progress
            updated_output = SitePasswordRotationWorkflow.RotatePasswordsStageOutput(
                successful_devices=successful_devices.copy(),
                failed_devices=failed_devices.copy(),
                display="\n".join(display_lines),
            )
            self.set_stage_output("rotate_passwords", updated_output)

        return SitePasswordRotationWorkflow.RotatePasswordsStageOutput(
            successful_devices=successful_devices,
            failed_devices=failed_devices,
            display=f"Password rotation completed for {len(successful_devices) + len(failed_devices)} devices. "
            f"Success: {len(successful_devices)}, Failed: {len(failed_devices)}",
        )

    class FormatResultStageInput(StageInput):
        """Format Result Stage Input."""

        successful_devices: dict[str, PasswordRotationResultData]
        failed_devices: dict[str, PasswordRotationResultData]
        total_devices: int

    @stage_executor("format_result")
    async def format_result(self, stage_input: FormatResultStageInput) -> StageOutput:
        """Format the results of password rotation into a summary."""
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Convert PasswordRotationResultData objects to dictionaries for the activity
        successful_devices_dict = {}
        for device_name, result_data in stage_input.successful_devices.items():
            successful_devices_dict[device_name] = {
                "success": result_data.success,
                "child_workflow_id": result_data.child_workflow_id,
            }

        failed_devices_dict = {}
        for device_name, result_data in stage_input.failed_devices.items():
            failed_devices_dict[device_name] = {
                "success": result_data.success,
                "error": result_data.error,
                "child_workflow_id": result_data.child_workflow_id,
            }

        display_text = await workflow.execute_activity(
            format_password_rotation_results,
            FormatPasswordRotationResultsInput(
                successful_devices=successful_devices_dict,
                failed_devices=failed_devices_dict,
                total_devices=stage_input.total_devices,
                ui_base_url=ui_base_url,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return StageOutput(display=display_text)

    def device_markdown_table(self, devices: list[NetworkDeviceData]) -> str:
        """Generate a markdown table of devices."""
        if not devices:
            return "No devices found."

        lines = [
            "| Device | Platform | Role | Site |",
            "|--------|----------|------|------|",
        ]
        for device in devices:
            lines.append(f"| {device.name} | {device.platform} | {device.role} | {device.site} |")
        return "\n".join(lines)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: SitePasswordRotationInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Execute site password rotation workflow."""
        self.set_input(workflow_input)

        devices_output = await self.get_devices(
            SitePasswordRotationWorkflow.GetDevicesStageInput(
                location=dcim_location_reference(
                    workflow_input.location, workflow_input.location_type
                ),
                roles=workflow_input.roles,
                tenant=workflow_input.tenant,
                status=workflow_input.status,
            )
        )

        rotation_output = await self.rotate_passwords(
            SitePasswordRotationWorkflow.RotatePasswordsStageInput(
                devices=devices_output.devices,
                selected_secret=workflow_input.selected_secret,
            )
        )

        format_output = await self.format_result(
            SitePasswordRotationWorkflow.FormatResultStageInput(
                successful_devices=rotation_output.successful_devices,
                failed_devices=rotation_output.failed_devices,
                total_devices=len(devices_output.devices),
            )
        )

        await self.archive_results()

        return format_output.display
