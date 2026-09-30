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
from configparser import ConfigParser
from datetime import timedelta
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.activities.ufm import get_ib_ports
from nv_config_manager.temporal.ngc.workflows.infiniband_get_unhealthy_ports import (
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


def _create_config(sections: dict[str, dict[str, str]]) -> ConfigParser:
    """Create a ConfigParser from a dict of sections."""
    config = ConfigParser()
    for section, values in sections.items():
        config.add_section(section)
        for key, value in values.items():
            config.set(section, key, value)
    return config


@pytest.fixture(autouse=True)
def reset_secrets_cache():
    """Clear the secrets config cache before and after each test."""
    clear_secrets_cache()
    yield
    clear_secrets_cache()


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


@pytest.mark.asyncio
async def test_execute_workflow_healthy_ports(env):
    """Test workflow execution with healthy ports."""
    task_queue_name = str(uuid.uuid4())

    with patch("nv_config_manager.common.config.loader.load_config") as mock_config:
        mock_config.return_value = _create_config(
            {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
        )

        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[InfinibandGetUnhealthyPortsWorkflow],
            activities=[mock_get_network_device, get_ib_ports],
        ):
            with aioresponses() as m:
                m.get(
                    "https://10.0.0.1/ufmRest/resources/ports",
                    status=200,
                    payload=UFM_HEALTHY_PORTS,
                )

                input = InfinibandGetUnhealthyPortsInput(
                    device_id="test-device",
                )
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
    task_queue_name = str(uuid.uuid4())

    with patch("nv_config_manager.common.config.loader.load_config") as mock_config:
        mock_config.return_value = _create_config(
            {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
        )

        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[InfinibandGetUnhealthyPortsWorkflow],
            activities=[mock_get_network_device, get_ib_ports],
        ):
            with aioresponses() as m:
                m.get(
                    "https://10.0.0.1/ufmRest/resources/ports",
                    status=200,
                    payload=UFM_UNHEALTHY_PORTS,
                )

                input = InfinibandGetUnhealthyPortsInput(
                    device_id="test-device",
                )
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
