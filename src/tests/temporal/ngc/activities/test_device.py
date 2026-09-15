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
"""Device Activity Test Suite."""

import datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
import requests
import responses
from aioresponses import aioresponses
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tests.temporal.ngc.activities.test_device_data import (
    ARISTA_ARP_TABLE,
    ARISTA_HOSTNAME,
    ARISTA_INTERFACE_STATUS,
    ARISTA_LLDP_NEIGHBORS,
    ARISTA_MAC_TABLE,
    CUMULUS_ARP_TABLE,
    CUMULUS_BRIDGE_DOMAINS,
    CUMULUS_INTERFACES,
    CUMULUS_INTERFACES_MINIMAL,
    CUMULUS_MAC_TABLE,
    CUMULUS_MAC_TABLE_DUPS,
    CUMULUS_SYSTEM,
    INTENDED_NEIGHBORS_V2,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_dcim_nautobot_2x.workflow import NetworkDeviceData

    from nv_config_manager.temporal.client.device import (
        AristaConnection,
        CumulusConnection,
        DeviceArpTable,
        DeviceMacEntry,
        DeviceMacTable,
        DeviceNeighborData,
        InterfaceNeighborData,
        NetworkDeviceException,
    )
    from nv_config_manager.temporal.ngc.activities.device import (
        get_device_actual_neighbors,
        get_device_arp_table,
        get_device_intended_neighbors,
        get_device_mac_table,
        validate_hostname,
    )


@responses.activate
def test_get_device_actual_neighbors_cumulus():
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface?include=/*/link/state&include=/*/link/oper-status&include=/*/type",
        json={
            interface: {
                "type": data["type"],
                "link": {
                    "state": data["link"]["state"],
                },
            }
            for interface, data in CUMULUS_INTERFACES.items()
        },
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface?include=/*/lldp/neighbor",
        json={
            interface: {
                "lldp": {
                    "neighbor": data.get("lldp", {}).get("neighbor"),
                },
            }
            for interface, data in CUMULUS_INTERFACES.items()
        },
    )

    for interface, data in CUMULUS_INTERFACES.items():
        responses.add(
            responses.GET,
            f"https://192.0.2.1:8765/nvue_v1/interface/{interface}?include=/link/troubleshooting-info",
            json={"link": {"troubleshooting-info": data["link"].get("troubleshooting-info")}},
        )

    activity_input = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )
    result = get_device_actual_neighbors(activity_input)
    assert result == DeviceNeighborData(
        neighbors={
            "eth0": InterfaceNeighborData(
                name="Ethernet2",
                device_name="rno1-m04-C10-leaf1.smn.lab1",
                device_serial=None,
            ),
        },
        link_states={
            "eth0": True,
            "swp1": False,
            "swp10": False,
            "swp11": False,
            "swp12": False,
        },
        ts_info={
            # "swp1": "Cable is unplugged.",
            # "swp10": "Cable is unplugged.",
            # "swp11": "Cable is unplugged.",
            # "swp12": "Cable is unplugged.",
        },
    )


def test_get_device_actual_neighbors_arista():
    def mock_enable(cmd, encoding):
        if cmd == "show lldp neighbors detail" and encoding == "json":
            return ARISTA_LLDP_NEIGHBORS
        elif cmd == "show interfaces status" and encoding == "json":
            return ARISTA_INTERFACE_STATUS
        return None

    mock_node = MagicMock()
    mock_node.enable.side_effect = mock_enable
    with mock.patch.object(AristaConnection, "_connect") as mock_connect:
        mock_connect.return_value = mock_node
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="mock_device",
            platform="arista-eos",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="192.0.2.1",
            primary_ip6=None,
        )
        result = get_device_actual_neighbors(activity_input)
        assert result == DeviceNeighborData(
            neighbors={
                "Ethernet1": InterfaceNeighborData(
                    name="Ethernet51",
                    macs=[],
                    device_name="PDX01-M01-G29-IPMITOR-01",
                    device_serial=None,
                    device_role=None,
                    link_up=None,
                ),
                "Ethernet2": InterfaceNeighborData(
                    name="Ethernet51",
                    macs=[],
                    device_name="PDX01-M01-H29-IPMITOR-01",
                    device_serial=None,
                    device_role=None,
                    link_up=None,
                ),
                "Ethernet3": InterfaceNeighborData(
                    name="swp52",
                    macs=[],
                    device_name="PDX01-M01-D36-IPMIFAB-01",
                    device_serial=None,
                    device_role=None,
                    link_up=None,
                ),
                "Ethernet4": InterfaceNeighborData(
                    name="swp52",
                    macs=[],
                    device_name="PDX01-M01-C36-IPMIFAB-01",
                    device_serial=None,
                    device_role=None,
                    link_up=None,
                ),
            },
            link_states={
                "Ethernet1": True,
                "Ethernet2": True,
                "Ethernet3": True,
                "Ethernet4": True,
            },
        )


