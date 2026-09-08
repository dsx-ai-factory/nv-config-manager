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
"""Shared device-client domain models."""

from __future__ import annotations

import datetime
import ipaddress
import re
import sys
from typing import Any

import netaddr
from pydantic import BaseModel

from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


def _append_unique(index: dict[str, list[str]], key: str, value: str) -> None:
    values = index.setdefault(key, [])
    if value not in values:
        values.append(value)


def is_mac_address(mac: str | None) -> bool:
    """Check if a string is a valid MAC address."""
    if mac is None:
        return False
    try:
        netaddr.EUI(mac)
        return True
    except netaddr.core.AddrFormatError:
        return False


def format_mac(mac: str) -> str:
    """Format a MAC address to colon-separated lowercase (e.g. 00:00:00:00:a3:42)."""
    return mac.replace("-", ":").lower()


class DeviceMacEntry(BaseModel):
    """Represents a MAC address table entry."""

    mac: str
    interface: str
    age: int
    vlan: int | None = None

    @staticmethod
    def from_nvue(data: dict[str, Any]) -> DeviceMacEntry:
        """Produce a MAC entry from NVUE API JSON."""
        return DeviceMacEntry(
            mac=str(netaddr.EUI(data["mac"])),
            interface=data["interface"],
            vlan=data.get("vlan"),
            age=int(data["last-update"]) if data.get("last-update") else sys.maxsize,
        )

    @staticmethod
    def from_eapi(data: dict[str, Any]) -> DeviceMacEntry:
        """Produce a MAC entry from PYEAPI JSON."""
        return DeviceMacEntry(
            mac=str(netaddr.EUI(data["macAddress"])),
            vlan=data["vlanId"],
            interface=data["interface"],
            age=int(
                datetime.datetime.now().timestamp() - data["lastMove"]
                if data.get("lastMove")
                else sys.maxsize
            ),
        )


class DeviceArpTable(BaseModel):
    """Device ARP Table."""

    ip_to_mac: dict[str, list[str]] = {}
    mac_to_ip: dict[str, list[str]] = {}
    interface_to_mac: dict[str, list[str]] = {}

    def add_entry(self, ip: str, mac: str, interface: str) -> None:
        """Record one ARP entry after normalizing IP and MAC."""
        ip_std = str(ipaddress.ip_address(ip))
        mac_std = str(netaddr.EUI(mac))
        _append_unique(self.ip_to_mac, ip_std, mac_std)
        _append_unique(self.mac_to_ip, mac_std, ip_std)
        _append_unique(self.interface_to_mac, interface, mac_std)

    def _process_eapi_neighbor(self, neighbor: dict[str, Any]) -> None:
        """Process a single EAPI neighbor entry."""
        if not (
            neighbor.get("address") and neighbor.get("hwAddress") and neighbor.get("interface")
        ):
            logger.warning("ARP entry missing data, skipping: %s", neighbor)
            return

        for interface in neighbor["interface"].split(","):
            self.add_entry(neighbor["address"], neighbor["hwAddress"], interface.strip())

    @staticmethod
    def from_eapi(data: dict[str, list[dict[str, Any]]]) -> DeviceArpTable:
        """ARP table from EAPI."""
        result = DeviceArpTable()

        for neighbor in data.get("ipV4Neighbors", []):
            result._process_eapi_neighbor(neighbor)

        return result

    @staticmethod
    def from_nvue(data: dict[str, dict[str, Any]]) -> DeviceArpTable:
        """ARP table from NVUE API."""
        result = DeviceArpTable()
        for interface, item in data.items():
            result.interface_to_mac.setdefault(interface, [])
            for ipaddr, ip_data in item.get("ipv4", {}).items():
                if ip_data.get("lladdr"):
                    result.add_entry(ipaddr, ip_data["lladdr"], interface)
                else:
                    logger.warning("ARP entry missing data, skipping: %s %s", ipaddr, ip_data)

        return result


class InterfaceNeighborData(BaseModel):
    """Interface data needed for cable validation."""

    name: str | None = None
    macs: list[str] = []
    device_name: str | None = None
    device_serial: str | None = None
    device_role: str | None = None
    device_rack: str | None = None
    device_position: int | None = None
    link_up: bool | None = None
    ts_info: str | None = None

    @staticmethod
    def from_graphql(data: dict[str, Any]) -> InterfaceNeighborData:
        """Produce InterfaceNeighborData from nautobot graphql."""
        if not data["connected_interface"]:
            return InterfaceNeighborData()

        device = (
            data["connected_interface"]["device"]
            if data["connected_interface"].get("device")
            else data["connected_interface"]["module"]["device"]
        )
        role = device["role"]
        if role:
            role = role["name"].lower().replace(" ", "-")

        if device.get("rack"):
            rack = device["rack"]["name"]
        else:
            rack = None

        return InterfaceNeighborData(
            name=(data["connected_interface"]["name"]),
            macs=(
                [str(netaddr.EUI(data["connected_interface"]["mac_address"]))]
                if data["connected_interface"]["mac_address"]
                else []
            ),
            device_name=(device["name"]),
            device_serial=(device["serial"]),
            device_role=role,
            device_rack=rack,
            device_position=device.get("position"),
        )

    @staticmethod
    def from_eapi(data: dict[str, Any]) -> InterfaceNeighborData:
        """Produce InterfaceNeighborData from Arista EAPI JSON."""
        name = re.sub(
            r"[\"\']",
            "",
            data["lldpNeighborInfo"][0]["neighborInterfaceInfo"]["interfaceId"],
        )
        return InterfaceNeighborData(
            device_name=data["lldpNeighborInfo"][0]["systemName"],
            name=str(netaddr.EUI(name)) if is_mac_address(name) else name,
        )

    @staticmethod
    def from_nvue(data: dict[str, Any]) -> InterfaceNeighborData:
        """Produce InterfaceNeighborData from cumulus NVUE API JSON."""
        # dict with one key being the neighbor device name
        device = [*data][0]
        return InterfaceNeighborData(
            device_name=device,
            name=(
                str(netaddr.EUI(data[device]["port"]["name"]))
                if is_mac_address(data[device]["port"]["name"])
                else data[device]["port"]["name"]
            ),
        )


class DeviceNeighborData(BaseModel, validate_assignment=True):
    """Neighbor data for a device."""

    # key is the interface name
    neighbors: dict[str, InterfaceNeighborData] = {}
    link_states: dict[str, bool] = {}
    ts_info: dict[str, str] = {}
    ignore: list[str] = []
    link_state_only: list[str] = []


class DeviceMacTable(BaseModel):
    """Represents a MAC address table for a device."""

    by_mac: dict[str, DeviceMacEntry] = {}
    by_interface: dict[str, list[str]] = {}
