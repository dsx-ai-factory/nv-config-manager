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
"""Multi-Device Configuration Deployment Workflow Definition."""

import asyncio
import hashlib
from datetime import timedelta
from typing import Any

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
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
    upsert_missing_search_attributes,
)
from nv_config_manager.temporal.common.workflow_references import OptionalLocationReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.config import build_workflow_url, get_ui_base_url
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDevicesInput,
        get_network_devices,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        ConfigApplyActivityInput,
        DiffActivityInput,
        apply_approved_configuration,
        load_intended_configuration,
        perform_candidate_diff,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )


CLONE_SEARCH_ATTRS = [
    USER_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
]
DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ConfigSyntaxException", "DiffChangedException"],
)


def _format_device_list(device_names: list[str]) -> str:
    """Format a compact device preview for a batch link."""
    device_list = ", ".join(device_names[:3])
    if len(device_names) > 3:
        device_list += f" +{len(device_names) - 3} more"
    return device_list


def _format_batch_status(result: dict[str, Any], device_count: int) -> tuple[str, str]:
    """Format the icon and compact terminal status for a batch result."""
    if "error" in result:
        return "❌", "Workflow failed"
    if not result.get("approved", False):
        return "⛔", "Rejected"

    successful = len(result.get("successful_devices") or [])
    failed = len(result.get("failed_devices") or {})
    backups = result.get("backups") or {}
    successful_backups = backups.get("successful", 0)
    failed_backups = backups.get("failed", 0)
    total_backups = backups.get("total", successful_backups + failed_backups)
    icon = "✅" if failed == 0 and failed_backups == 0 else "⚠️"

    backup_status = (
        f"Backups **{successful_backups}/{total_backups}**"
        if total_backups
        else "Backups **not run**"
    )
    return icon, f"Configured **{successful}/{device_count}** · {backup_status}"


def _format_backup_workflow_links(
    ui_base_url: str,
    backup_handles: dict[str, Any],
    backup_results: dict[str, Any],
) -> str:
    """Format backup child workflow links with their current status."""
    links = []
    for device_name, handle in backup_handles.items():
        result = backup_results.get(device_name)
        if result is None:
            icon, status = "⏳", "In progress"
        elif result.success:
            icon, status = "✅", "Successful"
        else:
            icon, status = "❌", "Failed"

        workflow_url = build_workflow_url(ui_base_url, handle.id)
        links.append(f"- {icon} [{device_name} backup]({workflow_url}) — {status}")
    return "\n".join(links) or "*No backup workflows were started.*"


class MultiDeployInput(BaseModel):
    """Multi-Deploy Workflow Input Definition."""

    role: str = Field(description="Device role used to select network devices for deployment.")
    max_batch_size: int = Field(
        default=10, description="Maximum number of devices included in each deployment batch."
    )
    location: OptionalLocationReference = Field(
        default=None, description="Location used to filter the selected network devices."
    )
    status: list[str] | None = Field(
        default=None, description="Device statuses used to filter the selected network devices."
    )
    tenant: str | None = Field(
        default=None, description="Tenant used to filter the selected network devices."
    )
    commit_confirm: bool = Field(
        default=True,
        description="Whether to use commit-confirmed mode when the platform supports it.",
    )


class DeviceDiffData(BaseModel):
    """Data structure for device diff information."""

    device: NetworkDeviceData
    diff: str | None = None
    intended_config: str | None = None
    commit_id: str | None = None
    error: str | None = None


class DiffGroup(BaseModel):
    """A group of devices with identical diffs.

    Note: Each device has its own intended config file and commit SHA.
    Grouping is only based on identical diff content, not commit IDs.
    """

    diff_hash: str
    diff_content: str
    devices: list[DeviceDiffData]


