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
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.worker import Worker

from nv_config_manager.temporal.client.device import InterfaceNeighborData
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.device import SwitchPortNeighborActivityInput
from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    SwitchPortByMacActivityInput,
    SwitchPortByMacActivityOutput,
)
from nv_config_manager.temporal.ngc.workflows.lldp import PortLLDPInfoInput, PortLLDPInfoWorkflow

MOCK_NEIGHBOR_DATA = InterfaceNeighborData(
    name="swp1",
    macs=[],
    device_name="mock_device",
    device_role="mock_role",
    device_serial="mock_serial",
    link_up=True,
)


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="mock_device",
            role="mock_role",
            platform="cumulus-linux",
            site="SITEA",
            device_type="sn4200",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
            render_enabled=False,
            deploy_enabled=False,
            backup_enabled=False,
            ztp_enabled=False,
            intent=None,
        )
    )


@activity.defn(name="get_switch_port_by_remote_mac_address")
async def mock_get_switch_port_by_remote_mac_address(
    input: SwitchPortByMacActivityInput,
) -> str:
    device = await mock_get_network_device(GetNetworkDeviceInput(device_id="mock_device_id"))
    return SwitchPortByMacActivityOutput(
        device=device.device,
        interface="swp1",
    )


@activity.defn(name="load_neighbor_data_by_switch_port")
async def mock_load_neighbor_data_by_switch_port(
    input: SwitchPortNeighborActivityInput,
) -> InterfaceNeighborData:
    return MOCK_NEIGHBOR_DATA


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow(
    mock_time,
    mock_nats_client,
    env,
):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[PortLLDPInfoWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_switch_port_by_remote_mac_address,
            mock_load_neighbor_data_by_switch_port,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        # By MAC Address
        input = PortLLDPInfoInput(remote_mac_address="mock_mac")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            PortLLDPInfoWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=1),
        )
        result = await handle.result()
        assert result == MOCK_NEIGHBOR_DATA

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the switch port by MAC address from the DCIM",
                "execution_time": 0.0,
                "input": {
                    "device_id": None,
                    "interface": None,
                    "remote_mac_address": "mock_mac",
                },
                "name": "get_switch_port",
                "output": {
                    "device": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_id/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_id",
                        "intended_config_path": "mock_device_id/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_id/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "mock_device:swp1",
                    "interface": "swp1",
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["get_switch_port"],
                "description": "Get Switch Port Neighbor Data",
                "execution_time": 0.0,
                "input": {
                    "device": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_id/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_id",
                        "intended_config_path": "mock_device_id/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_id/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "mock_device:swp1",
                    "interface": "swp1",
                },
                "name": "get_interface_neighbor_data",
                "output": {
                    "display": '```\n{\n    "name": "swp1",\n    "macs": [],\n    "device_name": "mock_device",\n    "device_serial": "mock_serial",\n    "device_role": "mock_role",\n    "device_rack": null,\n    "device_position": null,\n    "link_up": true,\n    "ts_info": null\n}\n```',
                    "interface_neighbor_data": {
                        "device_name": "mock_device",
                        "device_role": "mock_role",
                        "device_serial": "mock_serial",
                        "device_rack": None,
                        "device_position": None,
                        "link_up": True,
                        "macs": [],
                        "name": "swp1",
                        "ts_info": None,
                    },
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
        ]

        # By Switch and Port
        input = PortLLDPInfoInput(device_id="mock_device_id", interface="swp1")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            PortLLDPInfoWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=1),
        )
        result = await handle.result()
        assert result == MOCK_NEIGHBOR_DATA

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the switch port by MAC address from the DCIM",
                "execution_time": 0.0,
                "input": {
                    "device_id": "mock_device_id",
                    "interface": "swp1",
                    "remote_mac_address": None,
                },
                "name": "get_switch_port",
                "output": {
                    "device": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_id/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_id",
                        "intended_config_path": "mock_device_id/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_id/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "mock_device:swp1",
                    "interface": "swp1",
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["get_switch_port"],
                "description": "Get Switch Port Neighbor Data",
                "execution_time": 0.0,
                "input": {
                    "device": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_id/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_id",
                        "intended_config_path": "mock_device_id/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_id/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "mock_device:swp1",
                    "interface": "swp1",
                },
                "name": "get_interface_neighbor_data",
                "output": {
                    "display": "```\n"
                    "{\n"
                    '    "name": "swp1",\n'
                    '    "macs": [],\n'
                    '    "device_name": "mock_device",\n'
                    '    "device_serial": "mock_serial",\n'
                    '    "device_role": "mock_role",\n'
                    '    "device_rack": null,\n'
                    '    "device_position": null,\n'
                    '    "link_up": true,\n'
                    '    "ts_info": null\n'
                    "}\n"
                    "```",
                    "interface_neighbor_data": {
                        "device_name": "mock_device",
                        "device_role": "mock_role",
                        "device_serial": "mock_serial",
                        "device_rack": None,
                        "device_position": None,
                        "link_up": True,
                        "macs": [],
                        "name": "swp1",
                        "ts_info": None,
                    },
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
        ]
