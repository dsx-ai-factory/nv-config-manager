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
"""Tests for Nautobot cable-status persistence."""

from unittest.mock import AsyncMock

import pytest
from nv_config_manager_dcim import CableStatus, CableStatusUpdate, DCIMCableStatusClient
from nv_config_manager_dcim.errors import DCIMNotFoundError
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient


def _client() -> NautobotDCIMClient:
    return NautobotDCIMClient("https://nautobot.example", "token")


def _update(status: CableStatus = CableStatus.INVALID) -> CableStatusUpdate:
    return CableStatusUpdate(
        device_id="device-1",
        interface_name="Ethernet1/1",
        status=status,
        workflow_id="cable-validation-1",
    )


@pytest.mark.asyncio
async def test_update_cable_status_patches_attached_cable() -> None:
    client = _client()
    client.get = AsyncMock(
        side_effect=[
            {"results": [{"id": "interface-1", "cable": {"id": "cable-1"}}]},
            {"id": "cable-1", "status": {"name": "Connected"}},
            {"results": []},
        ]
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()

    await client.update_cable_status(_update())

    client.get.assert_any_await(
        "dcim/interfaces/",
        params={"device_id": ["device-1"], "name": "Ethernet1/1"},
    )
    client.patch.assert_awaited_once_with(
        "dcim/cables/cable-1/",
        {"status": "Invalid"},
    )
    client.post.assert_awaited_once_with(
        "extras/notes/",
        {
            "assigned_object_type": "dcim.cable",
            "assigned_object_id": "cable-1",
            "note": "Cable validation workflow cable-validation-1 set status to Invalid.",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(CableStatus))
async def test_unchanged_cable_status_skips_notes(status: CableStatus) -> None:
    client = _client()
    client.get = AsyncMock(
        side_effect=[
            {"results": [{"id": "interface-1", "cable": {"id": "cable-1"}}]},
            {"id": "cable-1", "status": {"name": status.value}},
        ]
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()

    await client.update_cable_status(_update(status))

    client.patch.assert_not_awaited()
    client.get.assert_any_await("dcim/cables/cable-1/", params={"depth": 1})
    assert client.get.await_count == 2
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_cable_status_rejects_interface_without_cable() -> None:
    client = _client()
    client.get = AsyncMock(return_value={"results": [{"id": "interface-1", "cable": None}]})

    with pytest.raises(DCIMNotFoundError, match="has no cable"):
        await client.update_cable_status(_update())


def test_nautobot_exposes_cable_status_capability() -> None:
    assert isinstance(_client(), DCIMCableStatusClient)
