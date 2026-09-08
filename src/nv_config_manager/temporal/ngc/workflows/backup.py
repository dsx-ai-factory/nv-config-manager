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
"""Network Device Backup Workflow Definition."""

import asyncio
from datetime import timedelta
from enum import StrEnum

from pydantic import Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StageWorkflowInput,
    stage_executor,
)
from nv_config_manager.temporal.common.secret_redaction import redact_junos_secrets
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.backup import (
        PersistConfigBackupInput,
        RecordBackupConfigManagerPluginInput,
        load_running_configuration,
        persist_config_backup,
        record_backup_config_manager_plugin,
    )
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        DiffActivityInput,
        load_intended_configuration,
        perform_candidate_diff,
    )
    from nv_config_manager.temporal.ngc.activities.slack import (
        SlackMessageInput,
        send_slack_message,
    )


DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


class TriggerEnum(StrEnum):
    """Enum of potential backup triggers."""

    SCHEDULED = "SCHEDULED"
    SYSLOG = "SYSLOG"
    WORKFLOW = "WORKFLOW"
    API = "API"


class BackupInput(StageWorkflowInput):
    """Backup Workflow Input Definiton."""

    device_id: DeviceReference = Field(description="Identifier of the network device to back up.")
    trigger: TriggerEnum = Field(description="Reason the backup workflow was started.")
    user: str | None = Field(default=None, description="User that requested the backup.")
    user_domain: str | None = Field(
        default=None, description="Domain of the user requesting the backup."
    )
    workflow_id: str | None = Field(
        default=None, description="Identifier of the parent workflow, if any."
    )
    intended_config_commit_id: str | None = Field(
        default=None, description="Config Store commit containing the intended configuration."
    )
    suppress_drift_notification: bool = Field(
        default=False,
        description="Suppress the Slack notification when configuration drift is detected.",
    )


