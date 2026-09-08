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
"""Network Device Reprovision Workflow Definition."""

from datetime import timedelta

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
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.config import (
        build_workflow_url,
        get_ui_base_url,
    )
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import load_intended_configuration
    from nv_config_manager.temporal.ngc.activities.os import (
        ExecuteZTPInput,
        PollZTPStatusInput,
        execute_ztp,
        poll_ztp_status,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=["ConfigSyntaxException", "FirmwareUpgradeException"],
)
REPROVISION_WORKFLOW_UPDATES_PATCH_ID = "reprovision-workflow-updates-v1"


class ReprovisionInput(BaseModel):
    """Reprovision Workflow Input Definition."""

    device_id: DeviceReference = Field(
        description="Identifier of the network device to reprovision."
    )


@workflow.defn
class ReprovisionWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network device reprovisioning workflow with ZTP."""

    # Workflow metadata
    workflow_name = "Reprovision"
    workflow_description = "Reprovision a network device using pre- and post-ZTP backups"
    workflow_input_class = ReprovisionInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/reprovision"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self._pre_backup_test_enabled = workflow.patched(REPROVISION_WORKFLOW_UPDATES_PATCH_ID)
        if self._pre_backup_test_enabled:
            self.define_stage(
                name="pre_reprovision_backup",
                description="Back up the device and validate the intended configuration.",
                requires_approval=False,
                depends_on=[],
            )

        self.define_stage(
            name="execute_ztp",
            description="Execute ZTP and wait for completion.",
            requires_approval=False,
            depends_on=(["pre_reprovision_backup"] if self._pre_backup_test_enabled else []),
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device.",
            requires_approval=False,
            depends_on=["execute_ztp"],
        )

    async def _fetch_device(self, device_id: str) -> NetworkDeviceData:
        """Fetch fresh device data and attach workflow search attributes."""
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        DeviceMixin.attach_device_search_attributes(result.device)
        return result.device

    async def _get_ui_base_url(self) -> str:
        """Return the Temporal UI URL without blocking backups when lookup fails."""
        if not self._pre_backup_test_enabled:
            return ""
        try:
            return await workflow.execute_activity(
                get_ui_base_url,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
        except ActivityError:
            self.logger.warning(
                "Unable to retrieve the Temporal UI URL; continuing backup without a link."
            )
            return ""

    @staticmethod
    def _is_invalid_config_error(error: BaseException) -> bool:
        """Return whether a failed backup child encountered invalid intended config."""
        current: BaseException | None = error
        while current is not None:
            if isinstance(current, ApplicationError) and current.type == "ConfigSyntaxException":
                return True
            current = getattr(current, "cause", None)
        return False

    @staticmethod
    def _backup_workflow_reference(ui_base_url: str, workflow_id: str) -> str:
        """Format a linked child workflow reference when the UI URL is available."""
        if ui_base_url:
            return f"[backup workflow]({build_workflow_url(ui_base_url, workflow_id)})"
        return f"backup workflow `{workflow_id}`"

    class PreReprovisionBackupStageInput(StageInput):
        """Pre-Reprovision Backup Stage Input."""

        device_id: str

    class PreReprovisionBackupStageOutput(StageOutput):
        """Pre-Reprovision Backup Stage Output."""

    @stage_executor("pre_reprovision_backup")
    async def pre_reprovision_backup(
        self, stage_input: PreReprovisionBackupStageInput
    ) -> PreReprovisionBackupStageOutput:
        """Back up the device and validate its intended configuration."""
        ui_base_url = await self._get_ui_base_url()
        backup_handle = await workflow.start_child_workflow(
            BackupWorkflow.run,
            BackupInput(
                device_id=stage_input.device_id,
                trigger=TriggerEnum.WORKFLOW,
                user="nv-config-manager-temporal",
                user_domain=None,
                workflow_id=workflow.info().workflow_id,
                intended_config_commit_id="",
                terminate_on_failure=True,
            ),
            run_timeout=timedelta(minutes=10),
        )
        self.append_child_workflow("pre_reprovision_backup", backup_handle.id)
        backup_reference = self._backup_workflow_reference(ui_base_url, backup_handle.id)

        try:
            await backup_handle
        except ChildWorkflowError as exc:
            if self._is_invalid_config_error(exc):
                device_data = await self._fetch_device(stage_input.device_id)
                _content, _commit_id, intended_config_url = await workflow.execute_activity(
                    load_intended_configuration,
                    device_data,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                self.set_stage_output(
                    "pre_reprovision_backup",
                    ReprovisionWorkflow.PreReprovisionBackupStageOutput(
                        display=(
                            "### Invalid intended configuration\n\n"
                            f"The intended configuration for **{device_data.name}** is invalid "
                            "and could not be loaded as a candidate. No factory reset was "
                            "requested.\n\n"
                            "Check the intended configuration "
                            f"[here]({intended_config_url}). Once it is fixed this stage can be "
                            f"retried.\n\nReview the {backup_reference} for details."
                        )
                    ),
                )
                raise ApplicationError(
                    "The intended configuration is invalid. Fix the errors and retry this stage."
                ) from exc

            self.set_stage_output(
                "pre_reprovision_backup",
                ReprovisionWorkflow.PreReprovisionBackupStageOutput(
                    display=(
                        f"The pre-reprovision backup failed via {backup_reference}. "
                        "Review the backup workflow for details, then retry this stage."
                    )
                ),
            )
            raise ApplicationError(
                "The pre-reprovision backup failed. Review it and retry this stage."
            ) from exc

        return ReprovisionWorkflow.PreReprovisionBackupStageOutput(
            display=f"The pre-reprovision backup completed via {backup_reference}.",
        )

    class ExecuteZTPStageInput(StageInput):
        """Execute ZTP Stage Input."""

        device_id: str

    class ExecuteZTPStageOutput(StageOutput):
        """Execute ZTP Stage Output."""

    @stage_executor("execute_ztp")
    async def execute_ztp_stage(self, stage_input: ExecuteZTPStageInput) -> ExecuteZTPStageOutput:
        """Execute ZTP and wait for completion."""
        device_data = await self._fetch_device(stage_input.device_id)

        # Trigger ZTP through factory reset
        ztp_execute_result = await workflow.execute_activity(
            execute_ztp,
            ExecuteZTPInput(device_data=device_data),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Poll ZTP status until success (with integrated reboot verification)
        ztp_status_result = await workflow.execute_activity(
            poll_ztp_status,
            PollZTPStatusInput(
                device_data=device_data,
                ztp_execution_timestamp=ztp_execute_result.start_time,  # Verify reboot happened
            ),
            start_to_close_timeout=timedelta(minutes=35),  # 30 min + 5 min buffer
            heartbeat_timeout=timedelta(minutes=3),  # Detect dead activities within 3 minutes
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not ztp_status_result.success:
            raise ApplicationError(
                "ZTP failed to complete within 30 minutes, check the device logs for details."
            )

        return ReprovisionWorkflow.ExecuteZTPStageOutput(
            display="ZTP completed successfully",
        )

    class BackupStageInput(StageInput):
        """Backup Stage Input."""

        device_id: str

    class BackupStageOutput(StageOutput):
        """Backup Stage Output."""

    @stage_executor("perform_backup")
    async def perform_backup(self, stage_input: BackupStageInput) -> BackupStageOutput:
        """Perform a configuration backup."""
        ui_base_url = await self._get_ui_base_url()

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

        if ui_base_url:
            backup_workflow_url = build_workflow_url(ui_base_url, backup_handle.id)
            display = (
                f"Configuration backup completed via [backup workflow]({backup_workflow_url})."
            )
        else:
            display = f"Configuration backup completed via workflow {backup_handle.id}."

        return ReprovisionWorkflow.BackupStageOutput(
            display=display,
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: ReprovisionInput) -> bool:  # type: ignore[override, ty:invalid-method-override]
        """Execute reprovision workflow."""
        self.set_input(workflow_input)

        # Validate intended configuration
        if self._pre_backup_test_enabled:
            await self.pre_reprovision_backup(
                ReprovisionWorkflow.PreReprovisionBackupStageInput(
                    device_id=workflow_input.device_id
                )
            )

        # Execute ZTP
        await self.execute_ztp_stage(
            ReprovisionWorkflow.ExecuteZTPStageInput(device_id=workflow_input.device_id)
        )

        # Perform backup
        await self.perform_backup(
            ReprovisionWorkflow.BackupStageInput(device_id=workflow_input.device_id)
        )

        await self.archive_results()
        return True
