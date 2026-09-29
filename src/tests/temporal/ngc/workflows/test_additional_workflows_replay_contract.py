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
"""Replay contracts for small inventory and Hello World workflows."""

from pathlib import Path
from typing import Any

import pytest
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.hello_world.workflows.hello_world_workflow import HelloWorld
from nv_config_manager.temporal.ngc.workflows.connected_host import (
    ConnectedHostMetadataWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_cable_validation import (
    InfinibandCableValidationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.lldp import PortLLDPInfoWorkflow

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_HELLO_WORLD_FIXTURE_ROOT = Path(__file__).parents[2] / "hello_world" / "workflows" / "fixtures"
_WORKFLOWS = [
    ConnectedHostMetadataWorkflow,
    HelloWorld,
    InfinibandCableValidationWorkflow,
    PortLLDPInfoWorkflow,
]
_EXPECTED_ACTIVITY_NAMES = {
    "connected_host_metadata.json": [
        "get_network_device",
        "get_device_mac_table",
        "get_network_device",
        "get_device_actual_neighbors",
        "get_host_data_by_macs",
        "get_host_data_by_names",
        "publish_nats",
    ],
    "hello_world.json": ["hello_world_activity"],
    "infiniband_cable_validation.json": [
        "get_network_device",
        "get_ib_ports",
        "get_network_device",
        "get_device_intended_neighbors",
    ],
    "port_lldp_info.json": [
        "get_switch_port_by_remote_mac_address",
        "load_neighbor_data_by_switch_port",
        "publish_nats",
    ],
}


def _history(filename: str) -> WorkflowHistory:
    fixture_root = _HELLO_WORLD_FIXTURE_ROOT if filename == "hello_world.json" else _FIXTURE_ROOT
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (fixture_root / filename).read_text(),
    )


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def _scheduled_inputs(filename: str) -> list[tuple[str, Any]]:
    converter = get_data_converter()
    scheduled = []
    for attributes in _scheduled_activities(_history(filename)):
        payloads = await converter.decode(attributes.input.payloads)
        scheduled.append(
            (
                attributes.activity_type.name,
                payloads[0] if payloads else None,
            )
        )
    return scheduled


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_main_history_replays(history_filename: str) -> None:
    """Activity extraction remains deterministic against histories captured from main."""
    replayer = Replayer(
        workflows=_WORKFLOWS,
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_main_history_preserves_activity_order(history_filename: str) -> None:
    """The fixtures independently freeze the scheduled activity type names."""
    scheduled = await _scheduled_inputs(history_filename)

    assert [name for name, _ in scheduled] == _EXPECTED_ACTIVITY_NAMES[history_filename]


@pytest.mark.asyncio
async def test_main_histories_preserve_activity_arguments() -> None:
    """The captured paths retain their serialized inventory and greeting arguments."""
    hello_world = await _scheduled_inputs("hello_world.json")
    connected_host = await _scheduled_inputs("connected_host_metadata.json")
    lldp = await _scheduled_inputs("port_lldp_info.json")
    infiniband = await _scheduled_inputs("infiniband_cable_validation.json")

    assert hello_world[0] == ("hello_world_activity", "replay-user")
    assert connected_host[0][1] == {"device_id": "switch-1"}
    assert connected_host[1][1]["id"] == "switch-1"
    assert connected_host[4] == ("get_host_data_by_macs", ["00:00:00:00:00:01"])
    assert connected_host[5] == ("get_host_data_by_names", ["host-1"])
    assert lldp[0] == (
        "get_switch_port_by_remote_mac_address",
        {"remote_mac_address": "00:00:00:00:00:01"},
    )
    assert lldp[1][1]["device_data"]["id"] == "switch-1"
    assert lldp[1][1]["interface"] == "swp1"
    assert infiniband[0][1] == {"device_id": "ufm-1"}
    assert infiniband[1][1] == {
        "host": "10.0.0.1",
        "site": "replay-site",
        "unhealthy": False,
    }
    assert infiniband[2][1] == {"device_id": "switch-1"}
    assert infiniband[3][1]["id"] == "switch-1"