class BatchDeployInput(BaseModel):
    """Input for batch deploy child workflow."""

    diff_group: DiffGroup = Field(description="Shared configuration diff for the device batch.")
    batch_devices: list[DeviceDiffData] | None = Field(
        default=None,
        json_schema_extra={"deprecated": True},
        description=(
            "Deprecated compatibility field. When omitted, devices are read from "
            "diff_group.devices."
        ),
    )
    parent_workflow_id: str = Field(description="Identifier of the parent multi-deploy workflow.")
    batch_number: int | None = Field(
        default=None, description="Sequence number of this batch within the parent workflow."
    )
    commit_confirm: bool = Field(
        default=True,
        description="Whether to use commit-confirmed mode when the platform supports it.",
    )

    def resolved_batch_devices(self) -> list[DeviceDiffData]:
        """Return the legacy device field when present, otherwise the canonical group devices."""
        # Legacy inputs supplied both collections and the child historically used
        # batch_devices, so it must retain precedence when the collections differ.
        return self.batch_devices if self.batch_devices is not None else self.diff_group.devices


class BatchBackupResultData(BaseModel):
    """Completion data for one backup child workflow in a deployment batch."""

    success: bool
    changed: bool | None = None
    error: str | None = None
    child_workflow_id: str


@workflow.defn
class BatchDeployWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Batch configuration deployment workflow for multiple devices."""

    # Workflow metadata
    workflow_name = "Batch Configuration Deploy"
    workflow_description = "Deploy configurations to a batch of devices with shared diff content"
    workflow_input_class = BatchDeployInput
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="review_shared_diff",
            description="Review the shared configuration diff for this batch.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=[],
        )

        self.define_stage(
            name="apply_configurations",
            description="Apply configurations to all devices in the batch.",
            requires_approval=False,
            depends_on=["review_shared_diff"],
        )

        self.define_stage(
            name="perform_backups",
            description="Perform backups for all devices in the batch.",
            requires_approval=False,
            depends_on=["apply_configurations"],
        )

    class ReviewDiffStageInput(StageInput):
        """Review Diff Stage Input."""

        diff_group: DiffGroup = Field(exclude=True)
        device_count: int
        batch_devices: list[DeviceDiffData] = Field(exclude=True)
        batch_number: int | None = None

    class ReviewDiffStageOutput(StageOutput):
        """Review Diff Stage Output."""

        approved: bool

    @stage_executor("review_shared_diff")
    async def review_shared_diff(self, stage_input: ReviewDiffStageInput) -> ReviewDiffStageOutput:
        """Review the shared diff for approval."""
        stage_name = "review_shared_diff"
        device_names = [device.device.name for device in stage_input.batch_devices]
        device_list = "\n".join([f"- {name}" for name in device_names])

        if stage_input.batch_number:
            markdown = f"**Batch {stage_input.batch_number} - Shared Configuration Diff for {stage_input.device_count} devices**\n\n"
        else:
            markdown = f"**Shared Configuration Diff for {stage_input.device_count} devices**\n\n"

        markdown += (
            f"**Devices in this batch:**\n{device_list}\n\n"
            f"**Configuration Changes:**\n```\n{stage_input.diff_group.diff_content}\n```"
        )

        output = BatchDeployWorkflow.ReviewDiffStageOutput(approved=False, display=markdown)
        self.set_stage_output(stage_name, output)
        self.set_stage_state(stage_name, StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state(stage_name) != StateEnum.PENDING_APPROVAL
        )

        # Update output with approval status
        approved = self.get_stage_state(stage_name) == StateEnum.APPROVED
        if approved:
            approval_state = "Approved"
            reviewers = [approver.user for approver in self.get_stage_by_name(stage_name).approvers]
        else:
            approval_state = "Rejected"
            reviewers = [rejecter.user for rejecter in self.get_stage_by_name(stage_name).rejecters]

        reviewmd = ",".join(reviewers)

        # Create bulleted device list for approval result
        device_names = [device.device.name for device in stage_input.batch_devices]
        device_list = "\n".join([f"- {name}" for name in device_names])

        if stage_input.batch_number:
            markdown = f"**Batch {stage_input.batch_number} - Shared Configuration Diff {approval_state} by {reviewmd}**\n\n"
        else:
            markdown = f"**Shared Configuration Diff {approval_state} by {reviewmd}**\n\n"
        markdown += (
            f"**Devices in this batch ({stage_input.device_count}):**\n{device_list}\n\n"
            f"**Configuration Changes:**\n```\n{stage_input.diff_group.diff_content}\n```"
        )
        return BatchDeployWorkflow.ReviewDiffStageOutput(approved=approved, display=markdown)

    class ApplyConfigsStageInput(StageInput):
        """Apply Configs Stage Input."""

        batch_devices: list[DeviceDiffData] = Field(exclude=True)
        approved_diff: str
        commit_confirm: bool = True

    class ApplyConfigsStageOutput(StageOutput):
        """Apply Configs Stage Output."""

        successful_devices: list[str]
        failed_devices: dict[str, str]

    @stage_executor("apply_configurations")
    async def apply_configurations(
        self, stage_input: ApplyConfigsStageInput
    ) -> ApplyConfigsStageOutput:
        """Apply configurations to all devices in the batch."""
        successful_devices = []
        failed_devices = {}

        # Apply configurations in parallel
        async def apply_to_device(
            device_data: DeviceDiffData,
        ) -> tuple[str, str | None]:
            try:
                # Ensure we have the intended config before applying
                if device_data.intended_config is None:
                    return (
                        device_data.device.name,
                        "No intended configuration available",
                    )

                await workflow.execute_activity(
                    apply_approved_configuration,
                    ConfigApplyActivityInput(
                        device_data=device_data.device,
                        configuration=device_data.intended_config,
                        approved_diff=stage_input.approved_diff,
                        commit_confirm=stage_input.commit_confirm,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                return device_data.device.name, None
            except Exception as e:
                return device_data.device.name, str(e)

        # Execute all applications in parallel
        results = await asyncio.gather(
            *[apply_to_device(device) for device in stage_input.batch_devices],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, tuple):
                device_name, error = result
                if error:
                    failed_devices[device_name] = error
                else:
                    successful_devices.append(device_name)
            else:
                # This shouldn't happen with return_exceptions=True, but handle it
                failed_devices["unknown"] = str(result)

        success_count = len(successful_devices)
        fail_count = len(failed_devices)
        display = f"Applied configurations to {success_count} devices successfully."
        if fail_count > 0:
            display += f" {fail_count} devices failed."

        return BatchDeployWorkflow.ApplyConfigsStageOutput(
            successful_devices=successful_devices,
            failed_devices=failed_devices,
            display=display,
        )

    class BackupsStageInput(StageInput):
        """Backups Stage Input."""

        successful_devices: list[DeviceDiffData] = Field(exclude=True)

    class BackupsStageOutput(StageOutput):
        """Backups Stage Output."""

        backup_results: dict[str, BatchBackupResultData]
        successful_backups: int
        failed_backups: int

    @stage_executor("perform_backups")
    async def perform_backups(self, stage_input: BackupsStageInput) -> BackupsStageOutput:
        """Perform backups for all successfully configured devices."""
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        backup_handles: dict[str, Any] = {}

        for device_data in stage_input.successful_devices:
            backup_input = BackupInput(
                device_id=device_data.device.id,
                trigger=TriggerEnum.WORKFLOW,
                user="nv-config-manager-temporal",
                user_domain=None,
                intended_config_commit_id=device_data.commit_id,
                workflow_id=workflow.info().workflow_id,
            )

            backup_handle = await workflow.start_child_workflow(
                BackupWorkflow.run, backup_input, run_timeout=timedelta(minutes=10)
            )
            self.append_child_workflow("perform_backups", backup_handle.id)
            backup_handles[device_data.device.name] = backup_handle

        backup_results: dict[str, BatchBackupResultData] = {}
        remaining_items = list(backup_handles.items())
        total_backups = len(backup_handles)
        initial_links = _format_backup_workflow_links(ui_base_url, backup_handles, backup_results)
        self.set_stage_output(
            "perform_backups",
            BatchDeployWorkflow.BackupsStageOutput(
                backup_results={},
                successful_backups=0,
                failed_backups=0,
                display=(
                    f"**Backups in progress** — 0/{total_backups} complete\n\n"
                    f"**Devices:** 0 successful · 0 failed · {total_backups} in progress\n\n"
                    f"**Backup workflows**\n{initial_links}"
                ),
            ),
        )

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

                try:
                    changed = completed_handle.result()
                    backup_results[device_name] = BatchBackupResultData(
                        success=True,
                        changed=changed,
                        child_workflow_id=completed_handle.id,
                    )
                except ChildWorkflowError as exc:
                    backup_results[device_name] = BatchBackupResultData(
                        success=False,
                        error=str(exc.cause),
                        child_workflow_id=completed_handle.id,
                    )

            successful_backups = sum(result.success for result in backup_results.values())
            failed_backups = len(backup_results) - successful_backups
            completed_backups = len(backup_results)
            backup_links = _format_backup_workflow_links(
                ui_base_url, backup_handles, backup_results
            )
            self.set_stage_output(
                "perform_backups",
                BatchDeployWorkflow.BackupsStageOutput(
                    backup_results=backup_results.copy(),
                    successful_backups=successful_backups,
                    failed_backups=failed_backups,
                    display=(
                        f"**Backups in progress** — {completed_backups}/{total_backups} complete\n\n"
                        f"**Devices:** {successful_backups} successful · {failed_backups} failed · "
                        f"{total_backups - completed_backups} in progress\n\n"
                        f"**Backup workflows**\n{backup_links}"
                    ),
                ),
            )

        successful_backups = sum(result.success for result in backup_results.values())
        failed_backups = len(backup_results) - successful_backups
        backup_links = _format_backup_workflow_links(ui_base_url, backup_handles, backup_results)

        return BatchDeployWorkflow.BackupsStageOutput(
            backup_results=backup_results,
            successful_backups=successful_backups,
            failed_backups=failed_backups,
            display=(
                f"**Backups complete**\n\n"
                f"**Devices:** {successful_backups} successful · {failed_backups} failed\n\n"
                f"**Backup workflows**\n{backup_links}"
            ),
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: BatchDeployInput) -> dict[str, Any]:  # type: ignore[override, ty:invalid-method-override]
        """Execute batch deploy workflow."""
        self.set_input(workflow_input)
        batch_devices = workflow_input.resolved_batch_devices()

        # Review the shared diff
        review_output = await self.review_shared_diff(
            BatchDeployWorkflow.ReviewDiffStageInput(
                diff_group=workflow_input.diff_group,
                device_count=len(batch_devices),
                batch_devices=batch_devices,
                batch_number=workflow_input.batch_number,
            )
        )

        result = {
            "approved": review_output.approved,
            "successful_devices": [],
            "failed_devices": {},
            "backups": {
                "total": 0,
                "successful": 0,
                "failed": 0,
                "results": {},
            },
        }

        if review_output.approved:
            # Apply configurations
            apply_output = await self.apply_configurations(
                BatchDeployWorkflow.ApplyConfigsStageInput(
                    batch_devices=batch_devices,
                    approved_diff=workflow_input.diff_group.diff_content,
                    commit_confirm=workflow_input.commit_confirm,
                )
            )

            result["successful_devices"] = apply_output.successful_devices
            result["failed_devices"] = apply_output.failed_devices

            # Perform backups for successful devices
            successful_device_data = [
                device
                for device in batch_devices
                if device.device.name in apply_output.successful_devices
            ]

            backups_output = await self.perform_backups(
                BatchDeployWorkflow.BackupsStageInput(
                    successful_devices=successful_device_data,
                )
            )
            result["backups"] = {
                "total": len(backups_output.backup_results),
                "successful": backups_output.successful_backups,
                "failed": backups_output.failed_backups,
                "results": {
                    device_name: backup_result.model_dump()
                    for device_name, backup_result in backups_output.backup_results.items()
                },
            }
        else:
            # Mark stages as unreachable
            self.set_stage_state("apply_configurations", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backups", StateEnum.UNREACHABLE)

        await self.archive_results()
        return result


@workflow.defn
class MultiDeployWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Multi-device configuration deployment orchestration workflow."""

    # Workflow metadata
    workflow_name = "Multi-Configuration Deploy"
    workflow_description = (
        "Deploy configurations to multiple devices by role with batching and approval workflow"
    )
    workflow_input_class = MultiDeployInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/multi_deploy"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="discover_devices",
            description="Discover devices by role from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="collect_diffs",
            description="Collect configuration diffs from all devices.",
            requires_approval=False,
            depends_on=["discover_devices"],
        )

        self.define_stage(
            name="group_and_batch",
            description="Group devices by shared diffs and create batches.",
            requires_approval=False,
            depends_on=["collect_diffs"],
        )

        self.define_stage(
            name="execute_batches",
            description="Execute batch deployments via child workflows.",
            requires_approval=False,
            depends_on=["group_and_batch"],
        )

    class DiscoverDevicesStageInput(StageInput):
        """Discover Devices Stage Input."""

        role: str
        location: str | None = None
        status: list[str] | None = None
        tenant: str | None = None

    class DiscoverDevicesStageOutput(StageOutput):
        """Discover Devices Stage Output."""

        devices: list[NetworkDeviceData] = Field(exclude=True)

    @stage_executor("discover_devices")
    async def discover_devices(
        self, stage_input: DiscoverDevicesStageInput
    ) -> DiscoverDevicesStageOutput:
        """Discover devices by role from the DCIM."""
        result = await workflow.execute_activity(
            get_network_devices,
            GetNetworkDevicesInput(
                site=stage_input.location,
                roles=[stage_input.role],
                status=stage_input.status,
                tenant=stage_input.tenant,
                deploy_enabled=True,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return MultiDeployWorkflow.DiscoverDevicesStageOutput(
            devices=result.devices,
            display=f"Discovered {len(result.devices)} devices with role '{stage_input.role}'.",
        )

    class CollectDiffsStageInput(StageInput):
        """Collect Diffs Stage Input."""

        devices: list[NetworkDeviceData] = Field(exclude=True)

    class CollectDiffsStageOutput(StageOutput):
        """Collect Diffs Stage Output."""

        device_diffs: list[DeviceDiffData] = Field(exclude=True)
        failed_devices: dict[str, str]

    @stage_executor("collect_diffs")
    async def collect_diffs(self, stage_input: CollectDiffsStageInput) -> CollectDiffsStageOutput:
        """Collect configuration diffs from all devices."""

        async def get_device_diff(device: NetworkDeviceData) -> DeviceDiffData:
            """Get diff for a single device."""
            try:
                # Load intended configuration
                content, commit_id, url = await workflow.execute_activity(
                    load_intended_configuration,
                    device,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )

                # Get the diff
                diff = await workflow.execute_activity(
                    perform_candidate_diff,
                    DiffActivityInput(device_data=device, configuration=content),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )

                return DeviceDiffData(
                    device=device,
                    diff=diff.strip() if diff.strip() else None,
                    intended_config=content,
                    commit_id=commit_id,
                )

            except Exception as e:
                return DeviceDiffData(
                    device=device,
                    error=str(e),
                )

        # Collect diffs in parallel
        device_diffs = await asyncio.gather(
            *[get_device_diff(device) for device in stage_input.devices],
            return_exceptions=True,
        )

        # Process results
        valid_diffs = []
        failed_devices = {}

        for result in device_diffs:
            if isinstance(result, DeviceDiffData):
                if result.error:
                    failed_devices[result.device.name] = result.error
                else:
                    valid_diffs.append(result)
            else:
                failed_devices["unknown"] = str(result)

        # Filter out devices with no diff
        devices_with_diffs = [dd for dd in valid_diffs if dd.diff]
        no_diff_count = len(valid_diffs) - len(devices_with_diffs)

        display_parts = [
            f"Collected diffs from {len(stage_input.devices)} devices.",
            f"- {len(devices_with_diffs)} devices have configuration changes",
            f"- {no_diff_count} devices have no changes",
            f"- {len(failed_devices)} devices failed",
        ]

        return MultiDeployWorkflow.CollectDiffsStageOutput(
            device_diffs=devices_with_diffs,
            failed_devices=failed_devices,
            display="\n".join(display_parts),
        )

    class GroupAndBatchStageInput(StageInput):
        """Group and Batch Stage Input."""

        device_diffs: list[DeviceDiffData] = Field(exclude=True)
        max_batch_size: int

    class GroupAndBatchStageOutput(StageOutput):
        """Group and Batch Stage Output."""

        batches: list[list[DeviceDiffData]] = Field(exclude=True)
        diff_groups: list[DiffGroup] = Field(exclude=True)

    @stage_executor("group_and_batch")
    async def group_and_batch(
        self, stage_input: GroupAndBatchStageInput
    ) -> GroupAndBatchStageOutput:
        """Group devices by shared diffs and create batches."""
        # Group devices by diff content
        diff_groups_dict: dict[str, DiffGroup] = {}

        for device_diff in stage_input.device_diffs:
            if device_diff.diff:
                # Create a hash of the diff content for grouping
                diff_hash = hashlib.sha256(device_diff.diff.encode()).hexdigest()[:16]

                if diff_hash not in diff_groups_dict:
                    diff_groups_dict[diff_hash] = DiffGroup(
                        diff_hash=diff_hash,
                        diff_content=device_diff.diff,
                        devices=[],
                    )

                diff_groups_dict[diff_hash].devices.append(device_diff)

        diff_groups = list(diff_groups_dict.values())

        # Create batches from groups
        all_batches = []
        for diff_group in diff_groups:
            # Split large groups into batches
            devices = diff_group.devices
            for i in range(0, len(devices), stage_input.max_batch_size):
                batch = devices[i : i + stage_input.max_batch_size]
                all_batches.append(batch)

        group_summary = []
        for diff_group in diff_groups:
            device_count = len(diff_group.devices)
            batch_count = (
                device_count + stage_input.max_batch_size - 1
            ) // stage_input.max_batch_size
            group_summary.append(
                f"  - {device_count} devices with diff {diff_group.diff_hash} ({batch_count} batches)"
            )

        display = (
            f"Created {len(diff_groups)} diff groups with {len(all_batches)} total batches:\n"
            + "\n".join(group_summary)
        )

        return MultiDeployWorkflow.GroupAndBatchStageOutput(
            batches=all_batches,
            diff_groups=diff_groups,
            display=display,
        )

    class ExecuteBatchesStageInput(StageInput):
        """Execute Batches Stage Input."""

        batches: list[list[DeviceDiffData]] = Field(exclude=True)
        diff_groups: list[DiffGroup] = Field(exclude=True)
        commit_confirm: bool = True

    class ExecuteBatchesStageOutput(StageOutput):
        """Execute Batches Stage Output."""

        batch_results: dict[str, dict[str, Any]]
        total_successful: int
        total_failed: int
        total_rejected: int

    @stage_executor("execute_batches")
    async def execute_batches(
        self, stage_input: ExecuteBatchesStageInput
    ) -> ExecuteBatchesStageOutput:
        """Execute batch deployments via child workflows."""
        # Get UI base URL from configuration via activity
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Create a mapping from diff_hash to DiffGroup for easy lookup
        diff_group_map = {dg.diff_hash: dg for dg in stage_input.diff_groups}

        batch_handles = {}

        # Search Attributes to clone from parent to child
        search_attrs = {
            k: v for k, v in workflow.info().search_attributes.items() if k in CLONE_SEARCH_ATTRS
        }

        for i, batch in enumerate(stage_input.batches):
            if batch:
                # Find the diff group for this batch
                # Use the first device's diff to identify the group
                first_device_diff = batch[0].diff
                if first_device_diff is None:
                    continue  # Skip batches with no diff
                diff_hash = hashlib.sha256(first_device_diff.encode()).hexdigest()[:16]
                diff_group = diff_group_map[diff_hash]

                # Carry only this batch in the canonical device collection. Omitting
                # deprecated batch_devices prevents serializing the devices twice.
                batch_input = BatchDeployInput(
                    diff_group=diff_group.model_copy(update={"devices": batch}),
                    parent_workflow_id=workflow.info().workflow_id,
                    batch_number=i + 1,  # 1-indexed batch number for display
                    commit_confirm=stage_input.commit_confirm,
                )

                handle = await workflow.start_child_workflow(
                    BatchDeployWorkflow.run,
                    batch_input,
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    search_attributes=search_attrs,
                )

                batch_id = f"batch_{i}_{diff_hash}"
                batch_handles[batch_id] = handle
                self.append_child_workflow("execute_batches", handle.id)

        # Create immediate status update with child workflow links for navigation
        child_workflow_links = []
        for batch_id, handle in batch_handles.items():
            batch_index = int(batch_id.split("_")[1])
            device_names = [d.device.name for d in stage_input.batches[batch_index]]
            device_list = _format_device_list(device_names)

            child_workflow_url = build_workflow_url(ui_base_url, handle.id)

            child_workflow_links.append(
                f"- ⏳ [Batch {batch_index + 1}]({child_workflow_url}) — {device_list}"
            )

        # Update the stage output with links while workflows are running
        batch_label = "batch" if len(batch_handles) == 1 else "batches"
        initial_display = (
            f"**{len(batch_handles)} {batch_label} awaiting approval**\n\n"
            + "\n".join(child_workflow_links)
            + "\n\n*Select a batch to review and approve its shared diff.*"
        )

        initial_output = MultiDeployWorkflow.ExecuteBatchesStageOutput(
            batch_results={},
            total_successful=0,
            total_failed=0,
            total_rejected=0,
            display=initial_display,
        )
        self.set_stage_output("execute_batches", initial_output)

        # Helper function to generate batch status display
        def generate_batch_links(
            batch_results: dict[str, Any], completed_batches: set[str] | None = None
        ) -> list[str]:
            """Generate child workflow links with current status."""
            batch_links = []

            for batch_id, handle in batch_handles.items():
                batch_index = int(batch_id.split("_")[1])
                device_count = len(stage_input.batches[batch_index])
                device_names = [d.device.name for d in stage_input.batches[batch_index]]
                device_list = _format_device_list(device_names)

                child_workflow_url = build_workflow_url(ui_base_url, handle.id)

                # Determine batch status
                if batch_id in batch_results:
                    result = batch_results[batch_id]
                    if isinstance(result, dict):
                        icon, status = _format_batch_status(result, device_count)
                    else:
                        icon, status = "❓", "Unknown result"
                elif completed_batches and batch_id in completed_batches:
                    icon, status = "⏳", "Processing result"
                else:
                    icon, status = "⏳", "Awaiting approval"

                batch_links.append(
                    f"- {icon} [Batch {batch_index + 1}]({child_workflow_url}) — {device_list} · {status}"
                )

            return batch_links

        # Wait for batches to complete one by one and update status incrementally
        batch_results: dict[str, dict[str, Any]] = {}
        total_successful = 0
        total_failed = 0
        total_rejected = 0
        completed_batches: set[str] = set()

        # Use asyncio.gather to wait for all batches, but process them individually
        # as they complete by using asyncio.wait with return_when=asyncio.FIRST_COMPLETED
        remaining_batch_items = list(batch_handles.items())

        while remaining_batch_items:
            # Extract handles from remaining items
            remaining_handles = [handle for _, handle in remaining_batch_items]

            # Wait for at least one to complete
            done, pending = await asyncio.wait(
                remaining_handles, return_when=asyncio.FIRST_COMPLETED
            )

            # Process completed handles
            for completed_handle in done:
                # Find the batch_id for this handle
                completed_batch_id = None
                for i, (batch_id, handle) in enumerate(remaining_batch_items):
                    if handle is completed_handle:
                        completed_batch_id = batch_id
                        remaining_batch_items.pop(i)
                        break

                if completed_batch_id is None:
                    continue  # Shouldn't happen

                completed_batches.add(completed_batch_id)

                try:
                    # Get the result from the completed handle
                    result = completed_handle.result()

                    # Store the result and update counters
                    batch_results[completed_batch_id] = result

                    # Ensure result has expected structure
                    if isinstance(result, dict):
                        if result.get("approved", False):
                            successful_devices = result.get("successful_devices", [])
                            failed_devices = result.get("failed_devices", {})
                            total_successful += len(successful_devices) if successful_devices else 0
                            total_failed += len(failed_devices) if failed_devices else 0
                        else:
                            # Batch was rejected
                            batch_index = int(completed_batch_id.split("_")[1])
                            total_rejected += len(stage_input.batches[batch_index])
                    else:
                        # Unexpected result structure, treat as failed
                        batch_index = int(completed_batch_id.split("_")[1])
                        total_failed += len(stage_input.batches[batch_index])

                except ChildWorkflowError as e:
                    # Handle workflow failure
                    batch_results[completed_batch_id] = {"error": str(e.cause)}
                    total_failed += len(stage_input.batches[int(completed_batch_id.split("_")[1])])

            # Update the display with current progress after each batch completion
            completed_count = len(batch_results)
            total_count = len(batch_handles)

            batch_links = generate_batch_links(batch_results, completed_batches)

            if completed_count < total_count:
                # Still in progress
                batch_label = "batch" if total_count == 1 else "batches"
                display = (
                    f"**Deployment in progress** — {completed_count}/{total_count} "
                    f"{batch_label} complete\n\n"
                    f"**Devices:** {total_successful} configured · {total_failed} failed · "
                    f"{total_rejected} rejected\n\n"
                    f"**Batches**\n" + "\n".join(batch_links)
                )
            else:
                # All completed
                display = (
                    f"**Deployment complete**\n\n"
                    f"**Devices:** {total_successful} configured · {total_failed} failed · "
                    f"{total_rejected} rejected\n\n"
                    f"**Batches**\n" + "\n".join(batch_links)
                )

            # Update the stage output with current progress
            updated_output = MultiDeployWorkflow.ExecuteBatchesStageOutput(
                batch_results=batch_results.copy(),
                total_successful=total_successful,
                total_failed=total_failed,
                total_rejected=total_rejected,
                display=display,
            )
            self.set_stage_output("execute_batches", updated_output)

        # Final display is already set in the loop above, but we'll generate it one more time
        # to ensure consistency
        final_batch_links = generate_batch_links(batch_results)
        display = (
            f"**Deployment complete**\n\n"
            f"**Devices:** {total_successful} configured · {total_failed} failed · "
            f"{total_rejected} rejected\n\n"
            f"**Batches**\n" + "\n".join(final_batch_links)
        )

        return MultiDeployWorkflow.ExecuteBatchesStageOutput(
            batch_results=batch_results,
            total_successful=total_successful,
            total_failed=total_failed,
            total_rejected=total_rejected,
            display=display,
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: MultiDeployInput) -> dict[str, Any]:  # type: ignore[override, ty:invalid-method-override]
        """Execute multi-deploy workflow."""
        self.set_input(workflow_input)
        if workflow_input.location:
            upsert_missing_search_attributes({SITE_SEARCH_ATTRIBUTE: [workflow_input.location]})

        # Discover devices
        discover_output = await self.discover_devices(
            MultiDeployWorkflow.DiscoverDevicesStageInput(
                role=workflow_input.role,
                location=workflow_input.location,
                status=workflow_input.status,
                tenant=workflow_input.tenant,
            )
        )

        if not discover_output.devices:
            return {
                "total_devices": 0,
                "successful_devices": 0,
                "failed_devices": 0,
                "rejected_devices": 0,
                "total_backups": 0,
                "successful_backups": 0,
                "failed_backups": 0,
                "discovery_failures": {},
                "message": f"No devices found with role '{workflow_input.role}'",
            }

        # Collect diffs
        diffs_output = await self.collect_diffs(
            MultiDeployWorkflow.CollectDiffsStageInput(devices=discover_output.devices)
        )

        if not diffs_output.device_diffs:
            return {
                "total_devices": len(discover_output.devices),
                "successful_devices": 0,
                "failed_devices": 0,
                "rejected_devices": 0,
                "total_backups": 0,
                "successful_backups": 0,
                "failed_backups": 0,
                "discovery_failures": diffs_output.failed_devices,
                "message": "No devices have configuration changes to deploy",
            }

        # Group and batch
        batch_output = await self.group_and_batch(
            MultiDeployWorkflow.GroupAndBatchStageInput(
                device_diffs=diffs_output.device_diffs,
                max_batch_size=workflow_input.max_batch_size,
            )
        )

        # Execute batches
        execute_output = await self.execute_batches(
            MultiDeployWorkflow.ExecuteBatchesStageInput(
                batches=batch_output.batches,
                diff_groups=batch_output.diff_groups,
                commit_confirm=workflow_input.commit_confirm,
            )
        )

        await self.archive_results()

        backup_summaries = [
            batch_result.get("backups", {})
            for batch_result in execute_output.batch_results.values()
            if "error" not in batch_result
        ]

        return {
            "total_devices": len(discover_output.devices),
            "successful_devices": execute_output.total_successful,
            "failed_devices": execute_output.total_failed + len(diffs_output.failed_devices),
            "rejected_devices": execute_output.total_rejected,
            "total_backups": sum(summary.get("total", 0) for summary in backup_summaries),
            "successful_backups": sum(summary.get("successful", 0) for summary in backup_summaries),
            "failed_backups": sum(summary.get("failed", 0) for summary in backup_summaries),
            "discovery_failures": diffs_output.failed_devices,
            "batch_results": execute_output.batch_results,
            "diff_groups": len(batch_output.diff_groups),
            "total_batches": len(batch_output.batches),
        }
