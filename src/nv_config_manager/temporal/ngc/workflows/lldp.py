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
"""LLDP Workflows."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.workflow_references import OptionalDeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.device import InterfaceNeighborData
    from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin, NetworkDeviceData
    from nv_config_manager.temporal.common.mixins.stage import (
        StageMixin,
        StageOutput,
        stage_executor,
    )
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        SwitchPortByMacActivityInput,
        SwitchPortByMacActivityOutput,
        get_network_device,
        get_switch_port_by_remote_mac_address,
    )
    from nv_config_manager.temporal.ngc.activities.device import (
        SwitchPortNeighborActivityInput,
        load_neighbor_data_by_switch_port,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


class PortLLDPInfoInput(BaseModel):
    """Input for Port LLDP Info Workflow."""

    device_id: OptionalDeviceReference = Field(
        default=None, description="Identifier of the network device to inspect."
    )
    interface: str | None = Field(default=None, description="Name of the local interface.")
    remote_mac_address: str | None = Field(
        default=None, description="MAC address of the remote device to locate."
    )


@workflow.defn
class PortLLDPInfoWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """LLDP neighbor discovery workflow for network port analysis."""

    # Workflow metadata
    workflow_name = "Port LLDP Info"
    workflow_description = "Gather LLDP neighbor data for network port analysis and troubleshooting"
    workflow_input_class = PortLLDPInfoInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/port_lldp_info"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_switch_port",
            description="Load the switch port by MAC address from the DCIM",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_interface_neighbor_data",
            description="Get Switch Port Neighbor Data",
            requires_approval=False,
            depends_on=["get_switch_port"],
        )

    class SwitchPortStageOutput(StageOutput):
        """Switch Port Stage Output."""

        device: NetworkDeviceData
        interface: str

    @stage_executor("get_switch_port")
    async def get_switch_port(self, stage_input: PortLLDPInfoInput) -> SwitchPortStageOutput:
        """Get Switch Port by MAC Address."""
        if stage_input.device_id and stage_input.interface:
            device_data = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=stage_input.device_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            self.attach_device_search_attributes(device_data.device)
            return PortLLDPInfoWorkflow.SwitchPortStageOutput(
                device=device_data.device,
                interface=stage_input.interface,
                display=f"{device_data.device.name}:{stage_input.interface}",
            )

        if stage_input.remote_mac_address:
            # Look up the device in the DCIM
            switch_port_data: SwitchPortByMacActivityOutput = await workflow.execute_activity(
                get_switch_port_by_remote_mac_address,
                SwitchPortByMacActivityInput(remote_mac_address=stage_input.remote_mac_address),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            self.attach_device_search_attributes(switch_port_data.device)
            return PortLLDPInfoWorkflow.SwitchPortStageOutput(
                device=switch_port_data.device,
                interface=switch_port_data.interface,
                display=f"{switch_port_data.device.name}:{switch_port_data.interface}",
            )
        raise ApplicationError("Must supply either device_id and interface or remote_mac_address")

    class InterfaceNeighborStageOutput(StageOutput):
        """Interface Neighbor Stage Output."""

        interface_neighbor_data: InterfaceNeighborData | None

    @stage_executor("get_interface_neighbor_data")
    async def get_interface_neighbor_data(
        self, stage_input: SwitchPortStageOutput
    ) -> InterfaceNeighborStageOutput:
        """Get Interface Neighbor Data."""
        neighbor_data = await workflow.execute_activity(
            load_neighbor_data_by_switch_port,
            SwitchPortNeighborActivityInput(
                device_data=stage_input.device, interface=stage_input.interface
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        if neighbor_data:
            display = f"```\n{neighbor_data.model_dump_json(indent=4)}\n```"
        else:
            display = f"No LLDP Data Found found on port {stage_input.device.name}:{stage_input.interface}"

        return PortLLDPInfoWorkflow.InterfaceNeighborStageOutput(
            interface_neighbor_data=neighbor_data,
            display=display,
        )

    @run_nv_config_manager_workflow
    async def run(  # type: ignore[override, ty:invalid-method-override]
        self,
        workflow_input: PortLLDPInfoInput,
    ) -> InterfaceNeighborData | None:
        """Run the workflow."""
        self.set_input(workflow_input)
        switch_port_data = await self.get_switch_port(workflow_input)
        result = await self.get_interface_neighbor_data(switch_port_data)
        await self.archive_results()
        return result.interface_neighbor_data
