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
"""Device Password Rotation Workflow Definition."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
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
    from nv_config_manager.temporal.client.device import DiffValidationError
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        ConfigApplyActivityInput,
        DiffActivityInput,
        apply_approved_configuration,
        load_intended_configuration,
        perform_candidate_diff,
    )
    from nv_config_manager.temporal.ngc.activities.device_password_rotation import (
        GetPasswordMappingsInput,
        ValidatePasswordDiffInput,
        ValidatePasswordDiffOutput,
        ValidatePlatformSupportInput,
        get_password_mappings,
        validate_password_diff,
        validate_platform_support,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )


DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ConfigSyntaxException", "DiffChangedException"],
)


class DevicePasswordRotationInput(BaseModel):
    """Device Password Rotation Workflow Input Definition."""

    device_id: DeviceReference = Field(description="Identifier of the network device to update.")
    selected_secret: str = Field(
        description="Name of the managed secret containing the replacement password."
    )


@workflow.defn
class DevicePasswordRotationWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network device password rotation workflow for security management."""

    # Workflow metadata
    workflow_name = "Device Password Rotation"
    workflow_description = (
        "Rotate passwords on network devices with validation and approval workflow"
    )
    workflow_input_class = DevicePasswordRotationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/device_password_rotation"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="load_intended_configuration",
            description="Load the latest intended configuration and validate password mappings.",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="perform_configuration_diff",
            description="Validate and auto-approve password only changes.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["load_intended_configuration"],
        )

        self.define_stage(
            name="apply_configuration",
            description="Apply the approved configuration to the device.",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device.",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

    class LoadConfigStageInput(StageInput):
        """Load Intended Config Stage Input."""

        device_id: str
        selected_secret: str

    class LoadConfigStageOutput(StageOutput):
        """Load Intended Config Stage Output."""

        intended_config: str
        commit_id: str

    @stage_executor("load_intended_configuration")
    async def load_intended_configuration(
        self, stage_input: LoadConfigStageInput
    ) -> LoadConfigStageOutput:
        """Load the intended configuration and validate password mappings."""
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        # Add device search attributes the first time we pull them from the DCIM
        DeviceMixin.attach_device_search_attributes(result.device)

        # Load the intended configuration
        content, commit_id, url = await workflow.execute_activity(
            load_intended_configuration,
            result.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        config_path = result.device.intended_config_path
        markdown = f"Loaded intended configuration from [{config_path}]({url})."
        return DevicePasswordRotationWorkflow.LoadConfigStageOutput(
            intended_config=content, commit_id=commit_id, display=markdown
        )

    class PerformDiffStageInput(StageInput):
        """Diff Stage Input."""

        device_id: str
        intended_config: str
        username: str

    class PerformDiffStageOutput(StageOutput):
        """Diff Stage Output."""

        approved: bool
        diff: str

    @stage_executor("perform_configuration_diff")
    async def perform_configuration_diff(
        self, stage_input: PerformDiffStageInput
    ) -> PerformDiffStageOutput:
        """Execute the configuration diff against the device."""
        # Loading this again to make this step retryable if a device has a bad IP in NB.
        stage_name = "perform_configuration_diff"
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Validate platform is supported for parsing
        platform_check = await workflow.execute_activity(
            validate_platform_support,
            ValidatePlatformSupportInput(platform=result.device.platform),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Validate password mapping configuration before proceeding
        await workflow.execute_activity(
            get_password_mappings,
            GetPasswordMappingsInput(
                device=result.device,
                username=stage_input.username,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Perform the diff
        diff = await workflow.execute_activity(
            perform_candidate_diff,
            DiffActivityInput(device_data=result.device, configuration=stage_input.intended_config),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not diff.strip():
            # No diff, end the workflow
            self.get_stage_by_name(stage_name).requires_approval = False
            return DevicePasswordRotationWorkflow.PerformDiffStageOutput(
                approved=False, diff="", display="No password changes needed, no diff."
            )

        # Validate the diff to see if it only contains password changes for the target user
        diff_validation: ValidatePasswordDiffOutput = await workflow.execute_activity(
            validate_password_diff,
            ValidatePasswordDiffInput(
                diff=diff,
                username=stage_input.username,
                platform=platform_check.normalized_platform,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not diff_validation.is_valid:
            invalid_diff_content = (
                "\n".join(diff_validation.invalid_lines) if diff_validation.invalid_lines else diff
            )

            error_message = f"Password rotation diff validation failed for user '{stage_input.username}' on device {result.device.name}. "
            error_message += (
                f"{diff_validation.error_message or 'Diff contains non-password changes.'}\n\n"
            )
            error_message += f"Diff:\n{diff}"

            raise DiffValidationError(
                message=error_message,
                invalid_diff=invalid_diff_content,
                device_name=result.device.name,
                username=stage_input.username,
            )

        self.get_stage_by_name(stage_name).requires_approval = False
        markdown = f"Password Rotation Configuration Diff (Auto-Approved password only changes for '{stage_input.username}'):\n```\n{diff}\n```"
        return DevicePasswordRotationWorkflow.PerformDiffStageOutput(
            approved=True, diff=diff, display=markdown
        )

    class ApplyStageInput(StageInput):
        """Apply Stage Input."""

        device_id: str
        intended_config: str
        approved_diff: str

    class ApplyStageOutput(StageOutput):
        """Apply Stage Output."""

    @stage_executor("apply_configuration")
    async def apply_configuration(self, stage_input: ApplyStageInput) -> ApplyStageOutput:
        """Apply the configuration to the device."""

        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        await workflow.execute_activity(
            apply_approved_configuration,
            ConfigApplyActivityInput(
                device_data=result.device,
                configuration=stage_input.intended_config,
                approved_diff=stage_input.approved_diff,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return DevicePasswordRotationWorkflow.ApplyStageOutput(
            display="Password rotation configuration applied successfully."
        )

    class BackupStageInput(StageInput):
        """Backup Stage Input."""

        device_id: str
        commit_id: str

    class BackupStageOutput(StageOutput):
        """Backup Stage Output."""

    @stage_executor("perform_backup")
    async def perform_backup(self, stage_input: BackupStageInput) -> BackupStageOutput:
        """Perform a configuration backup."""
        backup_input = BackupInput(
            device_id=stage_input.device_id,
            trigger=TriggerEnum.WORKFLOW,
            user="nv-config-manager-temporal",
            user_domain=None,
            intended_config_commit_id=stage_input.commit_id,
            workflow_id=workflow.info().workflow_id,
        )

        backup_handle = await workflow.start_child_workflow(
            BackupWorkflow.run, backup_input, run_timeout=timedelta(minutes=10)
        )
        self.append_child_workflow("perform_backup", backup_handle.id)
        await workflow.wait_condition(backup_handle.done)
        # TODO: once we have a UI, link out to this workflow.
        return DevicePasswordRotationWorkflow.BackupStageOutput(
            display=(
                f"Password rotation configuration change has been backed up via workflow {backup_handle.id}."
            )
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: DevicePasswordRotationInput) -> bool:  # type: ignore[override, ty:invalid-method-override]
        """Execute password rotation workflow."""
        self.set_input(workflow_input)
        load_config_output = await self.load_intended_configuration(
            DevicePasswordRotationWorkflow.LoadConfigStageInput(
                device_id=workflow_input.device_id,
                selected_secret=workflow_input.selected_secret,
            )
        )

        diff_output = await self.perform_configuration_diff(
            DevicePasswordRotationWorkflow.PerformDiffStageInput(
                device_id=workflow_input.device_id,
                intended_config=load_config_output.intended_config,
                username=workflow_input.selected_secret,
            )
        )

        if diff_output.diff and diff_output.approved:
            await self.apply_configuration(
                DevicePasswordRotationWorkflow.ApplyStageInput(
                    device_id=workflow_input.device_id,
                    intended_config=load_config_output.intended_config,
                    approved_diff=diff_output.diff,
                )
            )
            await self.perform_backup(
                DevicePasswordRotationWorkflow.BackupStageInput(
                    device_id=workflow_input.device_id,
                    commit_id=load_config_output.commit_id,
                )
            )
        elif diff_output.diff:
            # Rejected diff, no apply or backup
            self.set_stage_state("apply_configuration", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
        else:
            # No diff, backup still useful for keeping NVIDIA Config Manager UI in sync
            self.set_stage_state("apply_configuration", StateEnum.UNREACHABLE)
            await self.perform_backup(
                DevicePasswordRotationWorkflow.BackupStageInput(
                    device_id=workflow_input.device_id,
                    commit_id=load_config_output.commit_id,
                )
            )

        await self.archive_results()

        # Return true if a diff was pushed, no diff or rejected will have approved as False
        return diff_output.approved
