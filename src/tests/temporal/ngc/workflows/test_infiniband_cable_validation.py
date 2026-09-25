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
from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio import activity
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.activities.ufm import get_ib_ports
from nv_config_manager.temporal.ngc.workflows.infiniband_cable_validation import (
    InfinibandCableValidationInput,
    InfinibandCableValidationWorkflow,
)

UFM_PORTS = [
    {
        "number": "1",
        "label": "Port 1",
        "physical_state": "Link Up",
        "logical_state": "Active",
        "system_name": "IBLEAF1",
        "node_description": "Node 1",
        "peer_node_name": "IBSPINE1",
        "peer_node_description": "Peer Node 1",
        "peer_port_dname": "1",
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
    return GetNetworkDeviceOutput(device=NAUTOBOT_DEVICE)


@activity.defn(name="get_device_intended_neighbors")
async def mock_get_device_intended_neighbors(
    device_data: NetworkDeviceData,
) -> dict:
    return {"neighbors": INTENDED_NEIGHBORS}


@pytest.mark.asyncio
async def test_execute_workflow_valid_cables(env):
    """Test workflow execution with valid cable connections."""
    task_queue_name = str(uuid.uuid4())

    with patch("nv_config_manager.common.config.loader.load_config") as mock_config:
        mock_config.return_value = _create_config(
            {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
        )

        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[InfinibandCableValidationWorkflow],
            activities=[
                mock_get_network_device,
                get_ib_ports,
                mock_get_device_intended_neighbors,
            ],
        ):
            with aioresponses() as m:
                m.get(
                    "https://10.0.0.1/ufmRest/resources/ports",
                    status=200,
                    payload=UFM_PORTS,
                )

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

    mismatched_ports = [
        {
            "number": "1",
            "label": "Port 1",
            "physical_state": "Link Up",
            "logical_state": "Active",
            "system_name": "IBLEAF1",
            "node_description": "Node 1",
            "peer_node_name": "IBSPINE2",
            "peer_node_description": "Peer Node 2",
            "peer_port_dname": "1",
        }
    ]

    with patch("nv_config_manager.common.config.loader.load_config") as mock_config:
        mock_config.return_value = _create_config(
            {"ufm": {"ufm_api_user": "user", "ufm_api_token_r1": "pass"}}
        )

        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[InfinibandCableValidationWorkflow],
            activities=[
                mock_get_network_device,
                get_ib_ports,
                mock_get_device_intended_neighbors,
            ],
        ):
            with aioresponses() as m:
                m.get(
                    "https://10.0.0.1/ufmRest/resources/ports",
                    status=200,
                    payload=mismatched_ports,
                )

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
