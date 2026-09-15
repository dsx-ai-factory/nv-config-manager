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
"""Infiniband Unhealthy Ports Validation Workflow Definition."""

import base64
from datetime import timedelta

from py_markdown_table.markdown_table import markdown_table
from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

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
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.ufm import (
        GetUFMPortsInput,
        get_ib_ports,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[],
)


class InfinibandGetUnhealthyPortsInput(BaseModel):
    """Unhealthy Ports Validation Workflow Input Definition."""

    device_id: DeviceReference = Field(description="Identifier of the UFM device to inspect.")


@workflow.defn
class InfinibandGetUnhealthyPortsWorkflow(WorkflowMetadataMixin, StageMixin):
    """Infiniband fabric health monitoring workflow for unhealthy port detection."""

    # Workflow metadata
    workflow_name = "InfiniBand Get Unhealthy Ports"
    workflow_description = "Validate and report unhealthy ports in Infiniband network fabric"
    workflow_input_class = InfinibandGetUnhealthyPortsInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/infiniband_get_unhealthy_ports"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="validate_fabric_health",
            description="Validate and report unhealthy ports in the network fabric.",
            requires_approval=False,
            depends_on=[],
        )

    class ValidateUnhealthyPortsStageInput(StageInput):
        """Validate Unhealthy Ports Stage Input."""

        device_id: str

    class ValidateUnhealthyPortsStageOutput(StageOutput):
        """Validate Unhealthy Ports Stage Output."""

        unhealthy_ports: list[dict]
        display: str

    @stage_executor("validate_fabric_health")
    async def validate_fabric_health(
        self, stage_input: ValidateUnhealthyPortsStageInput
    ) -> ValidateUnhealthyPortsStageOutput:
        """Execute unhealthy ports validation."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(
                device_id=stage_input.device_id,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not device_result.device.host:
            raise ValueError("Device has no primary IP address configured.")

        result = await workflow.execute_activity(
            get_ib_ports,
            GetUFMPortsInput(
                host=device_result.device.host,
                unhealthy=True,
                site=device_result.device.site,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if not result.ports:
            return InfinibandGetUnhealthyPortsWorkflow.ValidateUnhealthyPortsStageOutput(
                unhealthy_ports=[],
                display="No unhealthy ports found in the network fabric.",
            )

        b64_csv = base64.b64encode(result.csv_data.encode("utf-8"))

        markdown = "Unhealthy ports found in the network fabric\n\n"
        markdown += f"[Export to CSV](data:text/csv;base64,{b64_csv.decode()})\n\n\n"

        markdown += (
            markdown_table(result.ports).set_params(quote=False, row_sep="markdown").get_markdown()
        )

        return InfinibandGetUnhealthyPortsWorkflow.ValidateUnhealthyPortsStageOutput(
            unhealthy_ports=result.ports,
            display=markdown,
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: InfinibandGetUnhealthyPortsInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Execute unhealthy ports validation workflow."""
        self.set_input(workflow_input)

        output = await self.validate_fabric_health(
            InfinibandGetUnhealthyPortsWorkflow.ValidateUnhealthyPortsStageInput(
                device_id=workflow_input.device_id,
            )
        )
        return output.display
