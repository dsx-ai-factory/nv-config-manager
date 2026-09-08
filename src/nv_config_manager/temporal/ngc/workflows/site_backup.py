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
"""Site Configuration Backup Workflow Definition."""

import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

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
from nv_config_manager.temporal.common.workflow_references import LocationReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
    from nv_config_manager.temporal.ngc.activities.config import get_ui_base_url
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDevicesInput,
        GetNetworkDevicesOutput,
        get_network_devices,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )

DEFAULT_CONFIG_MANAGER_STATUS = ["Active", "Provisioned"]
DEFAULT_CONFIG_MANAGER_TENANT = None
SUPPORTED_PLATFORMS = [
    Platform.ARISTA_EOS,
    Platform.CUMULUS_LINUX,
    Platform.NV_OS,
    Platform.JUNIPER_JUNOS,
]

CLONE_SEARCH_ATTRS = [
    USER_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
]

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ConfigSyntaxException", "DiffChangedException"],
)

BACKUP_CHILD_RUN_TIMEOUT = timedelta(minutes=15)


class SiteBackupInput(BaseModel):
    """Site Configuration Backup Workflow Input Definition."""

    site: LocationReference = Field(
        min_length=1,
        description="Site containing the network devices to back up.",
    )
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
    backup_enabled_only: bool = Field(
        default=True,
        description="When true, only devices with backup enabled are included.",
    )
    user: str | None = Field(
        default=None,
        description="User that requested the site backup.",
    )
    user_domain: str | None = Field(
        default=None,
        description="Domain of the user requesting the site backup.",
    )


class BackupResultData(BaseModel):
    """Result data for a single device backup within a site backup."""

    device: NetworkDeviceData | None = None
    success: bool
    changed: bool | None = None
    error: str | None = None
    child_workflow_id: str


