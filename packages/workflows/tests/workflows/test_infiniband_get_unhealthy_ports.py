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
"""Test Infiniband Unhealthy Ports Workflow."""

import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
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
from nv_config_manager_workflows.workflows.infiniband_get_unhealthy_ports import (
    InfinibandGetUnhealthyPortsInput,
    InfinibandGetUnhealthyPortsWorkflow,
)

UFM_HEALTHY_PORTS = [
    {
        "number": "1",
        "label": "Port 1",
        "physical_state": "Link Up",
        "logical_state": "Active",
        "system_name": "System1",
        "node_description": "Node 1",
        "peer_node_name": "Peer1",
        "peer_node_description": "Peer Node 1",
    }
]

UFM_UNHEALTHY_PORTS = [
    {
        "number": "1",
        "label": "Port 1",
        "physical_state": "Link Down",
        "logical_state": "Inactive",
        "system_name": "System1",
        "node_description": "Node 1",
        "peer_node_name": "Peer1",
        "peer_node_description": "Peer Node 1",
    }
]


_ufm_ports = UFM_HEALTHY_PORTS


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="mock_device",
            role="mock_role",
            platform="mlnx-os",
            site="mock_site",
            device_type="mock_device_type",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
            host="10.0.0.1",
        )
    )


@activity.defn(name="get_ib_ports")
async def mock_get_ib_ports(_activity_input: GetUFMPortsInput) -> GetUFMPortsOutput:
    return GetUFMPortsOutput(
        ports=_ufm_ports,
        csv_data="mock UFM port data",
        display="UFM ports retrieved successfully.",
    )


@pytest.mark.asyncio
async def test_execute_workflow_healthy_ports(env):
    """Test workflow execution with healthy ports."""
    global _ufm_ports  # noqa: PLW0603
    _ufm_ports = []
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[InfinibandGetUnhealthyPortsWorkflow],
        activities=[mock_get_network_device, mock_get_ib_ports],
    ):
        input = InfinibandGetUnhealthyPortsInput(device_id="test-device")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            InfinibandGetUnhealthyPortsWorkflow.run,
            input.model_dump(),
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result == "No unhealthy ports found in the network fabric."


@pytest.mark.asyncio
async def test_execute_workflow_unhealthy_ports(env):
    """Test workflow execution with unhealthy ports."""
    global _ufm_ports  # noqa: PLW0603
    _ufm_ports = UFM_UNHEALTHY_PORTS
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[InfinibandGetUnhealthyPortsWorkflow],
        activities=[mock_get_network_device, mock_get_ib_ports],
    ):
        input = InfinibandGetUnhealthyPortsInput(device_id="test-device")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            InfinibandGetUnhealthyPortsWorkflow.run,
            input.model_dump(),
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert "Unhealthy ports found in the network fabric" in result
        assert "Export to CSV" in result
        assert "Port 1" in result
        assert "Link Down" in result
        assert "Inactive" in result
