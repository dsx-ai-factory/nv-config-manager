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

from datetime import timedelta
from typing import Annotated, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from nv_config_manager.temporal.common.decorators.workflow import (
    run_nv_config_manager_workflow,
)
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StageRuntimeFailure,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import (
    DEVICE_REFERENCE,
    DeviceReference,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.config import build_workflow_url, get_ui_base_url
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.deploy import (
        ConfigApplyActivityInput,
        DiffActivityInput,
        LoadPartialConfigurationActivityInput,
        ValidateConfigDiffActivityInput,
        apply_approved_configuration,
        load_intended_configuration,
        load_partial_configuration,
        perform_candidate_diff,
        validate_config_diff,
    )
    from nv_config_manager.temporal.ngc.workflows.backup import (
        BackupInput,
        BackupWorkflow,
        TriggerEnum,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[
        "ConfigSyntaxException",
        "ConfigApplyFailureException",
        "DiffChangedException",
    ],
)
TENANT_CONFIG_COMMIT_ID_DESCRIPTION = (
    "Optional config-store commit ID for the tenant configuration. Must be supplied with "
    "intended_config_commit_id; omit both to deploy the latest tenant and intended configurations."
)
INTENDED_CONFIG_COMMIT_ID_DESCRIPTION = (
    "Optional config-store commit ID for the intended startup configuration from the same render "
    "snapshot as tenant_config_commit_id. Must be supplied with tenant_config_commit_id; omit both "
    "to deploy the latest tenant and intended configurations."
)


class DeployInput(BaseModel):
    """Config Deployment Workflow Input Definiton."""

    device_id: DeviceReference = Field(description="Identifier of the network device to configure.")
    commit_confirm: bool = Field(
        default=True,
        description="Whether to use commit-confirmed mode when the platform supports it.",
    )


@workflow.defn
class DeployWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network device configuration deployment workflow."""

    # Workflow metadata
    workflow_name = "Configuration Deploy"
    workflow_description = "Deploy intended configuration to network device with approval workflow"
    workflow_input_class = DeployInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/deploy"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="load_intended_configuration",
            description="Load the latest intended configuration from the Config Store.",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="perform_configuration_diff",
            description="Retrieve the configuration diff.",
            requires_approval=True,
            approval_threshold=1,
            depends_on=["load_intended_configuration"],
        )

        self.define_stage(
            name="apply_configuration",
            description="Apply the configuration to the device.",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device..",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

    class LoadConfigStageInput(StageInput):
        """Load Intended Config Stage Input."""

        device_id: str

    class LoadConfigStageOutput(StageOutput):
        """Load Intended Config Stage Output."""

        intended_config: str
        commit_id: str

    @stage_executor("load_intended_configuration")
    async def load_intended_configuration(
        self, stage_input: LoadConfigStageInput
    ) -> LoadConfigStageOutput:
        """Load the intended configuration content and commit ID."""
        result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        # Add device search attributes the first time we pull
        # them from the DCIM
        DeviceMixin.attach_device_search_attributes(result.device)

        content, commit_id, url = await workflow.execute_activity(
            load_intended_configuration,
            result.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        config_path = result.device.intended_config_path
        markdown = f"Loaded intended configuration from [{config_path}]({url})."
        return DeployWorkflow.LoadConfigStageOutput(
            intended_config=content, commit_id=commit_id, display=markdown
        )

    class PerformDiffStageInput(StageInput):
        """Diff Stage Input."""

        device_id: str
        intended_config: str

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

        diff = await workflow.execute_activity(
            perform_candidate_diff,
            DiffActivityInput(device_data=result.device, configuration=stage_input.intended_config),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not diff.strip():
            # No diff, end the workflow
            self.get_stage_by_name(stage_name).requires_approval = False
            return DeployWorkflow.PerformDiffStageOutput(
                approved=False,
                diff="",
                display=(
                    "No diff between the latest configuration render and the"
                    " configuration on the switch."
                ),
            )

        # Move stage to approval state
        markdown = f"Configuration Diff For Approval\n```\n{diff}\n```"
        output = DeployWorkflow.PerformDiffStageOutput(approved=False, diff=diff, display=markdown)
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
        markdown = f"Configuration Diff {approval_state} by {reviewmd}:\n```\n{diff}\n```"
        return DeployWorkflow.PerformDiffStageOutput(approved=approved, diff=diff, display=markdown)

    class ApplyStageInput(StageInput):
        """Apply Stage Input."""

        device_id: str
        intended_config: str
        approved_diff: str
        commit_confirm: bool = True

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

        try:
            await workflow.execute_activity(
                apply_approved_configuration,
                ConfigApplyActivityInput(
                    device_data=result.device,
                    configuration=stage_input.intended_config,
                    approved_diff=stage_input.approved_diff,
                    commit_confirm=stage_input.commit_confirm,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
        except ActivityError as e:
            if e.cause and isinstance(e.cause, ApplicationError):
                error_message = str(e.cause)
                stage_output = DeployWorkflow.ApplyStageOutput(display=error_message)
                self.set_stage_output("apply_configuration", stage_output)
            raise

        return DeployWorkflow.ApplyStageOutput(display="Configuration Applied Successfully.")

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
        return DeployWorkflow.BackupStageOutput(
            display=(f"Configuration change has been backed up via workflow {backup_handle.id}.")
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: DeployInput
    ) -> bool:
        """Execute deployment workflow."""
        self.set_input(workflow_input)
        load_config_output = await self.load_intended_configuration(
            DeployWorkflow.LoadConfigStageInput(device_id=workflow_input.device_id)
        )

        diff_output = await self.perform_configuration_diff(
            DeployWorkflow.PerformDiffStageInput(
                device_id=workflow_input.device_id,
                intended_config=load_config_output.intended_config,
            )
        )

        if diff_output.diff and diff_output.approved:
            await self.apply_configuration(
                DeployWorkflow.ApplyStageInput(
                    device_id=workflow_input.device_id,
                    intended_config=load_config_output.intended_config,
                    approved_diff=diff_output.diff,
                    commit_confirm=workflow_input.commit_confirm,
                )
            )

            await self.perform_backup(
                DeployWorkflow.BackupStageInput(
                    device_id=workflow_input.device_id,
                    commit_id=load_config_output.commit_id,
                )
            )
        elif diff_output.diff:
            # Rejected diff, no apply or backup
            self.set_stage_state("apply_configuration", StateEnum.UNREACHABLE)
            self.set_stage_state("perform_backup", StateEnum.UNREACHABLE)
        else:
            # No diff, backup still useful for keeping
            # NVIDIA Config Manager UI in sync
            self.set_stage_state("apply_configuration", StateEnum.UNREACHABLE)
            await self.perform_backup(
                DeployWorkflow.BackupStageInput(
                    device_id=workflow_input.device_id,
                    commit_id=load_config_output.commit_id,
                )
            )

        await self.archive_results()

        # Return true if a diff was pushed,
        # no diff or rejected will have approved as False
        return diff_output.approved


class TenantDeployInput(BaseModel):
    """Tenant Config Deployment Workflow Input Definiton."""

    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": [
                        "tenant_config_commit_id",
                        "intended_config_commit_id",
                    ],
                    "properties": {
                        "tenant_config_commit_id": {
                            "type": "string",
                            "pattern": r"^\d+$",
                            "description": TENANT_CONFIG_COMMIT_ID_DESCRIPTION,
                        },
                        "intended_config_commit_id": {
                            "type": "string",
                            "pattern": r"^\d+$",
                            "description": INTENDED_CONFIG_COMMIT_ID_DESCRIPTION,
                        },
                    },
                },
                {
                    "not": {
                        "anyOf": [
                            {"required": ["tenant_config_commit_id"]},
                            {"required": ["intended_config_commit_id"]},
                        ]
                    }
                },
            ]
        }
    )

    device: Annotated[str | NetworkDeviceData, DEVICE_REFERENCE] = Field(
        description="Identifier or preloaded data for the network device to configure."
    )
    tenant_config_commit_id: str | None = Field(
        default=None,
        pattern=r"^\d+$",
        description=TENANT_CONFIG_COMMIT_ID_DESCRIPTION,
    )
    intended_config_commit_id: str | None = Field(
        default=None,
        pattern=r"^\d+$",
        description=INTENDED_CONFIG_COMMIT_ID_DESCRIPTION,
    )
    use_full_intended_config: bool = Field(
        default=False,
        description=(
            "Use the full intended configuration as a replacement candidate while "
            "restricting the accepted diff to tenant configuration changes."
        ),
    )

    @model_validator(mode="after")
    def validate_render_snapshot(self) -> "TenantDeployInput":
        """Require non-null commit IDs to be supplied together or both omitted."""
        snapshot_fields = {
            "tenant_config_commit_id",
            "intended_config_commit_id",
        }
        supplied_fields = self.model_fields_set & snapshot_fields
        if supplied_fields and (
            supplied_fields != snapshot_fields
            or self.tenant_config_commit_id is None
            or self.intended_config_commit_id is None
        ):
            raise ValueError(
                "tenant_config_commit_id and intended_config_commit_id must both be non-null or both be omitted"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_render_snapshot(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        """Keep omitted snapshot IDs absent during Temporal serialization."""
        data = cast(dict[str, object], handler(self))
        if not self.model_fields_set & {
            "tenant_config_commit_id",
            "intended_config_commit_id",
        }:
            data.pop("tenant_config_commit_id", None)
            data.pop("intended_config_commit_id", None)
        return data


@workflow.defn
class TenantDeployWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Network device tenant configuration deployment workflow."""

    # Workflow metadata
    workflow_name = "Tenant Deploy"
    workflow_description = "Internal tenant configuration deployment child workflow"
    workflow_input_class = TenantDeployInput
    workflow_api_endpoint = None
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="load_tenant_configuration",
            description="Load the latest tenant configuration from config store.",
            requires_approval=False,
            depends_on=[],
        )

        self.define_stage(
            name="perform_configuration_diff",
            description="Retrieve the configuration diff.",
            requires_approval=False,
            depends_on=["load_tenant_configuration"],
        )

        self.define_stage(
            name="validate_configuration_diff",
            description="Validate the diff matches the tenant configuration.",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

        self.define_stage(
            name="apply_configuration",
            description="Apply the configuration to the device.",
            requires_approval=False,
            depends_on=["validate_configuration_diff"],
        )

        self.define_stage(
            name="perform_backup",
            description="Run the backup workflow for the device..",
            requires_approval=False,
            depends_on=["perform_configuration_diff"],
        )

    class LoadConfigStageInput(StageInput):
        """Load Tenant Config Stage Input."""

        device: str | NetworkDeviceData
        tenant_config_commit_id: str | None = None
        intended_config_commit_id: str | None = None
        use_full_intended_config: bool = False

    class LoadConfigStageOutput(StageOutput):
        """Load Tenant Config Stage Output."""

        device: NetworkDeviceData
        tenant_config: str
        deployment_config: str
        partial: bool
        commit_id: str
        intended_config_commit_id: str

    @stage_executor("load_tenant_configuration")
    async def load_tenant_configuration(
        self, stage_input: LoadConfigStageInput
    ) -> LoadConfigStageOutput:
        """Load the tenant configuration content and commit ID."""
        if isinstance(stage_input.device, str):
            result = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=stage_input.device),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            device = result.device
        else:
            device = stage_input.device

        DeviceMixin.attach_device_search_attributes(device)

        content, commit_id, url = await workflow.execute_activity(
            load_partial_configuration,
            LoadPartialConfigurationActivityInput(
                device_data=device,
                config_file=device.tenant_config_file,
                commit_id=stage_input.tenant_config_commit_id,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        intended_config = None
        intended_config_url = None
        intended_config_commit_id = stage_input.intended_config_commit_id
        if intended_config_commit_id is None:
            (
                intended_config,
                intended_config_commit_id,
                intended_config_url,
            ) = await workflow.execute_activity(
                load_intended_configuration,
                device,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
        if intended_config_commit_id is None:
            raise ApplicationError(
                "Unable to resolve intended configuration commit ID for tenant deployment"
            )
        resolved_intended_config_commit_id = intended_config_commit_id

        if stage_input.use_full_intended_config:
            if intended_config is None:
                (
                    intended_config,
                    resolved_intended_config_commit_id,
                    intended_config_url,
                ) = await workflow.execute_activity(
                    load_partial_configuration,
                    LoadPartialConfigurationActivityInput(
                        device_data=device,
                        config_file=device.intended_config_file,
                        commit_id=resolved_intended_config_commit_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
            if intended_config is None:
                raise ApplicationError(
                    "Unable to load intended configuration for tenant replacement"
                )
            deployment_config = intended_config
            partial = False
        else:
            deployment_config = content
            partial = True

        config_path = device.tenant_config_path
        markdown = f"Loaded tenant configuration from [{config_path}]({url})."
        if stage_input.use_full_intended_config:
            intended_config_path = device.intended_config_path
            markdown += (
                " Assignment removals will be deployed by replacing the candidate with "
                f"the full intended configuration from [{intended_config_path}]"
                f"({intended_config_url})."
            )
        return TenantDeployWorkflow.LoadConfigStageOutput(
            device=device,
            tenant_config=content,
            deployment_config=deployment_config,
            partial=partial,
            commit_id=commit_id,
            intended_config_commit_id=resolved_intended_config_commit_id,
            display=markdown,
        )

    class PerformDiffStageInput(StageInput):
        """Diff Stage Input."""

        device: NetworkDeviceData
        deployment_config: str
        partial: bool

    class PerformDiffStageOutput(StageOutput):
        """Diff Stage Output."""

        diff: str

    @stage_executor("perform_configuration_diff")
    async def perform_configuration_diff(
        self, stage_input: PerformDiffStageInput
    ) -> PerformDiffStageOutput:
        """Execute the configuration diff against the device."""
        diff = await workflow.execute_activity(
            perform_candidate_diff,
            DiffActivityInput(
                device_data=stage_input.device,
                configuration=stage_input.deployment_config,
                partial=stage_input.partial,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not diff.strip():
            # No diff, end the workflow
            return TenantDeployWorkflow.PerformDiffStageOutput(
                diff="",
                display=(
                    "No diff between the latest configuration render and the"
                    " configuration on the switch."
                ),
            )

        # Display diff without approval
        markdown = f"Configuration Diff\n```\n{diff}\n```"
        return TenantDeployWorkflow.PerformDiffStageOutput(diff=diff, display=markdown)

    class ValidateDiffStageInput(StageInput):
        """Validate Diff Stage Input."""

        tenant_config: str
        diff: str

    class ValidateDiffStageOutput(StageOutput):
        """Validate Diff Stage Output."""

        valid: bool
        message: str | None = None

    @stage_executor("validate_configuration_diff")
    async def validate_configuration_diff(
        self, stage_input: ValidateDiffStageInput
    ) -> ValidateDiffStageOutput:
        """Validate that the diff matches the tenant configuration."""
        # Define allowed and disallowed patterns for tenant configuration validation
        disallowed_patterns = [r"^nv (set|unset) vrf default\b"]
        allowed_patterns = [
            r"^nv (set|unset) nve vxlan\b",
            r"^nv (set|unset) evpn\b",
            r"^nv (set|unset) interface \S+ (ip )?vrf\b",
            r"^nv (set|unset) vrf \S+ evpn\b",
            r"^nv (set|unset) vrf \S+ router\b",
        ]
        result = await workflow.execute_activity(
            validate_config_diff,
            ValidateConfigDiffActivityInput(
                tenant_config=stage_input.tenant_config,
                diff=stage_input.diff,
                allowed_patterns=allowed_patterns,
                disallowed_patterns=disallowed_patterns,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not result.valid:
            raise StageRuntimeFailure(f"Invalid diff: {result.message}")

        if result.message:
            display_message = (
                f"Validation result: Valid. {result.message}\n\n"
                f"**Validated Diff:**\n```\n{stage_input.diff}\n```"
            )
        else:
            display_message = (
                "Validation result: Valid. The diff contains only allowed "
                "tenant configuration commands.\n\n"
                f"**Validated Diff:**\n```\n{stage_input.diff}\n```"
            )

        return TenantDeployWorkflow.ValidateDiffStageOutput(
            valid=result.valid,
            message=result.message,
            display=display_message,
        )

    class ApplyStageInput(StageInput):
        """Apply Stage Input."""

        device: NetworkDeviceData
        deployment_config: str
        diff: str
        partial: bool

    class ApplyStageOutput(StageOutput):
        """Apply Stage Output."""

    @stage_executor("apply_configuration")
    async def apply_configuration(self, stage_input: ApplyStageInput) -> ApplyStageOutput:
        """Apply the configuration to the device."""
        await workflow.execute_activity(
            apply_approved_configuration,
            ConfigApplyActivityInput(
                device_data=stage_input.device,
                configuration=stage_input.deployment_config,
                approved_diff=stage_input.diff,
                partial=stage_input.partial,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            # Do not retry if the diff changed after approval
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        return TenantDeployWorkflow.ApplyStageOutput(display="Configuration Applied Successfully.")

    class BackupStageInput(StageInput):
        """Backup Stage Input."""

        device_id: str

    class BackupStageOutput(StageOutput):
        """Backup Stage Output."""

    @stage_executor("perform_backup")
    async def perform_backup(self, stage_input: BackupStageInput) -> BackupStageOutput:
        """Perform a configuration backup."""
        # Get UI base URL for creating workflow link
        ui_base_url = await workflow.execute_activity(
            get_ui_base_url,
            start_to_close_timeout=timedelta(seconds=10),
        )

        backup_input = BackupInput(
            device_id=stage_input.device_id,
            trigger=TriggerEnum.WORKFLOW,
            user="nv-config-manager-temporal",
            user_domain=None,
            workflow_id=workflow.info().workflow_id,
            suppress_drift_notification=True,
        )

        backup_handle = await workflow.start_child_workflow(
            BackupWorkflow.run, backup_input, run_timeout=timedelta(minutes=10)
        )
        self.append_child_workflow("perform_backup", backup_handle.id)
        await workflow.wait_condition(backup_handle.done)

        backup_workflow_url = build_workflow_url(ui_base_url, backup_handle.id)

        return TenantDeployWorkflow.BackupStageOutput(
            display=(
                "Configuration change has been backed up via "
                f"[workflow {backup_handle.id}]({backup_workflow_url})."
            )
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: TenantDeployInput
    ) -> bool:
        """Execute tenant deployment workflow."""
        self.set_input(workflow_input)
        load_config_output = await self.load_tenant_configuration(
            TenantDeployWorkflow.LoadConfigStageInput(
                device=workflow_input.device,
                tenant_config_commit_id=workflow_input.tenant_config_commit_id,
                intended_config_commit_id=workflow_input.intended_config_commit_id,
                use_full_intended_config=workflow_input.use_full_intended_config,
            )
        )

        diff_output = await self.perform_configuration_diff(
            TenantDeployWorkflow.PerformDiffStageInput(
                device=load_config_output.device,
                deployment_config=load_config_output.deployment_config,
                partial=load_config_output.partial,
            )
        )

        if diff_output.diff:
            # Validate the diff matches the tenant config
            await self.validate_configuration_diff(
                TenantDeployWorkflow.ValidateDiffStageInput(
                    tenant_config=load_config_output.tenant_config,
                    diff=diff_output.diff,
                )
            )

            await self.apply_configuration(
                TenantDeployWorkflow.ApplyStageInput(
                    device=load_config_output.device,
                    deployment_config=load_config_output.deployment_config,
                    diff=diff_output.diff,
                    partial=load_config_output.partial,
                )
            )

        else:
            self.set_stage_state("validate_configuration_diff", StateEnum.UNREACHABLE)
            self.set_stage_state("apply_configuration", StateEnum.UNREACHABLE)

        await self.perform_backup(
            TenantDeployWorkflow.BackupStageInput(
                device_id=load_config_output.device.id,
            )
        )
        await self.archive_results()

        # Return true if a diff was applied
        return bool(diff_output.diff)