def test_get_mac_table_cumulus():
    host = "127.0.0.1"
    mock_bridge_domains = MagicMock()
    mock_bridge_domains.json.return_value = CUMULUS_BRIDGE_DOMAINS
    mock_mac_table = MagicMock()
    mock_mac_table.json.return_value = CUMULUS_MAC_TABLE

    def mock_get_fn(url):
        result = None
        if url == f"https://{host}:8765/nvue_v1/bridge/domain":
            result = mock_bridge_domains
        elif url == f"https://{host}:8765/nvue_v1/bridge/domain/br_default/mac-table":
            result = mock_mac_table
        return result

    with mock.patch.object(CumulusConnection, "get") as mock_get:
        mock_get.side_effect = mock_get_fn
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="mock_device",
            platform="cumulus-linux",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4=host,
            primary_ip6=None,
        )
        result = get_device_mac_table(activity_input)
        assert result == DeviceMacTable(
            by_mac={
                "48-B0-2D-6C-8D-6F": DeviceMacEntry(
                    mac="48-B0-2D-6C-8D-6F", interface="swp30s0", age=1414, vlan=121
                ),
                "48-B0-2D-38-15-DB": DeviceMacEntry(
                    mac="48-B0-2D-38-15-DB", interface="bond5", age=1369, vlan=122
                ),
                "48-B0-2D-81-8C-85": DeviceMacEntry(
                    mac="48-B0-2D-81-8C-85",
                    interface="bond5",
                    age=9223372036854775807,
                    vlan=122,
                ),
            },
            by_interface={
                "swp30s0": ["48-B0-2D-6C-8D-6F"],
                "bond5": [
                    "48-B0-2D-38-15-DB",
                    "48-B0-2D-81-8C-85",
                ],
            },
        )


def test_get_mac_table_arista():
    def mock_enable(cmd, encoding="json"):
        return ARISTA_MAC_TABLE if cmd == "show mac address-table" and encoding == "json" else None

    mock_node = MagicMock()
    mock_node.enable.side_effect = mock_enable
    with mock.patch.object(AristaConnection, "_connect") as mock_connect:
        mock_connect.return_value = mock_node
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="mock_device",
            platform="arista-eos",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="192.0.2.1",
            primary_ip6=None,
        )
        result = get_device_mac_table(activity_input)
        assert result == DeviceMacTable(
            by_mac={
                "B8-3F-D2-BF-3E-22": DeviceMacEntry(
                    mac="B8-3F-D2-BF-3E-22",
                    interface="Ethernet1",
                    age=int(datetime.datetime.now().timestamp() - 1709197358.909267),
                    vlan=13,
                ),
                "CC-48-3A-1E-C5-4C": DeviceMacEntry(
                    mac="CC-48-3A-1E-C5-4C",
                    interface="Ethernet3",
                    age=int(datetime.datetime.now().timestamp() - 1716671524.569158),
                    vlan=13,
                ),
                "CC-48-3A-1F-79-44": DeviceMacEntry(
                    mac="CC-48-3A-1F-79-44",
                    interface="Ethernet2",
                    age=int(datetime.datetime.now().timestamp() - 1722124624.08915),
                    vlan=13,
                ),
            },
            by_interface={
                "Ethernet1": ["B8-3F-D2-BF-3E-22"],
                "Ethernet3": ["CC-48-3A-1E-C5-4C"],
                "Ethernet2": ["CC-48-3A-1F-79-44"],
            },
        )