@workflow.defn
class BackupWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network device configuration backup workflow."""

    # Workflow metadata
    workflow_name = "Configuration Backup"
    workflow_description = (
        "Backup network device configuration to the Config Store and NVIDIA Config Manager plugin"
    )
    workflow_input_class = BackupInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/backup"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="load_running_configuration",
            description="Load the running configuration from the device.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="check_drift",
            description="Check for configuration drift.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="persist_backup",
            description="Persist Running Configuration to the Config Store and NVIDIA Config Manager plugin.",
            requires_approval=False,
            depends_on=["load_running_configuration", "check_drift"],
        )

    class LoadConfigStageInput(StageInput):
        """Load Running Config Stage Input."""

        device_id: str

    class LoadConfigStageOutput(StageOutput):
        """Load Running Config Stage Output."""

        device_data: NetworkDeviceData
        running_config: str

    @stage_executor("load_running_configuration")
    async def load_running_config(self, stage_input: LoadConfigStageInput) -> LoadConfigStageOutput:
        """Load the running configuration of a device."""
        device_data = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        # Add device search attributes the first time we pull
        # them from the DCIM
        DeviceMixin.attach_device_search_attributes(device_data.device)

        running_config: str = await workflow.execute_activity(
            load_running_configuration,
            device_data.device,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return BackupWorkflow.LoadConfigStageOutput(
            device_data=device_data.device,
            running_config=running_config,
            display=f"```\n{running_config}\n```",
        )

    class CheckDriftStageInput(StageInput):
        """Check Drift Stage Input."""

        device_id: str
        intended_config_commit_id: str | None
        suppress_drift_notification: bool

    class CheckDriftStageOutput(StageOutput):
        """Check Drift Stage Output."""

        commit_id: str
        diff: str
        has_drift: bool

    @stage_executor("check_drift")
    async def check_drift(self, stage_input: CheckDriftStageInput) -> CheckDriftStageOutput:
        """Check for configuration drift by comparing running config with intended config."""
        if stage_input.intended_config_commit_id:
            # No need to check for drift as this was invoked by a Deploy Workflow
            return BackupWorkflow.CheckDriftStageOutput(
                commit_id=stage_input.intended_config_commit_id,
                diff="",
                has_drift=False,
                display="No drift detected between running and intended configuration.",
            )

        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Load intended configuration
        content, commit_id, url = await workflow.execute_activity(
            load_intended_configuration,
            result.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        # Perform diff
        diff = await workflow.execute_activity(
            perform_candidate_diff,
            DiffActivityInput(device_data=result.device, configuration=content),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        diff = redact_junos_secrets(diff)

        has_drift = bool(diff.strip())
        if not has_drift:
            markdown = "No drift detected between running and intended configuration."
        else:
            markdown = f"Configuration Drift Detected:\n```\n{diff}\n```"
            if not stage_input.suppress_drift_notification:
                await workflow.execute_activity(
                    send_slack_message,
                    SlackMessageInput(
                        message=f"Configuration Drift Detected on {result.device.name}, see workflow link for details.",
                        link_workflow=True,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
        config_path = result.device.intended_config_path
        markdown = f"Loaded intended configuration from [{config_path}]({url}).\n{markdown}"

        return BackupWorkflow.CheckDriftStageOutput(
            commit_id=commit_id,
            diff=diff,
            has_drift=has_drift,
            display=markdown,
        )

    class PersistBackupStageInput(StageInput):
        """Persist Backup Stage Input."""

        device_data: NetworkDeviceData
        running_config: str
        trigger: TriggerEnum
        user: str
        user_domain: str | None
        workflow_id: str | None
        intended_config_commit_id: str | None

    class PersistBackupStageOutput(StageOutput):
        """Persist Backup Stage Output."""

        changed: bool

    @stage_executor("persist_backup")
    async def persist_backup(
        self, stage_input: PersistBackupStageInput
    ) -> PersistBackupStageOutput:
        """Persist the configuration to the Config Store and update NVIDIA Config Manager plugin."""
        commit_message = f"Backup trigger: {stage_input.trigger} User: {stage_input.user}"
        commit_id: str = await workflow.execute_activity(
            persist_config_backup,
            PersistConfigBackupInput(
                device_data=stage_input.device_data,
                device_running_config=stage_input.running_config,
                commit_message=commit_message,
                user=stage_input.user,
                user_domain=stage_input.user_domain,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        changed, markdown = await workflow.execute_activity(
            record_backup_config_manager_plugin,
            RecordBackupConfigManagerPluginInput(
                workflow_id=stage_input.workflow_id or workflow.info().workflow_id,
                device_id=stage_input.device_data.id,
                commit_id=commit_id,
                path=stage_input.device_data.backup_path,
                user=stage_input.user,
                commit_message=commit_message,
                deployed_commit_id=stage_input.intended_config_commit_id,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return BackupWorkflow.PersistBackupStageOutput(changed=changed, display=markdown)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: BackupInput) -> bool:  # type: ignore[override, ty:invalid-method-override]
        """Execute backup, return True if changed."""
        if not workflow_input.user:
            # Request did not come through API and
            # therefore the user did not get set.
            raise ApplicationError("Missing user for backup attribution.")
        self.set_input(workflow_input)

        # Execute load_running_configuration and check_drift in parallel
        load_config_output, drift_output = await asyncio.gather(
            self.load_running_config(
                BackupWorkflow.LoadConfigStageInput(device_id=workflow_input.device_id)
            ),
            self.check_drift(
                BackupWorkflow.CheckDriftStageInput(
                    device_id=workflow_input.device_id,
                    intended_config_commit_id=workflow_input.intended_config_commit_id,
                    suppress_drift_notification=workflow_input.suppress_drift_notification,
                )
            ),
        )

        # No full-config drift means the device matches the latest intended
        # content, even when this backup follows a partial tenant deployment.
        if not drift_output.has_drift and not workflow_input.intended_config_commit_id:
            workflow_input.intended_config_commit_id = drift_output.commit_id

        persist_output = await self.persist_backup(
            BackupWorkflow.PersistBackupStageInput(
                device_data=load_config_output.device_data,
                running_config=load_config_output.running_config,
                trigger=workflow_input.trigger,
                user=workflow_input.user,
                user_domain=workflow_input.user_domain,
                workflow_id=workflow_input.workflow_id,
                intended_config_commit_id=workflow_input.intended_config_commit_id,
            )
        )
        await self.archive_results()
        return persist_output.changed
