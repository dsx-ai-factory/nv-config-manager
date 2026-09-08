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
"""Network Device Firmware Upgrade Workflow Definition."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin
    from nv_config_manager.temporal.ngc.activities.dcim import (
        CheckRecordedConfigDriftInput,
        GetNetworkDeviceInput,
        check_recorded_config_drift,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.os import (
        ExecuteZTPInput,
        GetOSImageVersionsInput,
        PollImageInput,
        PollZTPStatusInput,
        UpdateIntendedOSImageInput,
        execute_ztp,
        get_os_image_versions,
        poll_image,
        poll_ztp_status,
        update_intended_os_image,
    )
    from nv_config_manager.temporal.ngc.activities.render import (
        ValidateRenderedImageChangeInput,
        validate_rendered_image_change,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["FirmwareUpgradeException"],
)

SUPPORTED_PLATFORMS = [Platform.CUMULUS_LINUX, Platform.JUNIPER_JUNOS]


class SwitchOSUpgradeInput(BaseModel):
    """Firmware Upgrade Workflow Input Definition."""

    device_id: DeviceReference = Field(description="Identifier of the network switch to upgrade.")


@workflow.defn
class SwitchOSUpgradeWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network switch OS upgrade workflow for firmware management."""

    # Workflow metadata
    workflow_name = "Switch OS Upgrade"
    workflow_description = (
        "Upgrade network switch operating system with approval and validation workflow"
    )
    workflow_input_class = SwitchOSUpgradeInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/switch_os_upgrade"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="approve_upgrade",
            description="Review and approve firmware upgrade.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=[],
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device.",
            requires_approval=False,
            depends_on=["approve_upgrade"],
        )

        self.define_stage(
            name="update_device_configuration",
            description="Update device configuration for new firmware.",
            requires_approval=False,
            depends_on=["perform_backup"],
        )

        self.define_stage(
            name="perform_upgrade",
            description="Execute firmware upgrade and validate success.",
            requires_approval=False,
            depends_on=["update_device_configuration"],
        )

    class ApproveUpgradeStageInput(StageInput):
        """Approve Upgrade Stage Input."""

        device_id: str

    class ApproveUpgradeStageOutput(StageOutput):
        """Approve Upgrade Stage Output."""

        device_data: NetworkDeviceData
        supported: bool
        current_firmware: str
        desired_firmware: str
        diff: str
        approved: bool = False

    @stage_executor("approve_upgrade")
    async def approve_upgrade(
        self, stage_input: ApproveUpgradeStageInput
    ) -> ApproveUpgradeStageOutput:
        """Review and approve firmware upgrade."""
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Add device search attributes the first time we pull
        # them from the DCIM
        DeviceMixin.attach_device_search_attributes(result.device)

        device_data = result.device
        if device_data.platform not in SUPPORTED_PLATFORMS:
            return SwitchOSUpgradeWorkflow.ApproveUpgradeStageOutput(
                device_data=device_data,
                supported=False,
                current_firmware="",
                desired_firmware="",
                diff="",
                display="Switch OS Upgrade is not supported for this platform.",
            )

        firmware_versions = await workflow.execute_activity(
            get_os_image_versions,
            GetOSImageVersionsInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if firmware_versions.intended_firmware == firmware_versions.desired_firmware:
            # No upgrade needed
            self.get_stage_by_name("approve_upgrade").requires_approval = False
            return SwitchOSUpgradeWorkflow.ApproveUpgradeStageOutput(
                device_data=device_data,
                supported=True,
                current_firmware=firmware_versions.intended_firmware,
                desired_firmware=firmware_versions.desired_firmware,
                diff="",
                display="No firmware upgrade needed - versions match.",
            )

        # Move stage to approval state
        markdown = (
            f"Firmware Upgrade For Approval\n"
            f"Current: {firmware_versions.intended_firmware}\n"
            f"Desired: {firmware_versions.desired_firmware}"
        )
        output = SwitchOSUpgradeWorkflow.ApproveUpgradeStageOutput(
            current_firmware=firmware_versions.intended_firmware,
            desired_firmware=firmware_versions.desired_firmware,
            diff=f"Upgrade from {firmware_versions.intended_firmware} to {firmware_versions.desired_firmware}",
            display=markdown,
            device_data=device_data,
            supported=True,
        )
        self.set_stage_output("approve_upgrade", output)
        self.set_stage_state("approve_upgrade", StateEnum.PENDING_APPROVAL)
        await workflow.wait_condition(
            lambda: self.get_stage_state("approve_upgrade") != StateEnum.PENDING_APPROVAL
        )

        # Update output with approval status
        approved = self.get_stage_state("approve_upgrade") == StateEnum.APPROVED
        if approved:
            approval_state = "Approved"
            reviewers = [
                approver.user for approver in self.get_stage_by_name("approve_upgrade").approvers
            ]
        else:
            approval_state = "Rejected"
            reviewers = [
                rejecter.user for rejecter in self.get_stage_by_name("approve_upgrade").rejecters
            ]

        reviewmd = ",".join(reviewers)
        markdown = (
            f"Firmware Upgrade {approval_state} by {reviewmd}:\n"
            f"Current: {firmware_versions.intended_firmware}\n"
            f"Desired: {firmware_versions.desired_firmware}"
        )
        return SwitchOSUpgradeWorkflow.ApproveUpgradeStageOutput(
            current_firmware=firmware_versions.intended_firmware,
            desired_firmware=firmware_versions.desired_firmware,
            diff=f"Upgrade from {firmware_versions.intended_firmware} to {firmware_versions.desired_firmware}",
            display=markdown,
            device_data=device_data,
            supported=True,
            approved=approved,
        )

    class UpdateConfigStageInput(StageInput):
        """Update Configuration Stage Input."""

        device_data: NetworkDeviceData
        desired_firmware: str

    class UpdateConfigStageOutput(StageOutput):
        """Update Configuration Stage Output."""

    @stage_executor("update_device_configuration")
    async def update_device_configuration(
        self, stage_input: UpdateConfigStageInput
    ) -> UpdateConfigStageOutput:
        """Update device configuration for new firmware."""
        # Update the intended firmware version
        await workflow.execute_activity(
            update_intended_os_image,
            UpdateIntendedOSImageInput(
                device_id=stage_input.device_data.id,
                desired_firmware=stage_input.desired_firmware,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Validate the rendered image change
        await workflow.execute_activity(
            validate_rendered_image_change,
            ValidateRenderedImageChangeInput(
                device_data=stage_input.device_data,
                desired_image=stage_input.desired_firmware,
            ),
            start_to_close_timeout=timedelta(minutes=6),
            heartbeat_timeout=timedelta(minutes=2),  # Detect dead activities within 2 minutes
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        display = f"Successfully updated intended os image to {stage_input.desired_firmware}\n"
        display += f"Validated image change for device {stage_input.device_data.name}"

        return SwitchOSUpgradeWorkflow.UpdateConfigStageOutput(display=display)

    class BackupStageInput(StageInput):
        """Backup Stage Input."""

        device_id: str

    class BackupStageOutput(StageOutput):
        """Backup Stage Output."""

        config_drift: bool

    @stage_executor("perform_backup")
    async def perform_backup(self, stage_input: BackupStageInput) -> BackupStageOutput:
        """Perform a configuration backup."""
        backup_input = BackupInput(
            device_id=stage_input.device_id,
            trigger=TriggerEnum.WORKFLOW,
            user="nv-config-manager-temporal",
            user_domain=None,
            workflow_id=workflow.info().workflow_id,
            intended_config_commit_id="",
        )

        backup_handle = await workflow.start_child_workflow(
            BackupWorkflow.run, backup_input, run_timeout=timedelta(minutes=10)
        )
        self.append_child_workflow("perform_backup", backup_handle.id)
        await workflow.wait_condition(backup_handle.done)

        # Check if the backup workflow reported config drift to the DCIM
        config_drift = await workflow.execute_activity(
            check_recorded_config_drift,
            CheckRecordedConfigDriftInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        display = f"Configuration backup completed via workflow {backup_handle.id}."
        if config_drift:
            display += "\n\n**Configuration Drift Detected, Halting Workflow**\n\n"
        return SwitchOSUpgradeWorkflow.BackupStageOutput(
            display=display,
            config_drift=config_drift,
        )

    class PerformUpgradeStageInput(StageInput):
        """Perform Upgrade Stage Input."""

        device_data: NetworkDeviceData
        desired_firmware: str

    class PerformUpgradeStageOutput(StageOutput):
        """Perform Upgrade Stage Output."""

    @stage_executor("perform_upgrade")
    async def perform_upgrade(
        self, stage_input: PerformUpgradeStageInput
    ) -> PerformUpgradeStageOutput:
        """Execute firmware upgrade and validate success."""
        # 1. Trigger ZTP through factory reset
        await workflow.execute_activity(
            execute_ztp,
            ExecuteZTPInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # 2. Poll device until reachable and get running image
        image_result = await workflow.execute_activity(
            poll_image,
            PollImageInput(
                device_data=stage_input.device_data,
                expected_image=stage_input.desired_firmware,
            ),
            start_to_close_timeout=timedelta(minutes=35),  # 30 min + 5 min buffer
            heartbeat_timeout=timedelta(minutes=3),  # Detect dead activities within 3 minutes
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if image_result.running_image != stage_input.desired_firmware:
            raise ApplicationError(
                f"Running image {image_result.running_image} does not match desired "
                f"firmware {stage_input.desired_firmware}"
            )

        # 3. Poll ZTP status until success
        ztp_status_result = await workflow.execute_activity(
            poll_ztp_status,
            PollZTPStatusInput(device_data=stage_input.device_data),
            start_to_close_timeout=timedelta(minutes=15),  # 10 min + 5 min buffer
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not ztp_status_result.success:
            raise ApplicationError(
                "ZTP failed to complete within 10 minutes, check the device logs for details."
            )

        return SwitchOSUpgradeWorkflow.PerformUpgradeStageOutput(
            display=(
                f"Successfully upgraded device to {stage_input.desired_firmware}\n"
                f"ZTP completed successfully"
            )
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: SwitchOSUpgradeInput) -> bool:  # type: ignore[override, ty:invalid-method-override]
        """Execute firmware upgrade workflow."""
        self.set_input(workflow_input)

        approve_output = await self.approve_upgrade(
            SwitchOSUpgradeWorkflow.ApproveUpgradeStageInput(device_id=workflow_input.device_id)
        )

        if not approve_output.diff:
            # No firmware upgrade needed
            self.set_stage_state("update_device_configuration", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_upgrade", StateEnum.UNREACHABLE)
            return False

        if not approve_output.approved:
            # Upgrade was rejected
            self.set_stage_state("update_device_configuration", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_upgrade", StateEnum.UNREACHABLE)
            return False

        backup_output = await self.perform_backup(
            SwitchOSUpgradeWorkflow.BackupStageInput(device_id=workflow_input.device_id)
        )
        if backup_output.config_drift:
            self.set_stage_state("update_device_configuration", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_upgrade", StateEnum.UNREACHABLE)
            return False

        await self.update_device_configuration(
            SwitchOSUpgradeWorkflow.UpdateConfigStageInput(
                device_data=approve_output.device_data,
                desired_firmware=approve_output.desired_firmware,
            )
        )

        await self.perform_upgrade(
            SwitchOSUpgradeWorkflow.PerformUpgradeStageInput(
                device_data=approve_output.device_data,
                desired_firmware=approve_output.desired_firmware,
            )
        )

        await self.archive_results()
        return True