def test_get_mac_table_cumulus_dups():
    host = "127.0.0.1"
    mock_bridge_domains = MagicMock()
    mock_bridge_domains.json.return_value = CUMULUS_BRIDGE_DOMAINS
    mock_mac_table = MagicMock()
    mock_mac_table.json.return_value = CUMULUS_MAC_TABLE_DUPS

    def mock_get_fn(url):
        result = None
        if url == f"https://{host}:8765/nvue_v1/bridge/domain":
            result = mock_bridge_domains
        elif url == f"https://{host}:8765/nvue_v1/bridge/domain/br_default/mac-table":
            result = mock_mac_table
        return result

    with mock.patch.object(CumulusConnection, "get") as mock_get:
        mock_get.side_effect = mock_get_fn
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="mock_device",
            platform="cumulus-linux",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4=host,
            primary_ip6=None,
        )
        result = get_device_mac_table(activity_input)
        assert result == DeviceMacTable(
            by_mac={
                "48-B0-2D-6C-8D-6F": DeviceMacEntry(
                    mac="48-B0-2D-6C-8D-6F", interface="swp30s0", age=1369, vlan=122
                )
            },
            by_interface={"swp30s0": ["48-B0-2D-6C-8D-6F"]},
        )


@responses.activate
def test_get_device_actual_neighbors_cumulus_timeout():
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface?include=/*/link/state&include=/*/link/oper-status&include=/*/type",
        body=requests.exceptions.Timeout(),
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface?include=/*/lldp/neighbor",
        json={
            interface: {
                "lldp": {
                    "neighbor": data.get("lldp", {}).get("neighbor"),
                },
            }
            for interface, data in CUMULUS_INTERFACES.items()
        },
    )

    activity_input = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )
    with pytest.raises(ApplicationError) as error:
        get_device_actual_neighbors(activity_input)
    assert error.type is NetworkDeviceException
    assert (
        error.value.args[0]
        == "Timed out getting from https://192.0.2.1:8765/nvue_v1/interface with params: {'include': ['/*/link/state', '/*/link/oper-status', '/*/type']}"
    )


def test_get_device_arp_table_arista():
    def mock_enable(cmd, encoding="json"):
        return ARISTA_ARP_TABLE if cmd == "show ip arp" and encoding == "json" else None

    mock_node = MagicMock()
    mock_node.enable.side_effect = mock_enable

    with mock.patch.object(AristaConnection, "_connect") as mock_connect:
        mock_connect.return_value = mock_node
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="mock_device",
            platform="arista-eos",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="192.0.2.1",
            primary_ip6=None,
        )
        result = get_device_arp_table(activity_input)
        assert result == DeviceArpTable(
            ip_to_mac={
                "10.180.166.1": ["E0-23-FF-D1-86-95"],
                "10.180.166.11": ["9C-63-C0-09-25-F2"],
                "10.180.166.12": ["74-83-EF-20-F5-58"],
            },
            mac_to_ip={
                "E0-23-FF-D1-86-95": ["10.180.166.1"],
                "9C-63-C0-09-25-F2": ["10.180.166.11"],
                "74-83-EF-20-F5-58": ["10.180.166.12"],
            },
            interface_to_mac={
                "Vlan677": [
                    "E0-23-FF-D1-86-95",
                    "9C-63-C0-09-25-F2",
                    "74-83-EF-20-F5-58",
                ],
                "Ethernet52": ["E0-23-FF-D1-86-95"],
                "Ethernet2": ["9C-63-C0-09-25-F2"],
                "Ethernet1": ["74-83-EF-20-F5-58"],
            },
        )


@responses.activate
def test_get_device_arp_table_cumulus():
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface",
        json=CUMULUS_INTERFACES_MINIMAL,
    )
    entries = iter(CUMULUS_ARP_TABLE)
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface/br_default/ip/neighbor",
        status=404,
    )
    for interface in list(CUMULUS_INTERFACES_MINIMAL)[1:]:
        responses.add(
            responses.GET,
            f"https://192.0.2.1:8765/nvue_v1/interface/{interface}/ip/neighbor",
            json=next(entries),
        )
    activity_input = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )
    result = get_device_arp_table(activity_input)
    assert result == DeviceArpTable(
        ip_to_mac={
            "10.91.144.14": ["C0-69-11-9F-EE-69"],
            "169.254.0.1": ["9C-05-91-BA-34-68", "9C-05-91-BA-34-6A"],
        },
        mac_to_ip={
            "C0-69-11-9F-EE-69": ["10.91.144.14"],
            "9C-05-91-BA-34-68": ["169.254.0.1"],
            "9C-05-91-BA-34-6A": ["169.254.0.1"],
        },
        interface_to_mac={
            "eth0": ["C0-69-11-9F-EE-69"],
            "swp1": ["9C-05-91-BA-34-68"],
            "swp2": ["9C-05-91-BA-34-6A"],
        },
    )

    responses.replace(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/interface/swp1/ip/neighbor",
        status=400,
        body="Not Found",
    )
    with pytest.raises(ApplicationError) as error:
        get_device_arp_table(activity_input)

    assert error.type == NetworkDeviceException
    assert error.value.args[0] == (
        "Error getting from url https://192.0.2.1:8765/nvue_v1/interface/swp1/ip"
        "/neighbor: Not Found"
    )