@workflow.defn
class SiteBackupWorkflow(WorkflowMetadataMixin, StageMixin, ArchiveMixin):
    """Site-wide configuration backup workflow for network infrastructure."""

    workflow_name = "Site Configuration Backup"
    workflow_description = (
        "Back up running configurations for in-scope devices at a site to the Config Store"
    )
    workflow_input_class = SiteBackupInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/site_backup"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow stages."""
        super().__init__()
        self.define_stage(
            name="get_devices",
            description="Get the list of devices to back up for this site.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="perform_backups",
            description="Back up configurations for all selected devices.",
            requires_approval=False,
            depends_on=["get_devices"],
        )
        self.define_stage(
            name="format_result",
            description="Summarize site backup results.",
            requires_approval=False,
            depends_on=["perform_backups"],
        )

    class GetDevicesStageInput(StageInput):
        """Get Devices Stage Input."""

        site: str
        roles: list[str]
        status: list[str]
        tenant: str | None
        backup_enabled_only: bool

    class GetDevicesStageOutput(StageOutput):
        """Get Devices Stage Output."""

        devices: list[NetworkDeviceData]

    @stage_executor("get_devices")
    async def get_devices(self, stage_input: GetDevicesStageInput) -> GetDevicesStageOutput:
        """Get devices from the site that match the criteria."""
        result: GetNetworkDevicesOutput = await workflow.execute_activity(
            get_network_devices,
            GetNetworkDevicesInput(
                site=stage_input.site,
                roles=stage_input.roles,
                tenant=stage_input.tenant,
                status=stage_input.status,
                managed_only=True,
                backup_enabled=True if stage_input.backup_enabled_only else None,
                platforms=SUPPORTED_PLATFORMS,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not result.devices:
            display = (
                "No devices found matching the specified filters "
                f"(site={stage_input.site}, roles={stage_input.roles}, "
                f"status={stage_input.status}, tenant={stage_input.tenant}, "
                f"backup_enabled_only={stage_input.backup_enabled_only})."
            )
        else:
            display = self.markdown_table(result.devices)

        return SiteBackupWorkflow.GetDevicesStageOutput(devices=result.devices, display=display)

    class PerformBackupsStageInput(StageInput):
        """Perform Backups Stage Input."""

        devices: list[NetworkDeviceData]
        user: str
        user_domain: str | None

    class PerformBackupsStageOutput(StageOutput):
        """Perform Backups Stage Output."""

        successful_devices: dict[str, BackupResultData]
        failed_devices: dict[str, BackupResultData]

    @stage_executor("perform_backups")
    async def perform_backups(
        self, stage_input: PerformBackupsStageInput
    ) -> PerformBackupsStageOutput:
        """Execute configuration backups on in-scope devices using child workflows."""
        handles: dict[str, Any] = {}

        search_attrs = {
            key: value
            for key, value in workflow.info().search_attributes.items()
            if key in CLONE_SEARCH_ATTRS
        }

        for device in stage_input.devices:
            search_attrs.update({DEVICE_ID_SEARCH_ATTRIBUTE: [device.id]})

            backup_input = BackupInput(
                device_id=device.id,
                trigger=TriggerEnum.WORKFLOW,
                user=stage_input.user,
                user_domain=stage_input.user_domain,
                workflow_id=workflow.info().workflow_id,
            )

            handles[device.name] = await workflow.start_child_workflow(
                BackupWorkflow.run,
                backup_input,
                run_timeout=BACKUP_CHILD_RUN_TIMEOUT,
                search_attributes=search_attrs,
            )
            self.append_child_workflow("perform_backups", handles[device.name].id)

        successful_devices: dict[str, BackupResultData] = {}
        failed_devices: dict[str, BackupResultData] = {}
        remaining_items = list(handles.items())
        total_count = len(handles)

        while remaining_items:
            remaining_handles = [handle for _, handle in remaining_items]
            done, _ = await workflow.wait(remaining_handles, return_when=asyncio.FIRST_COMPLETED)

            for completed_handle in done:
                device_name = None
                for index, (name, handle) in enumerate(remaining_items):
                    if handle is completed_handle:
                        device_name = name
                        remaining_items.pop(index)
                        break

                if device_name is None:
                    continue

                device_data = next(
                    (device for device in stage_input.devices if device.name == device_name),
                    None,
                )

                try:
                    changed = completed_handle.result()
                    successful_devices[device_name] = BackupResultData(
                        device=device_data,
                        success=True,
                        changed=changed,
                        child_workflow_id=completed_handle.id,
                    )
                except ChildWorkflowError as exc:
                    failed_devices[device_name] = BackupResultData(
                        device=device_data,
                        success=False,
                        error=str(exc.cause),
                        child_workflow_id=completed_handle.id,
                    )

            completed_count = len(successful_devices) + len(failed_devices)
            in_progress_count = total_count - completed_count
            display_lines = [
                f"**Site backup in progress ({completed_count}/{total_count} completed):**",
                f"- {len(successful_devices)} devices backed up",
                f"- {len(failed_devices)} devices failed",
                f"- {in_progress_count} devices in progress",
            ]
            self.set_stage_output(
                "perform_backups",
                SiteBackupWorkflow.PerformBackupsStageOutput(
                    successful_devices=successful_devices.copy(),
                    failed_devices=failed_devices.copy(),
                    display="\n".join(display_lines),
                ),
            )

        changed_count = sum(1 for result in successful_devices.values() if result.changed)
        return SiteBackupWorkflow.PerformBackupsStageOutput(
            successful_devices=successful_devices,
            failed_devices=failed_devices,
            display=(
                f"Site backup completed for {len(successful_devices) + len(failed_devices)} devices. "
                f"Success: {len(successful_devices)} ({changed_count} changed), "
                f"Failed: {len(failed_devices)}"
            ),
        )

    class FormatResultStageInput(StageInput):
        """Format Result Stage Input."""

        site: str
        successful_devices: dict[str, BackupResultData]
        failed_devices: dict[str, BackupResultData]
        total_devices: int

    @stage_executor("format_result")
    async def format_result(self, stage_input: FormatResultStageInput) -> StageOutput:
        """Format the results of the site backup into a summary."""
        ui_base_url: str | None = None
        try:
            ui_base_url = await workflow.execute_activity(
                get_ui_base_url,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            pass

        changed_count = sum(
            1 for result in stage_input.successful_devices.values() if result.changed
        )
        unchanged_count = len(stage_input.successful_devices) - changed_count

        lines = [
            f"## Site backup results for {stage_input.site}",
            "",
            f"- **Total devices:** {stage_input.total_devices}",
            f"- **Successful:** {len(stage_input.successful_devices)}",
            f"- **Failed:** {len(stage_input.failed_devices)}",
            f"- **Changed:** {changed_count}",
            f"- **Unchanged:** {unchanged_count}",
            "",
        ]

        if stage_input.successful_devices:
            lines.append("### Successful backups")
            lines.append("")
            for device_name, result in sorted(stage_input.successful_devices.items()):
                status = "changed" if result.changed else "unchanged"
                workflow_ref = (
                    f"[workflow]({ui_base_url}/workflows/{result.child_workflow_id})"
                    if ui_base_url
                    else f"workflow `{result.child_workflow_id}`"
                )
                lines.append(f"- **{device_name}** ({status}) — {workflow_ref}")

        if stage_input.failed_devices:
            lines.append("")
            lines.append("### Failed backups")
            lines.append("")
            for device_name, result in sorted(stage_input.failed_devices.items()):
                workflow_ref = (
                    f"[workflow]({ui_base_url}/workflows/{result.child_workflow_id})"
                    if ui_base_url
                    else f"workflow `{result.child_workflow_id}`"
                )
                error = result.error or "unknown error"
                lines.append(f"- **{device_name}** — {error} — {workflow_ref}")

        return StageOutput(display="\n".join(lines))

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: SiteBackupInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Execute site configuration backup workflow."""
        if not workflow_input.user:
            raise ApplicationError("Missing user for backup attribution.")

        self.set_input(workflow_input)
        upsert_missing_search_attributes({SITE_SEARCH_ATTRIBUTE: [workflow_input.site]})

        devices_output = await self.get_devices(
            SiteBackupWorkflow.GetDevicesStageInput(
                site=workflow_input.site,
                roles=workflow_input.roles,
                tenant=workflow_input.tenant,
                status=workflow_input.status,
                backup_enabled_only=workflow_input.backup_enabled_only,
            )
        )

        if not devices_output.devices:
            self.set_stage_state("perform_backups", StateEnum.UNREACHABLE)
            self.set_stage_state("format_result", StateEnum.UNREACHABLE)
            await self.archive_results()
            return devices_output.display or "No devices found matching the specified filters."

        backups_output = await self.perform_backups(
            SiteBackupWorkflow.PerformBackupsStageInput(
                devices=devices_output.devices,
                user=workflow_input.user,
                user_domain=workflow_input.user_domain,
            )
        )

        format_output = await self.format_result(
            SiteBackupWorkflow.FormatResultStageInput(
                site=workflow_input.site,
                successful_devices=backups_output.successful_devices,
                failed_devices=backups_output.failed_devices,
                total_devices=len(devices_output.devices),
            )
        )

        await self.archive_results()
        return format_output.display
