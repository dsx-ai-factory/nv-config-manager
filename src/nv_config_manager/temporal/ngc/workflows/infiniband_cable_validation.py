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
"""Infiniband Cable Validation Workflow Definition."""

from datetime import timedelta
from typing import Any

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
from nv_config_manager.temporal.common.workflow_references import (
    DeviceReference,
    DeviceReferences,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.device import (
        NetworkDeviceData,
        get_device_intended_neighbors,
    )
    from nv_config_manager.temporal.ngc.activities.ufm import (
        GetUFMPortsInput,
        get_ib_ports,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[],
)

DEFAULT_IB_SWITCH_ROLES = [
    "IBLEAF",
    "IBSPINE",
]


class InfinibandCableValidationInput(BaseModel):
    """IB Cable Validation Workflow Input Definition."""

    ufm_device_id: DeviceReference = Field(
        description="Identifier of the UFM device used to inspect the InfiniBand fabric."
    )
    switch_device_ids: DeviceReferences = Field(
        description="Identifiers of the InfiniBand switches to validate."
    )


class InvalidCable(BaseModel):
    """Invalid Cable for Validation Results."""

    intended: dict | None = None
    actual: dict | None = None


class InfinibandCableValidationResult(BaseModel):
    """IB Cable Validation Result."""

    interfaces: dict[str, InvalidCable]


@workflow.defn
class InfinibandCableValidationWorkflow(WorkflowMetadataMixin, StageMixin):
    """Infiniband cable validation workflow for high-performance computing networks."""

    # Workflow metadata
    workflow_name = "InfiniBand Cable Validation"
    workflow_description = (
        "Validate Infiniband cable connections against intended topology using UFM data"
    )
    workflow_input_class = InfinibandCableValidationInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/infiniband_cable_validation"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_ufm_device",
            description="Get UFM device information from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_ib_ports_from_ufm",
            description="Get ports information from UFM API.",
            requires_approval=False,
            depends_on=["get_ufm_device"],
        )
        self.define_stage(
            name="get_switch_data",
            description="Get switch information from the DCIM.",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_intended_neighbors",
            description="Get intended neighbor information from the DCIM.",
            requires_approval=False,
            depends_on=["get_switch_data"],
        )
        self.define_stage(
            name="compare_states",
            description="Compare live state with desired state from the DCIM.",
            requires_approval=False,
            depends_on=[
                "get_ib_ports_from_ufm",
                "get_intended_neighbors",
            ],
        )

    class GetUFMDeviceStageInput(StageInput):
        """Get UFM Device Stage Input."""

        ufm_device_id: str

    class GetUFMDeviceStageOutput(StageOutput):
        """Get UFM Device Stage Output."""

        ufm_hostname: str
        site: str
        display: str

    class GetUFMPortsStageInput(StageInput):
        """Get UFM Ports Stage Input."""

        ufm_hostname: str
        site: str

    class GetUFMPortsStageOutput(StageOutput):
        """Get UFM Ports Stage Output."""

        ports_data: dict[str, list[dict]]
        display: str

    class GetSwitchDataStageInput(StageInput):
        """Get Switch Data Stage Input."""

        switch_device_ids: list[str]

    class GetSwitchDataStageOutput(StageOutput):
        """Get Switch Data Stage Output."""

        switch_data: dict[str, dict]
        switch_id_to_hostname: dict[str, str]
        display: str

    class GetIntendedNeighborsStageInput(StageInput):
        """Get Intended Neighbors Stage Input."""

        switch_data: dict[str, dict]

    class GetIntendedNeighborsStageOutput(StageOutput):
        """Get Intended Neighbors Stage Output."""

        intended_neighbors: dict[str, dict]
        display: str

    class CompareStatesStageInput(StageInput):
        """Compare States Stage Input."""

        ib_ports: dict[str, list[dict]]
        intended_neighbors: dict[str, dict]
        switch_id_to_hostname: dict[str, str]

    class CompareStatesStageOutput(StageOutput):
        """Compare States Stage Output."""

        differences: list[dict]
        display: str

    @stage_executor("get_ufm_device")
    async def get_ufm_device(self, stage_input: GetUFMDeviceStageInput) -> GetUFMDeviceStageOutput:
        """Execute UFM device lookup."""
        device_result = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(
                device_id=stage_input.ufm_device_id,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        primary_ip = device_result.device.host

        if not primary_ip:
            raise ValueError("UFM device has no primary IP address set in the DCIM.")

        return InfinibandCableValidationWorkflow.GetUFMDeviceStageOutput(
            ufm_hostname=primary_ip,
            site=device_result.device.site,
            display="UFM device information retrieved successfully.",
        )

    @stage_executor("get_ib_ports_from_ufm")
    async def get_ib_ports_from_ufm(
        self, stage_input: GetUFMPortsStageInput
    ) -> GetUFMPortsStageOutput:
        """Execute UFM ports lookup."""
        result = await workflow.execute_activity(
            get_ib_ports,
            GetUFMPortsInput(
                host=stage_input.ufm_hostname,
                unhealthy=False,
                site=stage_input.site,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        ports_by_switch: dict[str, list[dict[str, Any]]] = {}
        for port in result.ports:
            switch_name = port.get("system_name", "unknown")
            if any(role in switch_name.upper() for role in DEFAULT_IB_SWITCH_ROLES):
                if switch_name not in ports_by_switch:
                    ports_by_switch[switch_name] = []
                ports_by_switch[switch_name].append(port)

        return InfinibandCableValidationWorkflow.GetUFMPortsStageOutput(
            ports_data=ports_by_switch,
            display="IB ports data retrieved successfully from UFM.",
        )

    @stage_executor("get_switch_data")
    async def get_switch_data(
        self, stage_input: GetSwitchDataStageInput
    ) -> GetSwitchDataStageOutput:
        """Execute switch data lookup."""
        if not stage_input.switch_device_ids:
            raise ValueError("No switch device IDs provided for DCIM lookup.")

        switch_data: dict[str, dict] = {}
        switch_id_to_hostname: dict[str, str] = {}

        for device_id in stage_input.switch_device_ids:
            device_result = await workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(
                    device_id=device_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            switch_data[device_result.device.id] = device_result.device.model_dump()
            switch_id_to_hostname[device_result.device.id] = device_result.device.name

        return InfinibandCableValidationWorkflow.GetSwitchDataStageOutput(
            switch_data=switch_data,
            switch_id_to_hostname=switch_id_to_hostname,
            display="DCIM data retrieved successfully.",
        )

    @stage_executor("get_intended_neighbors")
    async def get_intended_neighbors(
        self, stage_input: GetIntendedNeighborsStageInput
    ) -> GetIntendedNeighborsStageOutput:
        """Execute intended neighbors lookup."""
        if not stage_input.switch_data:
            raise ValueError("No switch data available for intended neighbors lookup.")

        all_intended_neighbors = {}
        for switch_id, switch_data in stage_input.switch_data.items():
            device_data = NetworkDeviceData(**switch_data)
            neighbors_result = await workflow.execute_activity(
                get_device_intended_neighbors,
                device_data,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )

            for interface, neighbor in neighbors_result.neighbors.items():
                key = f"{switch_id}:{interface}"
                all_intended_neighbors[key] = neighbor.model_dump()

        return InfinibandCableValidationWorkflow.GetIntendedNeighborsStageOutput(
            intended_neighbors=all_intended_neighbors,
            display="Intended neighbors data retrieved successfully.",
        )

    @stage_executor("compare_states")
    async def compare_states(
        self, stage_input: CompareStatesStageInput
    ) -> CompareStatesStageOutput:
        """Execute state comparison."""
        differences = []

        for switch_name, ports in stage_input.ib_ports.items():
            switch_id = None
            for sid, hostname in stage_input.switch_id_to_hostname.items():
                if hostname.lower() == switch_name.lower():
                    switch_id = sid
                    break

            if not switch_id:
                continue

            for port in ports:
                port_number = port.get("port")
                port_label = port.get("label")
                logical_state = port.get("logical_state")
                physical_state = port.get("physical_state")

                if not port_number:
                    continue

                intended_connection = stage_input.intended_neighbors.get(
                    f"{switch_id}:{port_number}"
                )

                if intended_connection:
                    peer_name = port.get("peer_node_name", "")
                    peer_port = port.get("peer_port", "")

                    intended_peer = intended_connection.get("device_name", "")
                    intended_peer_port = intended_connection.get("name", "")

                    if peer_name and intended_peer:
                        if peer_name.lower() != intended_peer.lower() or str(peer_port) != str(
                            intended_peer_port
                        ):
                            differences.append(
                                {
                                    "switch_name": switch_name,
                                    "switch_port": f"{port_number} ({port_label})",
                                    "switch_state (physical/logical)": f"{physical_state} / {logical_state}",
                                    "peer_current": peer_name,
                                    "peer_port": peer_port,
                                    "peer_intended": intended_peer,
                                    "peer_port_intended": intended_peer_port,
                                    "details": f"Port {port_number} ({port_label}) on {switch_name} is connected to {peer_name}:{peer_port} instead of {intended_peer}:{intended_peer_port}",
                                }
                            )

        if not differences:
            return InfinibandCableValidationWorkflow.CompareStatesStageOutput(
                differences=[],
                display="No differences found between discovered and intended states.",
            )

        markdown = "Differences found between discovered and intended states:\n\n"
        markdown += (
            markdown_table(differences).set_params(quote=False, row_sep="markdown").get_markdown()
        )

        return InfinibandCableValidationWorkflow.CompareStatesStageOutput(
            differences=differences,
            display=markdown,
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: InfinibandCableValidationInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Execute IB cable validation workflow."""
        self.set_input(workflow_input)

        ufm_device_output = await self.get_ufm_device(
            InfinibandCableValidationWorkflow.GetUFMDeviceStageInput(
                ufm_device_id=workflow_input.ufm_device_id,
            )
        )

        ib_ports_output = await self.get_ib_ports_from_ufm(
            InfinibandCableValidationWorkflow.GetUFMPortsStageInput(
                ufm_hostname=ufm_device_output.ufm_hostname,
                site=ufm_device_output.site,
            )
        )

        switch_data_output = await self.get_switch_data(
            InfinibandCableValidationWorkflow.GetSwitchDataStageInput(
                switch_device_ids=workflow_input.switch_device_ids,
            )
        )

        neighbors_output = await self.get_intended_neighbors(
            InfinibandCableValidationWorkflow.GetIntendedNeighborsStageInput(
                switch_data=switch_data_output.switch_data,
            )
        )

        comparison_output = await self.compare_states(
            InfinibandCableValidationWorkflow.CompareStatesStageInput(
                ib_ports=ib_ports_output.ports_data,
                intended_neighbors=neighbors_output.intended_neighbors,
                switch_id_to_hostname=switch_data_output.switch_id_to_hostname,
            )
        )

        return comparison_output.display
