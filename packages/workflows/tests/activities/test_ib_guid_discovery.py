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
"""Unit tests for reusable InfiniBand GUID discovery activities."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.models import IBInterfaceGuid, IBNeighbor, IBSwitchTopology
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_guid_discovery import (
    DiscoverIBPortGuidsInput,
    IBGuidMapping,
    SyncIBGuidInput,
    compute_guid_mappings,
    discover_ib_port_guids,
    helpers,
    sync_ib_guid_on_interface,
)
from nv_config_manager_workflows.clients.ufm import UFMClient
from nv_config_manager_workflows.runtime import configure_dcim_client, configure_ufm_client

SWITCH_ID = "switch-1"
SWITCH_NAME = "ib-leaf-01"
COMPUTE_DEVICE = "compute-01"
COMPUTE_INTERFACE = "mlx5_0"
INTERFACE_ID = "interface-1"
GUID_A = "0x0002c903000a0a01"
GUID_B = "0x0002c903000a0a02"


def _dcim_client() -> MagicMock:
    client = MagicMock(spec=DCIMClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_ib_switch_topology = AsyncMock()
    client.get_ib_interface_guids = AsyncMock()
    client.get_ib_interface_guid = AsyncMock()
    client.set_ib_interface_guid = AsyncMock()
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


def _ufm_client() -> MagicMock:
    client = MagicMock(spec=UFMClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_ports = AsyncMock()
    configure_ufm_client(lambda _host, _site: cast(UFMClient, client))
    return client


def _ib_port(
    *,
    guid: str = GUID_A,
    peer_switch: str = SWITCH_NAME,
    peer_port: str = "10",
    system_name: str = "mlx5-system-01",
) -> dict[str, str]:
    return {
        "system_name": system_name,
        "port": "1",
        "guid": guid,
        "peer_node_name": peer_switch,
        "peer_port": peer_port,
    }


def _neighbors() -> dict[str, dict[str, dict[str, str]]]:
    return {
        SWITCH_ID: {
            "10": {"device_name": COMPUTE_DEVICE, "name": COMPUTE_INTERFACE},
        }
    }


def _interface_cache(guid: str = "") -> dict[tuple[str, str], dict[str, str]]:
    return {
        (COMPUTE_DEVICE.lower(), COMPUTE_INTERFACE): {
            "id": INTERFACE_ID,
            "ib_guid": guid,
        }
    }


@pytest.mark.parametrize(
    ("current", "discovered", "expected"),
    [
        ("anything", "", "skip"),
        (GUID_A, GUID_A.upper(), "noop"),
        ("", GUID_A, "set"),
        (GUID_A, GUID_B, "update"),
    ],
)
def test_mapping_action_classification(current: str, discovered: str, expected: str) -> None:
    assert IBGuidMapping._classify_action(current, discovered) == expected


def test_neighbor_helpers_preserve_connected_interfaces_and_skip_incomplete_entries() -> None:
    connected = {
        "name": "swp1s1",
        "connected_interface": {
            "name": "eth0",
            "device": {"name": COMPUTE_DEVICE},
        },
    }
    incomplete = {
        "name": "swp2",
        "connected_interface": {"name": "", "device": {"name": COMPUTE_DEVICE}},
    }

    assert helpers._intended_neighbor_from_interface(connected) == (
        "swp1s1",
        {"device_name": COMPUTE_DEVICE, "name": "eth0"},
    )
    assert helpers._intended_neighbor_from_interface(incomplete) is None
    assert helpers._neighbors_for_switch_device({"interfaces": [connected, incomplete]}) == {
        "swp1s1": {"device_name": COMPUTE_DEVICE, "name": "eth0"}
    }


@pytest.mark.parametrize(
    ("current_guid", "expected_action"),
    [("", "set"), (GUID_A, "noop"), (GUID_B, "update")],
)
def test_compute_guid_mappings_classifies_resolved_interfaces(
    current_guid: str, expected_action: str
) -> None:
    mappings = compute_guid_mappings(
        ib_ports=[_ib_port()],
        switch_id_to_name={SWITCH_ID: SWITCH_NAME},
        neighbors_by_switch_id=_neighbors(),
        nautobot_interface_by_dev_iface=_interface_cache(current_guid),
    )

    assert len(mappings) == 1
    assert mappings[0].action == expected_action
    assert mappings[0].device_name == COMPUTE_DEVICE
    assert mappings[0].interface_name == COMPUTE_INTERFACE
    assert mappings[0].interface_id == INTERFACE_ID
    assert mappings[0].discovered_guid == GUID_A
    assert mappings[0].current_guid == current_guid


def test_compute_guid_mappings_preserves_skip_rules_and_result_order() -> None:
    mappings = compute_guid_mappings(
        ib_ports=[
            _ib_port(guid=""),
            _ib_port(system_name=SWITCH_NAME),
            _ib_port(peer_switch="unmanaged-switch"),
            _ib_port(peer_port="99"),
            _ib_port(),
        ],
        switch_id_to_name={SWITCH_ID: SWITCH_NAME},
        neighbors_by_switch_id=_neighbors(),
        nautobot_interface_by_dev_iface={},
    )

    assert [mapping.action for mapping in mappings] == ["skip", "skip"]
    assert "No cable modeled" in mappings[0].reason
    assert "not found" in mappings[1].reason


async def test_discover_guid_activity_uses_runtime_providers_and_preserves_shape() -> None:
    ufm = _ufm_client()
    dcim = _dcim_client()
    ufm.get_ports.return_value = [_ib_port()]
    dcim.get_ib_switch_topology.return_value = IBSwitchTopology(
        switch_names={SWITCH_ID: SWITCH_NAME},
        intended_neighbors={
            SWITCH_ID: {
                "10": IBNeighbor(
                    device_name=COMPUTE_DEVICE,
                    interface_name=COMPUTE_INTERFACE,
                )
            }
        },
    )
    dcim.get_ib_interface_guids.return_value = [
        IBInterfaceGuid(
            interface_id=INTERFACE_ID,
            device_name=COMPUTE_DEVICE,
            interface_name=COMPUTE_INTERFACE,
            guid="",
        )
    ]

    output = await discover_ib_port_guids(
        DiscoverIBPortGuidsInput(
            ufm_host="ufm.example.test",
            site="site-1",
            switch_device_ids=[SWITCH_ID],
        )
    )

    assert [mapping.action for mapping in output.mappings] == ["set"]
    assert output.display == (
        "Discovered 1 IB port/interface mappings (set=1, update=0, noop=0, skip=0)"
    )
    ufm.get_ports.assert_awaited_once_with(unhealthy_only=False)
    dcim.get_ib_switch_topology.assert_awaited_once_with([SWITCH_ID])
    dcim.get_ib_interface_guids.assert_awaited_once_with({(COMPUTE_DEVICE, COMPUTE_INTERFACE)})
    ufm.__aenter__.assert_awaited_once()
    ufm.__aexit__.assert_awaited_once()
    dcim.__aenter__.assert_awaited_once()
    dcim.__aexit__.assert_awaited_once()


async def test_discover_rejects_empty_switch_list_after_reading_ufm() -> None:
    ufm = _ufm_client()
    dcim = _dcim_client()
    ufm.get_ports.return_value = []

    with pytest.raises(ApplicationError) as exc_info:
        await discover_ib_port_guids(
            DiscoverIBPortGuidsInput(
                ufm_host="ufm.example.test",
                switch_device_ids=[],
            )
        )

    assert exc_info.value.message == "No switch_device_ids provided, cannot resolve topology."
    assert exc_info.value.non_retryable is True
    ufm.get_ports.assert_awaited_once_with(unhealthy_only=False)
    dcim.__aenter__.assert_not_awaited()


@pytest.mark.parametrize(
    ("interface_id", "guid", "message"),
    [("", GUID_A, "interface_id is required"), (INTERFACE_ID, "", "guid is required")],
)
async def test_sync_rejects_missing_values_without_opening_dcim(
    interface_id: str, guid: str, message: str
) -> None:
    dcim = _dcim_client()

    with pytest.raises(ApplicationError) as exc_info:
        await sync_ib_guid_on_interface(
            SyncIBGuidInput(interface_id=interface_id, guid=guid, dry_run=False)
        )

    assert exc_info.value.message == message
    assert exc_info.value.non_retryable is True
    dcim.__aenter__.assert_not_awaited()


async def test_sync_noop_is_case_insensitive_and_preserves_current_guid() -> None:
    dcim = _dcim_client()
    dcim.get_ib_interface_guid.return_value = IBInterfaceGuid(
        interface_id=INTERFACE_ID,
        device_name=COMPUTE_DEVICE,
        interface_name=COMPUTE_INTERFACE,
        guid=GUID_A.upper(),
    )

    output = await sync_ib_guid_on_interface(
        SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=False)
    )

    assert output.changed is False
    assert output.new_guid == GUID_A.upper()
    assert output.reason == "ib_guid already up to date"
    dcim.set_ib_interface_guid.assert_not_awaited()


async def test_sync_dry_run_reports_change_without_writing() -> None:
    dcim = _dcim_client()
    dcim.get_ib_interface_guid.return_value = IBInterfaceGuid(
        interface_id=INTERFACE_ID,
        device_name=COMPUTE_DEVICE,
        interface_name=COMPUTE_INTERFACE,
        guid=GUID_B,
    )

    output = await sync_ib_guid_on_interface(
        SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=True)
    )

    assert output.changed is False
    assert output.previous_guid == GUID_B
    assert output.new_guid == GUID_A
    assert output.reason == "dry_run=True; no write performed"
    dcim.set_ib_interface_guid.assert_not_awaited()


async def test_sync_changed_guid_writes_once_and_preserves_output() -> None:
    dcim = _dcim_client()
    dcim.get_ib_interface_guid.return_value = IBInterfaceGuid(
        interface_id=INTERFACE_ID,
        device_name=COMPUTE_DEVICE,
        interface_name=COMPUTE_INTERFACE,
        guid=GUID_B,
    )

    output = await sync_ib_guid_on_interface(
        SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=False)
    )

    assert output.changed is True
    assert output.dry_run is False
    assert output.previous_guid == GUID_B
    assert output.new_guid == GUID_A
    assert output.device_name == COMPUTE_DEVICE
    assert output.interface_name == COMPUTE_INTERFACE
    dcim.set_ib_interface_guid.assert_awaited_once_with(INTERFACE_ID, GUID_A)
