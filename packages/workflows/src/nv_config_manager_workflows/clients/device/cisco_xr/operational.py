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
"""Operational data of a Cisco IOS XR router: YANG subtree filters and reply parsers.

IOS XR has no "run this show command" RPC, so every read NVCM needs is a ``<get>`` on an
operational model with a narrow subtree filter. The parsers turn the XML replies into the
data the diagnostics and cable-validation workflows use. Pure text processing, so it is
tested without a router.
"""

from __future__ import annotations

from typing import Any

import netaddr
from lxml import etree  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]

from nv_config_manager_workflows.clients.device.exceptions import NetworkDeviceException
from nv_config_manager_workflows.clients.device.models import (
    InterfaceNeighborData,
    is_mac_address,
)

# --- Operational data through YANG (reply shapes read from a lab router running 26.4.1) ---
# IOS XR has no "run this show command" RPC, so every read NVCM needs is a <get> on an
# operational model with a narrow subtree filter (whole containers are megabytes).
FILTER_VERSION = (
    '<install xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-install-oper"><version/></install>'
)
FILTER_UPTIME = '<system-time xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-shellutil-oper"><uptime/></system-time>'
FILTER_LLDP = (
    '<lldp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ethernet-lldp-oper">'
    "<nodes><node><neighbors><devices/></neighbors></node></nodes></lldp>"
)
FILTER_LINKS = '<interfaces xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-pfi-im-cmd-oper"><interface-briefs/></interfaces>'
FILTER_ARP = (
    '<arp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ipv4-arp-oper">'
    "<nodes><node><entries/></node></nodes></arp>"
)
_LINE_UP = "im-state-up"
_ARP_UNRESOLVED_STATES = ("state-incomplete", "state-deleted")


def _xml_root(xml: str, what: str) -> Any:
    try:
        return etree.fromstring(xml.encode())
    except etree.XMLSyntaxError as error:
        raise NetworkDeviceException(f"Unreadable {what} reply from the router: {error}") from error


def parse_version(xml: str) -> str:
    """Running IOS XR version (``26.4.1.03I``) from the install-oper ``version`` container."""
    for package in _xml_root(xml, "version").iterfind(".//{*}version/{*}package"):
        if package.findtext("{*}name") == "IOS-XR" and package.findtext("{*}version"):
            return str(package.findtext("{*}version")).strip()
    raise NetworkDeviceException(
        "No IOS-XR package version in the router's reply.", non_retryable=True
    )


def parse_uptime(xml: str) -> int:
    """Uptime in seconds from the shellutil-oper ``system-time/uptime`` container."""
    text = _xml_root(xml, "uptime").findtext(".//{*}uptime/{*}uptime")
    if text is None or not text.strip().isdigit():
        raise NetworkDeviceException("No uptime in the router's reply.")
    return int(text.strip())


def parse_lldp(xml: str) -> dict[str, InterfaceNeighborData]:
    """Neighbors by local (physical) interface from the lldp-oper ``neighbors/devices`` container."""
    neighbors: dict[str, InterfaceNeighborData] = {}
    for device in _xml_root(xml, "LLDP").iterfind(".//{*}neighbors/{*}devices/{*}device"):
        local = device.findtext("{*}interface-name")
        neighbor = device.find("{*}lldp-neighbor")
        if not local or neighbor is None:
            continue
        if local in neighbors:
            raise NetworkDeviceException(f"Received multiple LLDP neighbors on interface {local}")
        port = (neighbor.findtext("{*}port-id-detail") or "").strip() or None
        system = (
            neighbor.findtext("{*}detail/{*}system-name") or neighbor.findtext("{*}device-id") or ""
        ).strip()
        neighbors[local] = InterfaceNeighborData(
            device_name=system or None,
            name=str(netaddr.EUI(port)) if port and is_mac_address(port) else port,
        )
    return neighbors


def parse_link_states(xml: str) -> dict[str, bool]:
    """Line-protocol state by interface from the pfi-im-cmd-oper ``interface-briefs`` container."""
    states: dict[str, bool] = {}
    for brief in _xml_root(xml, "interface state").iterfind(
        ".//{*}interface-briefs/{*}interface-brief"
    ):
        name = brief.findtext("{*}interface-name")
        if name:
            states[name] = brief.findtext("{*}line-state") == _LINE_UP
    return states


def parse_arp(xml: str) -> list[tuple[str, str, str]]:
    """(ip, mac, interface) for each resolved entry of the ipv4-arp-oper ``entries`` container."""
    rows: list[tuple[str, str, str]] = []
    for entry in _xml_root(xml, "ARP").iterfind(".//{*}entries/{*}entry"):
        if entry.findtext("{*}state") in _ARP_UNRESOLVED_STATES:
            continue
        ip = entry.findtext("{*}address")
        mac = entry.findtext("{*}hardware-address")
        interface = entry.findtext("{*}interface-name")
        if ip and mac and interface and int(mac.replace(":", ""), 16):
            rows.append((ip, mac, interface))
    return rows
