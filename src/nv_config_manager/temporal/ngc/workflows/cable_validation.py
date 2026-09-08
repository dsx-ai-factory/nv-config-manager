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
"""Site Cable Validation Workflow."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated, Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
    upsert_missing_search_attributes,
)
from nv_config_manager.temporal.common.workflow_references import (
    DEVICE_REFERENCE,
    DeviceReference,
    LocationReference,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.device import (
        DeviceArpTable,
        DeviceMacTable,
        DeviceNeighborData,
    )
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import (
        DeviceMixin,
        NetworkDeviceData,
        Platform,
    )
    from nv_config_manager.temporal.ngc.activities.cable_validation import (
        CableValidationResultData,
        DecorateResultActivityInput,
        FormatDeviceValidationResultInput,
        FormatResultsActivityInput,
        InvalidCable,
        ValidateDeviceNeighborsInput,
        ValidateDeviceNeighborsResult,
        decorate_result,
        format_device_validation_result,
        format_results,
        validate_device_neighbors,
    )
    from nv_config_manager.temporal.ngc.activities.config import build_workflow_url, get_ui_base_url
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        GetNetworkDevicesInput,
        get_network_device,
        get_network_devices,
    )
    from nv_config_manager.temporal.ngc.activities.device import (
        get_device_actual_neighbors,
        get_device_arp_table,
        get_device_intended_neighbors,
        get_device_mac_table,
        validate_hostname,
    )


ACTIVITY_NO_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
DEFAULT_CONFIG_MANAGER_STATUS = ["Active", "Provisioned"]
DEFAULT_CONFIG_MANAGER_TENANT = None
# list of search attributes to clone from parent to child
CLONE_SEARCH_ATTRS = [
    USER_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
]

SUPPORTED_PLATFORMS = [
    Platform.CUMULUS_LINUX,
    Platform.ARISTA_EOS,
    Platform.NV_OS,
    Platform.JUNIPER_JUNOS,
]
DEVICE_CABLE_VALIDATION_DEVICE_DESCRIPTION = (
    "Preloaded data for the target network device, if available."
)


class SiteCableValidationInput(BaseModel):
    """Input for Site Cable Validation Workflow."""

    site: LocationReference = Field(description="Site containing the network devices to validate.")
    roles: list[str] = Field(
        default=[],
        description="Device roles used to filter the selected network devices.",
    )
    status: list[str] = Field(
        default=DEFAULT_CONFIG_MANAGER_STATUS,
        description="Device statuses used to filter the selected network devices.",
    )
    tenant: str | None = Field(
        default=DEFAULT_CONFIG_MANAGER_TENANT,
        description="Tenant used to filter the selected network devices.",
    )
    device_type_ids: list[str] = Field(
        default=[], description="Device type identifiers used to filter network devices."
    )
    raise_for_invalid: bool = Field(
        default=False, description="Whether invalid cabling should fail the workflow."
    )


class DeviceCableValidationInput(BaseModel):
    """Input for Device Cable Validation Workflow."""

    device_id: DeviceReference = Field(description="Identifier of the network device to validate.")
    device: Annotated[
        Annotated[
            NetworkDeviceData,
            Field(description=DEVICE_CABLE_VALIDATION_DEVICE_DESCRIPTION),
        ]
        | None,
        DEVICE_REFERENCE,
    ] = Field(
        default=None,
        description=DEVICE_CABLE_VALIDATION_DEVICE_DESCRIPTION,
    )
    ignore_no_neighbor: bool = Field(
        default=False,
        description="Whether interfaces without discovered neighbors should be ignored.",
    )


class DeviceCableValidationResult(BaseModel, validate_assignment=True):
    """Result for a Device Cable Validation."""

    # key is the interface name
    interfaces: dict[str, InvalidCable] = {}


class SiteCableValidationResult(BaseModel):
    """Result for a Site Cable Validation."""

    markdown: str


@workflow.defn
class SiteCableValidationWorkflow(WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Site-wide cable validation workflow for network infrastructure."""

    # Workflow metadata
    workflow_name = "Site Cable Validation"
    workflow_description = (
        "Validate cable connections for all devices in a site against intended topology"
    )
    workflow_input_class = SiteCableValidationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/site_cable_validation"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Workflow Constructor."""
        super().__init__()
        self.define_stage(
            name="get_devices_to_validate",
            description="Get the list of devices to validate for this site",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="validate_devices",
            description="Validate all devices",
            requires_approval=False,
            depends_on=["get_devices_to_validate"],
        )
        self.define_stage(
            name="format_result",
            description="Generate cable validation report",
            requires_approval=False,
            depends_on=["validate_devices"],
        )

    class GetDevicesStageInput(StageInput):
        """Get Devices Stage Input."""

        site: str
        roles: list[str]
        status: list[str]
        tenant: str | None
        device_type_ids: list[str]

    class GetDevicesStageOutput(StageOutput):
        """Get Devices Stage Output."""

        devices: list[NetworkDeviceData]

    @stage_executor("get_devices_to_validate")
    async def get_devices_to_validate(
        self, stage_input: SiteCableValidationWorkflow.GetDevicesStageInput
    ) -> SiteCableValidationWorkflow.GetDevicesStageOutput:
        """Get all devices to validate from the DCIM."""
        result = await workflow.execute_activity(
            get_network_devices,
            GetNetworkDevicesInput(
                site=stage_input.site,
                roles=stage_input.roles,
                status=stage_input.status,
                tenant=stage_input.tenant,
                device_type_ids=stage_input.device_type_ids,
                managed_only=True,
                platforms=SUPPORTED_PLATFORMS,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not result.devices:
            platform_names = [platform.value for platform in SUPPORTED_PLATFORMS]
            display = (
                "No devices found matching the specified filters "
                f"(location={stage_input.site}, roles={stage_input.roles}, "
                f"status={stage_input.status}, tenant={stage_input.tenant}, "
                f"platforms={platform_names})."
            )
        else:
            display = self.markdown_table(result.devices)

        return SiteCableValidationWorkflow.GetDevicesStageOutput(
            devices=result.devices, display=display
        )

    class ValidateDevicesStageInput(StageInput):
        """Validate Devices Stage Input."""

        devices: list[NetworkDeviceData]
        legacy_site: bool = False

    class ValidateDevicesStageOutput(StageOutput):
        """Validate Devices Stage Output."""

        devices: dict[str, CableValidationResultData]
        failed_devices: dict[str, str]

    @stage_executor("validate_devices")
    async def validate_devices(
        self, stage_input: SiteCableValidationWorkflow.ValidateDevicesStageInput
    ) -> ValidateDevicesStageOutput:
        """Validate all devices for a site."""
        # Get UI base URL for creating workflow links
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        handles = {}
        # Search Attributes to clone from parent to child
        search_attrs = {
            k: v for k, v in workflow.info().search_attributes.items() if k in CLONE_SEARCH_ATTRS
        }
        for device in stage_input.devices:
            # Attach the device search attribute as well
            search_attrs.update({DEVICE_ID_SEARCH_ATTRIBUTE: [device.id]})
            handles[device.name] = await workflow.start_child_workflow(
                DeviceCableValidationWorkflow.run,
                DeviceCableValidationInput(device_id=device.id, device=device),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                search_attributes=search_attrs,
            )
            self.append_child_workflow("validate_devices", handles[device.name].id)

        # Set initial stage output
        total_count = len(handles)
        initial_display = (
            f"**Device validation in progress (0/{total_count} completed):**\n"
            f"- 0 devices validated successfully\n"
            f"- 0 devices failed\n"
        )
        initial_output = self.ValidateDevicesStageOutput(
            devices={},
            failed_devices={},
            display=initial_display,
        )
        self.set_stage_output("validate_devices", initial_output)

        # Helper function to generate device links (when remaining < 10)
        def generate_device_links(
            remaining_items: list[tuple[str, workflow.ChildWorkflowHandle[Any, Any]]],
        ) -> list[str]:
            """Generate child workflow links for remaining devices."""
            device_links = []
            for device_name, handle in remaining_items:
                child_workflow_url = build_workflow_url(ui_base_url, handle.id)
                device_links.append(f"- [{device_name}]({child_workflow_url})")

            return device_links

        # Wait for devices to complete one by one and update status incrementally
        failed_devices = {}
        child_results = {}
        stage_results = {}
        remaining_items = list(handles.items())

        while remaining_items:
            # Extract handles from remaining items
            remaining_handles = [handle for _, handle in remaining_items]

            # Wait for at least one to complete
            done, _ = await asyncio.wait(remaining_handles, return_when=asyncio.FIRST_COMPLETED)

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

                # Find the device data for this device
                device_data = next((d for d in stage_input.devices if d.name == device_name), None)

                try:
                    result = completed_handle.result()
                    child_results[device_name] = result
                    stage_results[device_name] = CableValidationResultData(
                        interfaces=result.interfaces, device=device_data
                    )
                except ChildWorkflowError as exc:
                    failed_devices[device_name] = str(exc.cause)

            # Update the display with current progress
            completed_count = len(stage_results) + len(failed_devices)
            remaining_count = total_count - completed_count
            successful_count = len(stage_results)
            failed_count = len(failed_devices)

            # Generate display message
            if remaining_count > 0:
                display_lines = [
                    f"**Device validation in progress ({completed_count}/{total_count} completed):**",
                    f"- {successful_count} devices validated successfully",
                    f"- {failed_count} devices failed",
                    f"- {remaining_count} devices remaining",
                ]
            else:
                display_lines = [
                    "**Device validation completed:**",
                    f"- {successful_count} devices validated successfully",
                    f"- {failed_count} devices failed",
                ]

            # Add device links if remaining devices < 10
            if remaining_count > 0 and remaining_count < 10:
                device_links = generate_device_links(remaining_items)
                if device_links:
                    display_lines.append("\n**Remaining devices (click to view details):**")
                    display_lines.extend(device_links)

            display = "\n".join(display_lines)

            # Update the stage output with current progress
            updated_output = self.ValidateDevicesStageOutput(
                devices=stage_results.copy(),
                failed_devices=failed_devices.copy(),
                display=display,
            )
            self.set_stage_output("validate_devices", updated_output)

        # Return final output
        return self.ValidateDevicesStageOutput(
            devices=stage_results,
            failed_devices=failed_devices,
            display=display,
        )

    class FormatResultStageInput(StageInput):
        """Format result stage input."""

        devices: dict[str, CableValidationResultData]
        legacy_site: bool = False
        failed_devices: dict[str, str] = {}

    class FormatResultStageOutput(StageOutput):
        """Format result stage output."""

    @stage_executor("format_result")
    async def format_result(self, stage_input: FormatResultStageInput) -> FormatResultStageOutput:
        """Format results stage."""
        results = await workflow.execute_activity(
            format_results,
            FormatResultsActivityInput(
                devices=stage_input.devices,
                # Legacy parameter, no longer used
                ignore_no_neighbor=False,
                failed_devices=stage_input.failed_devices,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=ACTIVITY_NO_RETRY_POLICY,
        )
        return self.FormatResultStageOutput(display=results)

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self,
        workflow_input: SiteCableValidationInput,
    ) -> SiteCableValidationResult:
        """Run the workflow."""
        self.set_input(workflow_input)
        upsert_missing_search_attributes({SITE_SEARCH_ATTRIBUTE: [workflow_input.site]})

        devices_output = await self.get_devices_to_validate(
            SiteCableValidationWorkflow.GetDevicesStageInput(
                site=workflow_input.site,
                roles=workflow_input.roles,
                status=workflow_input.status,
                tenant=workflow_input.tenant,
                device_type_ids=workflow_input.device_type_ids,
            )
        )

        if not devices_output.devices:
            self.set_stage_state("validate_devices", StateEnum.UNREACHABLE)
            self.set_stage_state("format_result", StateEnum.UNREACHABLE)
            await self.archive_results()
            return SiteCableValidationResult(
                markdown="No devices found matching the specified filters."
            )

        validation_output = await self.validate_devices(
            SiteCableValidationWorkflow.ValidateDevicesStageInput(devices=devices_output.devices)
        )

        output = await self.format_result(
            self.FormatResultStageInput(
                devices=validation_output.devices,
                failed_devices=validation_output.failed_devices,
            )
        )

        await self.archive_results()

        return SiteCableValidationResult(markdown=output.display)


@workflow.defn
class DeviceCableValidationWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Single device cable validation workflow for network infrastructure."""

    # Workflow metadata
    workflow_name = "Device Cable Validation"
    workflow_description = (
        "Validate cable connections for a specific device against intended topology"
    )
    workflow_input_class = DeviceCableValidationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/device_cable_validation"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Workflow constructor."""
        super().__init__()
        self.define_stage(
            name="get_device_data",
            description="Get the device data if not provided already",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="validate_device_hostname",
            description="Ensure device hostnames match the DCIM",
            requires_approval=False,
            depends_on=["get_device_data"],
        )
        self.define_stage(
            name="get_device_intended_neighbors",
            description="Get the list of intended connected interfaces from the DCIM",
            requires_approval=False,
            depends_on=["validate_device_hostname"],
        )
        self.define_stage(
            name="get_device_actual_neighbors",
            description=("Get the list of the actual connected interfaces from the device"),
            requires_approval=False,
            depends_on=["validate_device_hostname"],
        )
        self.define_stage(
            name="get_device_mac_table",
            description="Get the MAC addresses from the device FDB",
            requires_approval=False,
            depends_on=["validate_device_hostname"],
        )
        self.define_stage(
            name="validate_connections",
            description="Validate the intended vs actual connections",
            requires_approval=False,
            retryable=False,
            depends_on=[
                "get_device_intended_neighbors",
                "get_device_actual_neighbors",
                "get_device_mac_table",
            ],
        )

    class IntendedNeighborStageInput(StageInput):
        """Get Device Intended Neighbors Stage Input."""

        device: NetworkDeviceData

    class IntendedNeighborStageOutput(StageOutput):
        """Get Device Intended Neighbors Stage Output."""

        intended_neighbors: DeviceNeighborData

    @stage_executor("get_device_intended_neighbors")
    async def get_device_intended_neighbors(
        self, stage_input: IntendedNeighborStageInput
    ) -> IntendedNeighborStageOutput:
        """Get intended connections from the DCIM."""
        result = await workflow.execute_activity(
            get_device_intended_neighbors,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        neighbor_list = list(result.neighbors.values())
        display = (
            self.markdown_table(neighbor_list) if neighbor_list else "No intended neighbors found."
        )
        return DeviceCableValidationWorkflow.IntendedNeighborStageOutput(
            intended_neighbors=result, display=display
        )

    class NetworkDeviceDataStageInput(StageInput):
        """Get Device Data Stage Input."""

        device_id: str
        device: NetworkDeviceData | None

    class NetworkDeviceDataStageOutput(StageOutput):
        """Get Device Data Stage Output."""

        device: NetworkDeviceData

    @stage_executor("get_device_data")
    async def get_device_data(
        self, stage_input: NetworkDeviceDataStageInput
    ) -> NetworkDeviceDataStageOutput:
        """Get device data from the DCIM."""
        if stage_input.device:
            # When called from another workflow, this may already be present
            device = stage_input.device
        else:
            result = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=stage_input.device_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            device = result.device
        return DeviceCableValidationWorkflow.NetworkDeviceDataStageOutput(
            device=device, display=self.markdown_table(device)
        )

    class ValidateHostnameStageInput(StageInput):
        """Validate device hostname stage input."""

        device: NetworkDeviceData

    class ValidateHostnameStageOutput(StageOutput):
        """Validate device hostname stage output."""

    @stage_executor("validate_device_hostname")
    async def validate_device_hostname(
        self, stage_input: ValidateHostnameStageInput
    ) -> ValidateHostnameStageOutput:
        """Validate device hostname stage."""
        result = await workflow.execute_activity(
            validate_hostname,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return self.ValidateHostnameStageOutput(display=f"```{result.hostname}```")

    class ActualNeighborStageInput(StageInput):
        """Get Device Observed Neighbors Stage Input."""

        device: NetworkDeviceData

    class ActualNeighborStageOutput(StageOutput):
        """Get Device Observed Neighbors Stage Output."""

        actual_neighbors: DeviceNeighborData

    @stage_executor("get_device_actual_neighbors")
    async def get_device_actual_neighbors(
        self, stage_input: ActualNeighborStageInput
    ) -> ActualNeighborStageOutput:
        """Get actual connections from the device LLDP neighbors."""
        result = await workflow.execute_activity(
            get_device_actual_neighbors,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        neighbor_list = list(result.neighbors.values())
        display = (
            self.markdown_table(neighbor_list) if neighbor_list else "No actual neighbors found."
        )
        return DeviceCableValidationWorkflow.ActualNeighborStageOutput(
            actual_neighbors=result, display=display
        )

    class DeviceMacTableStageInput(StageInput):
        """Get Device MAC Table Stage Input."""

        device: NetworkDeviceData

    class DeviceMacTableStageOutput(StageOutput):
        """Get Device MAC Table Stage Input."""

        mac_table: DeviceMacTable
        arp_table: DeviceArpTable

    @stage_executor("get_device_mac_table")
    async def get_device_mac_table(
        self, stage_input: DeviceMacTableStageInput
    ) -> DeviceMacTableStageOutput:
        """Get all MAC entries from a device FDB."""
        result = await workflow.execute_activity(
            get_device_mac_table,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        arp_table = await workflow.execute_activity(
            get_device_arp_table,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        mac_entries = list(result.by_mac.values())
        display = self.markdown_table(mac_entries) if mac_entries else "No MAC table entries found."
        return DeviceCableValidationWorkflow.DeviceMacTableStageOutput(
            mac_table=result, arp_table=arp_table, display=display
        )

    class ValidateConnectionsStageInput(StageInput):
        """Validate Connections Stage Input."""

        device: NetworkDeviceData
        intended: DeviceNeighborData
        actual: DeviceNeighborData
        mac_table: DeviceMacTable
        arp_table: DeviceArpTable
        ignore_no_neighbor: bool = False

    class ValidateConnectionsStageOutput(StageOutput):
        """Validate Connections Stage Output."""

        validation_result: ValidateDeviceNeighborsResult

    @stage_executor("validate_connections")
    async def validate_connections(
        self, stage_input: ValidateConnectionsStageInput
    ) -> ValidateConnectionsStageOutput:
        """Produce a list of mismatches between intended and actual cabling."""
        result = await workflow.execute_activity(
            validate_device_neighbors,
            ValidateDeviceNeighborsInput(
                device=stage_input.device,
                intended=stage_input.intended,
                actual=stage_input.actual,
                mac_table=stage_input.mac_table,
                arp_table=stage_input.arp_table,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        single_device_results = {
            stage_input.device.name: CableValidationResultData(
                interfaces=result.interfaces,
                device=stage_input.device,
            )
        }
        decorated = await workflow.execute_activity(
            decorate_result,
            DecorateResultActivityInput(devices=single_device_results),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        decorated_result = ValidateDeviceNeighborsResult(
            interfaces=decorated.devices[stage_input.device.name].interfaces
        )

        display: str = await workflow.execute_activity(
            format_device_validation_result,
            FormatDeviceValidationResultInput(
                device=stage_input.device,
                validation_result=decorated_result,
                ignore_no_neighbor=stage_input.ignore_no_neighbor,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return DeviceCableValidationWorkflow.ValidateConnectionsStageOutput(
            validation_result=decorated_result, display=display
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self,
        workflow_input: DeviceCableValidationInput,
    ) -> DeviceCableValidationResult:
        """Run the device cable validation workflow."""
        self.set_input(workflow_input)

        device_data = (
            await self.get_device_data(
                DeviceCableValidationWorkflow.NetworkDeviceDataStageInput(
                    device=workflow_input.device, device_id=workflow_input.device_id
                )
            )
        ).device
        DeviceMixin.attach_device_search_attributes(device_data)

        await self.validate_device_hostname(self.ValidateHostnameStageInput(device=device_data))

        intended_stage = asyncio.create_task(
            self.get_device_intended_neighbors(
                DeviceCableValidationWorkflow.IntendedNeighborStageInput(device=device_data)
            )
        )

        actual_stage = asyncio.create_task(
            self.get_device_actual_neighbors(
                DeviceCableValidationWorkflow.ActualNeighborStageInput(device=device_data)
            )
        )

        mac_stage = asyncio.create_task(
            self.get_device_mac_table(
                DeviceCableValidationWorkflow.DeviceMacTableStageInput(device=device_data)
            )
        )

        intended_output = await intended_stage
        actual_output = await actual_stage
        mac_output = await mac_stage

        validation_output = await self.validate_connections(
            DeviceCableValidationWorkflow.ValidateConnectionsStageInput(
                device=device_data,
                intended=intended_output.intended_neighbors,
                actual=actual_output.actual_neighbors,
                mac_table=mac_output.mac_table,
                arp_table=mac_output.arp_table,
                ignore_no_neighbor=workflow_input.ignore_no_neighbor,
            )
        )

        await self.archive_results()

        return DeviceCableValidationResult(
            interfaces=validation_output.validation_result.interfaces
        )
