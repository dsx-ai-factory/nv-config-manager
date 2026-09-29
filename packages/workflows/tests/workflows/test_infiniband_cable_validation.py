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
"""Test Infiniband Cable Validation Workflow."""

import uuid

import pytest
from temporalio import activity
from temporalio.worker import Worker

from nv_config_manager_workflows.activities.dcim import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager_workflows.activities.device import NetworkDeviceData
from nv_config_manager_workflows.activities.ufm import (
    GetUFMPortsInput,
    GetUFMPortsOutput,
)
from nv_config_manager_workflows.workflows.infiniband_cable_validation import (
    InfinibandCableValidationInput,
    InfinibandCableValidationWorkflow,
)

UFM_PORTS = [
    {
        "port": "1",
        "label": "Port 1",
        "physical_state": "Link Up",
        "logical_state": "Active",
        "system_name": "IBLEAF1",
        "node_description": "Node 1",
        "peer_node_name": "IBSPINE1",
        "peer_node_description": "Peer Node 1",
        "peer_port": "1",
    }
]

NAUTOBOT_DEVICE = NetworkDeviceData(
    id="test-device",
    name="IBLEAF1",
    role="IBLEAF",
    platform="mlnx-os",
    site="mock_site",
    device_type="mock_device_type",
    primary_ip4="10.0.0.1",
    primary_ip6=None,
    host="10.0.0.1",
)

INTENDED_NEIGHBORS = {
    "1": {
        "device_name": "IBSPINE1",
        "name": "1",
        "role": "IBSPINE",
    }
}


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(device=NAUTOBOT_DEVICE)


@activity.defn(name="get_device_intended_neighbors")
async def mock_get_device_intended_neighbors(
    device_data: NetworkDeviceData,
) -> dict:
    return {"neighbors": INTENDED_NEIGHBORS}


@activity.defn(name="get_ib_ports")
async def mock_get_ib_ports(_activity_input: GetUFMPortsInput) -> GetUFMPortsOutput:
    return GetUFMPortsOutput(
        ports=UFM_PORTS,
        csv_data="mock UFM port data",
        display="UFM ports retrieved successfully.",
    )


@activity.defn(name="get_ib_ports")
async def mock_get_mismatched_ib_ports(
    _activity_input: GetUFMPortsInput,
) -> GetUFMPortsOutput:
    return GetUFMPortsOutput(
        ports=[
            {
                "port": "1",
                "label": "Port 1",
                "physical_state": "Link Up",
                "logical_state": "Active",
                "system_name": "IBLEAF1",
                "node_description": "Node 1",
                "peer_node_name": "IBSPINE2",
                "peer_node_description": "Peer Node 2",
                "peer_port": "1",
            }
        ],
        csv_data="mock UFM port data",
        display="UFM ports retrieved successfully.",
    )


@pytest.mark.asyncio
async def test_execute_workflow_valid_cables(env):
    """Test workflow execution with valid cable connections."""
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[InfinibandCableValidationWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_ib_ports,
            mock_get_device_intended_neighbors,
        ],
    ):
        input = InfinibandCableValidationInput(
            ufm_device_id="ufm-device",
            switch_device_ids=["test-device"],
        )

        result = await env.client.execute_workflow(
            InfinibandCableValidationWorkflow.run,
            input,
            id=str(uuid.uuid4()),
            task_queue=task_queue_name,
        )

        assert "No differences found" in result


@pytest.mark.asyncio
async def test_execute_workflow_mismatched_cables(env):
    """Test workflow execution with mismatched cable connections."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[InfinibandCableValidationWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_mismatched_ib_ports,
            mock_get_device_intended_neighbors,
        ],
    ):
        input = InfinibandCableValidationInput(
            ufm_device_id="ufm-device",
            switch_device_ids=["test-device"],
        )

        result = await env.client.execute_workflow(
            InfinibandCableValidationWorkflow.run,
            input,
            id=str(uuid.uuid4()),
            task_queue=task_queue_name,
        )

        assert "Differences found" in result
        assert "IBLEAF1" in result
        assert "Port 1" in result
        assert "IBSPINE1" in result
        assert "IBSPINE2" in result
