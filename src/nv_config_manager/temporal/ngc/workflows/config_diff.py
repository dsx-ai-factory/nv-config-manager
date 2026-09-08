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
"""Read-only configuration diff workflow."""

from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.workflow_references import (
    DEVICE_REFERENCE,
    DeviceReference,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.decorators.workflow import (
        run_nv_config_manager_workflow,
    )
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.common.mixins.stage import (
        StageInput,
        StageMixin,
        StageOutput,
        stage_executor,
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

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
CONFIG_DIFF_DEVICE_DESCRIPTION = "Preloaded network device data, if available."


class ConfigDiffInput(BaseModel):
    """Config Diff Workflow input."""

    device_id: DeviceReference = Field(description="Identifier of the network device to compare.")
    device: Annotated[NetworkDeviceData | None, DEVICE_REFERENCE] = Field(
        default=None,
        description=CONFIG_DIFF_DEVICE_DESCRIPTION,
    )


class ConfigDiffWorkflowOutput(BaseModel):
    """Config Diff Workflow output."""

    diff: str
    has_diff: bool


@workflow.defn
class ConfigDiffWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Read-only diff of intended configuration against the live device."""

    workflow_name = "Configuration Diff"
    workflow_description = (
        "Compare the intended configuration against the live device without applying any changes"
    )
    workflow_input_class = ConfigDiffInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/config_diff"
    workflow_namespace = "ngc"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_device_data",
            description="Get the device data if not provided already.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="load_intended_configuration",
            description="Load the latest intended configuration from the Config Store.",
            requires_approval=False,
            depends_on=["get_device_data"],
        )
        self.define_stage(
            name="perform_configuration_diff",
            description="Compare the intended configuration against the live device.",
            requires_approval=False,
            depends_on=["load_intended_configuration"],
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
        """Resolve the device data, reusing preloaded input when a caller provides it."""
        if stage_input.device:
            device = stage_input.device
        else:
            result = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=stage_input.device_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            device = result.device
        return ConfigDiffWorkflow.NetworkDeviceDataStageOutput(
            device=device, display=self.markdown_table(device)
        )

    class LoadConfigStageInput(StageInput):
        """Load Intended Config Stage Input."""

        device: NetworkDeviceData

    class LoadConfigStageOutput(StageOutput):
        """Load Intended Config Stage Output."""

        intended_config: str

    @stage_executor("load_intended_configuration")
    async def load_intended_configuration(
        self, stage_input: LoadConfigStageInput
    ) -> LoadConfigStageOutput:
        """Load the intended configuration content."""
        content, _commit_id, url = await workflow.execute_activity(
            load_intended_configuration,
            stage_input.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        config_path = stage_input.device.intended_config_path
        markdown = f"Loaded intended configuration from [{config_path}]({url})."
        return ConfigDiffWorkflow.LoadConfigStageOutput(intended_config=content, display=markdown)

    class PerformDiffStageInput(StageInput):
        """Diff Stage Input."""

        device: NetworkDeviceData
        intended_config: str

    class PerformDiffStageOutput(StageOutput):
        """Diff Stage Output."""

        diff: str
        has_diff: bool

    @stage_executor("perform_configuration_diff")
    async def perform_configuration_diff(
        self, stage_input: PerformDiffStageInput
    ) -> PerformDiffStageOutput:
        """Compute the diff against the live device without applying it."""
        diff = await workflow.execute_activity(
            perform_candidate_diff,
            DiffActivityInput(
                device_data=stage_input.device, configuration=stage_input.intended_config
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        if not diff.strip():
            return ConfigDiffWorkflow.PerformDiffStageOutput(
                diff="",
                has_diff=False,
                display=(
                    "No diff between the latest intended configuration and the"
                    " configuration on the device."
                ),
            )

        markdown = f"Configuration Diff\n```\n{diff}\n```"
        return ConfigDiffWorkflow.PerformDiffStageOutput(diff=diff, has_diff=True, display=markdown)

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self, workflow_input: ConfigDiffInput
    ) -> ConfigDiffWorkflowOutput:
        """Execute the read-only diff workflow."""
        self.set_input(workflow_input)
        device_data = (
            await self.get_device_data(
                ConfigDiffWorkflow.NetworkDeviceDataStageInput(
                    device_id=workflow_input.device_id, device=workflow_input.device
                )
            )
        ).device
        DeviceMixin.attach_device_search_attributes(device_data)

        load_config_output = await self.load_intended_configuration(
            ConfigDiffWorkflow.LoadConfigStageInput(device=device_data)
        )
        diff_output = await self.perform_configuration_diff(
            ConfigDiffWorkflow.PerformDiffStageInput(
                device=device_data,
                intended_config=load_config_output.intended_config,
            )
        )
        await self.archive_results()
        return ConfigDiffWorkflowOutput(diff=diff_output.diff, has_diff=diff_output.has_diff)
