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
"""Replay contracts for the extracted UFM and InfiniBand GUID activities."""

from pathlib import Path

import pytest
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.ib_port_guid_discovery import (
    IBPortGuidDiscoveryWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_get_unhealthy_ports import (
    InfinibandGetUnhealthyPortsWorkflow,
)
from nv_config_manager_workflows.activities.dcim import GetNetworkDeviceInput
from nv_config_manager_workflows.activities.ib_guid_discovery import (
    DiscoverIBPortGuidsInput,
    SyncIBGuidInput,
)
from nv_config_manager_workflows.activities.ufm import GetUFMPortsInput

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _history(filename: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (_FIXTURE_ROOT / filename).read_text(),
    )


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_class", "history_filename"),
    [
        (InfinibandGetUnhealthyPortsWorkflow, "infiniband_get_unhealthy_ports.json"),
        (IBPortGuidDiscoveryWorkflow, "ib_port_guid_discovery.json"),
    ],
)
async def test_infiniband_discovery_history_replays(
    workflow_class: type,
    history_filename: str,
) -> None:
    """The extracted activity objects emit the commands recorded by these workflows."""
    replayer = Replayer(
        workflows=[workflow_class],
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
async def test_get_ib_ports_history_preserves_activity_names_and_arguments() -> None:
    scheduled = _scheduled_activities(_history("infiniband_get_unhealthy_ports.json"))
    assert [item.activity_type.name for item in scheduled] == [
        "get_network_device",
        "get_ib_ports",
    ]

    converter = get_data_converter()
    assert await converter.decode(scheduled[0].input.payloads, [GetNetworkDeviceInput]) == [
        GetNetworkDeviceInput(device_id="captured-ufm")
    ]
    assert await converter.decode(scheduled[1].input.payloads, [GetUFMPortsInput]) == [
        GetUFMPortsInput(host="192.0.2.10/32", unhealthy=True, site="captured-site")
    ]


@pytest.mark.asyncio
async def test_ib_guid_history_preserves_activity_names_and_arguments() -> None:
    scheduled = _scheduled_activities(_history("ib_port_guid_discovery.json"))
    assert [item.activity_type.name for item in scheduled] == [
        "get_network_device",
        "discover_ib_port_guids",
        "sync_ib_guid_on_interface",
    ]

    converter = get_data_converter()
    assert await converter.decode(scheduled[0].input.payloads, [GetNetworkDeviceInput]) == [
        GetNetworkDeviceInput(device_id="captured-ufm")
    ]
    assert await converter.decode(scheduled[1].input.payloads, [DiscoverIBPortGuidsInput]) == [
        DiscoverIBPortGuidsInput(
            ufm_host="192.0.2.10/32",
            site="captured-site",
            switch_device_ids=["captured-switch"],
        )
    ]
    assert await converter.decode(scheduled[2].input.payloads, [SyncIBGuidInput]) == [
        SyncIBGuidInput(
            interface_id="captured-interface",
            guid="0011223344556677",
            dry_run=True,
        )
    ]