@responses.activate
def test_validate_hostname_mismatch():
    responses.add(
        responses.GET,
        "https://172.0.0.1:8765/nvue_v1/system",
        json=CUMULUS_SYSTEM,
    )
    activity_input = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device1",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="172.0.0.1",
        primary_ip6=None,
    )
    with pytest.raises(ApplicationError) as error:
        validate_hostname(activity_input)
    assert (
        str(error.value.message)
        == "Hostname on 172.0.0.1 (rno1-m04-c10-leaf2-hss-tan-lab1) does not match the DCIM record (mock_device1)."
    )

    def mock_enable(cmd, encoding="json"):
        return ARISTA_HOSTNAME if cmd == "show hostname" and encoding == "json" else None

    mock_node = MagicMock()
    mock_node.enable.side_effect = mock_enable
    with mock.patch.object(AristaConnection, "_connect") as mock_connect:
        mock_connect.return_value = mock_node
        activity_input = NetworkDeviceData(
            id="c2c2be6-d4f6-4645-8ac8-a4a968050273",
            name="mock_device2",
            platform="arista-eos",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="172.0.0.2",
            primary_ip6=None,
        )
        with pytest.raises(ApplicationError) as error:
            validate_hostname(activity_input)
        assert (
            str(error.value.message)
            == "Hostname on 172.0.0.2 (rno1-m04-C10-leaf1.smn.lab1) does not match the DCIM record (mock_device2)."
        )


@responses.activate
def test_validate_hostname_match():
    responses.add(
        responses.GET,
        "https://172.0.0.1:8765/nvue_v1/system",
        json=CUMULUS_SYSTEM,
    )
    activity_input = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="rno1-m04-c10-leaf2-hss-tan-lab1",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="172.0.0.1",
        primary_ip6=None,
    )
    assert validate_hostname(activity_input).hostname == "rno1-m04-c10-leaf2-hss-tan-lab1"

    def mock_enable(cmd, encoding="json"):
        return ARISTA_HOSTNAME if cmd == "show hostname" and encoding == "json" else None

    mock_node = MagicMock()
    mock_node.enable.side_effect = mock_enable
    with mock.patch.object(AristaConnection, "_connect") as mock_connect:
        mock_connect.return_value = mock_node
        activity_input = NetworkDeviceData(
            id="c2c2be6-d4f6-4645-8ac8-a4a968050273",
            name="RNO1-M04-C10-LEAF1.SMN.LAB1",
            platform="arista-eos",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="172.0.0.2",
            primary_ip6=None,
        )

        assert validate_hostname(activity_input).hostname == "rno1-m04-C10-leaf1.smn.lab1"


@pytest.mark.asyncio
async def test_get_intended_neighbors():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=INTENDED_NEIGHBORS_V2,
        )
        activity_input = NetworkDeviceData(
            id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
            name="rno1-m04-c10-leaf2-hss-tan-lab1",
            platform="cumulus-linux",
            role="test_role",
            site="test_site",
            device_type="test_device_type",
            primary_ip4="172.0.0.1",
            primary_ip6=None,
        )
        neighbor_data = await get_device_intended_neighbors(activity_input)
    assert neighbor_data.neighbors == {
        "swp27": InterfaceNeighborData(
            name="swp27",
            macs=[],
            device_name="AZ51-AD505-IPMISPINE-02",
            device_serial="M2NJ33L000C",
            device_role="azure-ipmispine",
            link_up=None,
            device_rack="AD505",
            device_position=1,
        ),
        "swp28": InterfaceNeighborData(
            name="swp28",
            macs=[],
            device_name="AZ51-AD505-IPMISPINE-02",
            device_serial="M2NJ33L000C",
            device_role="azure-ipmispine",
            link_up=None,
            device_rack="AD505",
            device_position=1,
        ),
    }
    assert neighbor_data.ignore == ["swp28"]
