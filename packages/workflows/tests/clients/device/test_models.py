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

import pytest

from nv_config_manager_workflows.clients.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
    format_mac,
    is_mac_address,
)


@pytest.mark.parametrize(
    "mac",
    [
        "00:11:22:33:44:55",
        "00-11-22-33-44-55",
        "001122334455",
    ],
)
def test_is_mac_address_accepts_supported_formats(mac: str) -> None:
    assert is_mac_address(mac)


@pytest.mark.parametrize("mac", [None, "", "not-a-mac", "00:11:22:33:44"])
def test_is_mac_address_rejects_invalid_values(mac: str | None) -> None:
    assert not is_mac_address(mac)


@pytest.mark.parametrize(
    ("mac", "expected"),
    [
        ("00-11-22-33-44-55", "00:11:22:33:44:55"),
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
    ],
)
def test_format_mac_normalizes_case_and_separator(mac: str, expected: str) -> None:
    assert format_mac(mac) == expected


def test_add_entry_normalizes_and_indexes_ip_mac_and_interface() -> None:
    table = DeviceArpTable()
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")

    mac = "00-11-22-33-44-55"
    assert table.ip_to_mac == {"192.0.2.1": [mac]}
    assert table.mac_to_ip == {mac: ["192.0.2.1"]}
    assert table.interface_to_mac == {"swp1": [mac]}


def test_add_entry_does_not_duplicate_the_same_mapping() -> None:
    table = DeviceArpTable()
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")

    mac = "00-11-22-33-44-55"
    assert table.ip_to_mac["192.0.2.1"] == [mac]
    assert table.interface_to_mac["swp1"] == [mac]


def test_add_entry_raises_on_invalid_ip() -> None:
    table = DeviceArpTable()
    with pytest.raises(ValueError):
        table.add_entry("not-an-ip", "00:11:22:33:44:55", "swp1")


def test_device_models_round_trip_through_json() -> None:
    mac_entry = DeviceMacEntry(
        mac="00-11-22-33-44-55",
        interface="swp1",
        age=42,
        vlan=100,
    )
    mac_table = DeviceMacTable(
        by_mac={mac_entry.mac: mac_entry},
        by_interface={"swp1": [mac_entry.mac]},
    )
    arp_table = DeviceArpTable(
        ip_to_mac={"192.0.2.1": [mac_entry.mac]},
        mac_to_ip={mac_entry.mac: ["192.0.2.1"]},
        interface_to_mac={"swp1": [mac_entry.mac]},
    )
    neighbor = InterfaceNeighborData(
        name="Ethernet1",
        macs=[mac_entry.mac],
        device_name="leaf01",
        device_serial="serial-1",
        device_role="leaf",
        device_rack="rack-1",
        device_position=10,
        link_up=True,
        ts_info="connected",
    )
    neighbor_data = DeviceNeighborData(
        neighbors={"swp1": neighbor},
        link_states={"swp1": True},
        ts_info={"swp1": "connected"},
        ignore=["swp2"],
        link_state_only=["swp3"],
    )

    assert DeviceMacEntry.model_validate_json(mac_entry.model_dump_json()) == mac_entry
    assert DeviceMacTable.model_validate_json(mac_table.model_dump_json()) == mac_table
    assert DeviceArpTable.model_validate_json(arp_table.model_dump_json()) == arp_table
    assert InterfaceNeighborData.model_validate_json(neighbor.model_dump_json()) == neighbor
    assert DeviceNeighborData.model_validate_json(neighbor_data.model_dump_json()) == neighbor_data


def test_device_mac_entry_preserves_required_field_validation() -> None:
    with pytest.raises(ValueError):
        DeviceMacEntry.model_validate(
            {"mac": "00-11-22-33-44-55", "interface": "swp1", "age": None}
        )
