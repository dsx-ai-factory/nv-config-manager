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
"""Unit tests for reusable device activities."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.errors import DCIMError
from nv_config_manager_dcim.models import IntendedInterfaceNeighbor, IntendedNeighborDevice
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.device import (
    SwitchPortNeighborActivityInput,
    get_device_actual_neighbors,
    get_device_arp_table,
    get_device_intended_neighbors,
    get_device_mac_table,
    load_neighbor_data_by_switch_port,
    validate_hostname,
)
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager_workflows.runtime import (
    configure_dcim_client,
    configure_device_connection,
)


def _device(
    *,
    name: str = "leaf-1",
    primary_ip4: str | None = "192.0.2.1",
    primary_ip6: str | None = None,
) -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-1",
        name=name,
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4=primary_ip4,
        primary_ip6=primary_ip6,
    )


def _device_connection(device: NetworkDeviceData) -> MagicMock:
    connection = MagicMock(spec=NetworkConnection)
    configure_device_connection(
        lambda requested_device: (
            cast(NetworkConnection, connection) if requested_device is device else None
        )
    )
    return connection


def _dcim_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


async def test_intended_neighbors_preserve_normalization_tags_and_filtering() -> None:
    client = _dcim_client()
    client.get_intended_interface_neighbors = AsyncMock(
        return_value=[
            IntendedInterfaceNeighbor(
                name="swp1",
                tags=("cable-validation-ignore",),
                connected_interface_name="Ethernet1",
                connected_interface_mac="001122334455",
                connected_device=IntendedNeighborDevice(
                    name="spine-1",
                    serial="serial-1",
                    role="Spine Switch",
                    rack="rack-1",
                    position=42,
                ),
            ),
            IntendedInterfaceNeighbor(
                name="swp2",
                tags=("cable-validation-link-state-only",),
            ),
            IntendedInterfaceNeighbor(name="swp3"),
        ]
    )

    output = await get_device_intended_neighbors(_device())

    assert output == DeviceNeighborData(
        neighbors={
            "swp1": InterfaceNeighborData(
                name="Ethernet1",
                macs=["00-11-22-33-44-55"],
                device_name="spine-1",
                device_serial="serial-1",
                device_role="spine-switch",
                device_rack="rack-1",
                device_position=42,
            )
        },
        ignore=["swp1"],
        link_state_only=["swp2"],
    )
    client.get_intended_interface_neighbors.assert_awaited_once_with("device-1")
    client.__aenter__.assert_awaited_once()
    client.__aexit__.assert_awaited_once()


async def test_intended_neighbor_dcim_failure_is_forced_non_retryable() -> None:
    client = _dcim_client()
    provider_error = DCIMError("intended neighbors unavailable")
    client.get_intended_interface_neighbors = AsyncMock(side_effect=provider_error)

    with pytest.raises(ApplicationError) as exc_info:
        await get_device_intended_neighbors(_device())

    assert exc_info.value.message == "intended neighbors unavailable"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is provider_error


def test_actual_neighbors_filter_only_empty_entries_and_preserve_metadata() -> None:
    device = _device()
    connection = _device_connection(device)
    populated_by_name = InterfaceNeighborData(name="Ethernet1")
    populated_by_device = InterfaceNeighborData(device_name="spine-2")
    populated_by_mac = InterfaceNeighborData(macs=["00-11-22-33-44-55"])
    connection.get_interface_connections.return_value = DeviceNeighborData(
        neighbors={
            "swp1": populated_by_name,
            "swp2": populated_by_device,
            "swp3": populated_by_mac,
            "swp4": InterfaceNeighborData(),
        },
        link_states={"swp1": True, "swp4": False},
        ts_info={"swp4": "Cable is unplugged."},
        ignore=["swp5"],
        link_state_only=["swp4"],
    )

    output = get_device_actual_neighbors(device)

    assert output.neighbors == {
        "swp1": populated_by_name,
        "swp2": populated_by_device,
        "swp3": populated_by_mac,
    }
    assert output.link_states == {"swp1": True, "swp4": False}
    assert output.ts_info == {"swp4": "Cable is unplugged."}
    assert output.ignore == ["swp5"]
    assert output.link_state_only == ["swp4"]
    connection.close.assert_called_once_with()


def test_mac_and_arp_activities_return_provider_results_unchanged() -> None:
    device = _device()
    connection = _device_connection(device)
    mac_table = DeviceMacTable(
        by_mac={
            "00-11-22-33-44-55": DeviceMacEntry(
                mac="00-11-22-33-44-55",
                interface="swp1",
                age=10,
                vlan=100,
            )
        },
        by_interface={"swp1": ["00-11-22-33-44-55"]},
    )
    arp_table = DeviceArpTable(
        ip_to_mac={"192.0.2.10": ["00-11-22-33-44-55"]},
        mac_to_ip={"00-11-22-33-44-55": ["192.0.2.10"]},
        interface_to_mac={"swp1": ["00-11-22-33-44-55"]},
    )
    connection.get_mac_table.return_value = mac_table
    connection.get_arp_table.return_value = arp_table

    assert get_device_mac_table(device) is mac_table
    assert get_device_arp_table(device) is arp_table
    assert connection.close.call_count == 2


def test_device_connection_is_closed_when_operation_fails() -> None:
    device = _device()
    connection = _device_connection(device)
    connection.get_mac_table.side_effect = RuntimeError("device unavailable")

    with pytest.raises(RuntimeError, match="device unavailable"):
        get_device_mac_table(device)

    connection.close.assert_called_once_with()


def test_validate_hostname_is_case_insensitive_and_preserves_returned_value() -> None:
    device = _device(name="LEAF-1")
    connection = _device_connection(device)
    connection.get_hostname.return_value = "leaf-1"

    output = validate_hostname(device)

    assert output.hostname == "leaf-1"
    connection.close.assert_called_once_with()


def test_validate_hostname_mismatch_preserves_address_message_and_retryability() -> None:
    device = _device(name="leaf-1", primary_ip4=None, primary_ip6="2001:db8::1")
    connection = _device_connection(device)
    connection.get_hostname.return_value = "leaf-2"

    with pytest.raises(ApplicationError) as exc_info:
        validate_hostname(device)

    assert exc_info.value.message == (
        "Hostname on 2001:db8::1 (leaf-2) does not match the DCIM record (leaf-1)."
    )
    assert exc_info.value.non_retryable is True
    connection.close.assert_called_once_with()


def test_switch_port_neighbor_lookup_preserves_interface_and_optional_result() -> None:
    device = _device()
    connection = _device_connection(device)
    neighbor = InterfaceNeighborData(name="Ethernet1", device_name="spine-1")
    connection.get_lldp_data.side_effect = [neighbor, None]
    activity_input = SwitchPortNeighborActivityInput(device_data=device, interface="swp1")

    assert load_neighbor_data_by_switch_port(activity_input) is neighbor
    assert load_neighbor_data_by_switch_port(activity_input) is None
    assert connection.get_lldp_data.call_args_list[0].args == ("swp1",)
    assert connection.get_lldp_data.call_args_list[1].args == ("swp1",)
    assert connection.close.call_count == 2
