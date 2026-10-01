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
"""Interface dataclass."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from nv_config_manager_dcim.render import RenderInterface


@dataclass(frozen=True)
class ConnectedDevice:  # pylint: disable=too-many-instance-attributes
    """Representation of a Device connected on an interface."""

    name: str
    role: str | None
    asn: str | None
    tags: list[str] = field(compare=False)  # make this class hashable
    peer_ipv4: str | None
    peer_ipv6: str | None
    tenant: str | None = None

    @property
    def peer_group(self):
        """Return the normalized role as the default BGP peer group."""
        return (self.role or "").upper()


@dataclass(frozen=True)
class ConnectedInterface:
    """Representation of a connected interface in nautobot."""

    name: str
    device: ConnectedDevice


@dataclass(frozen=True)
class Interface:  # pylint: disable=too-many-instance-attributes
    """Template-facing representation of a provider-neutral interface."""

    name: str
    primary_ipv4: str | None
    # These 4 IP fields are for future-proofing
    primary_ipv6: str | None
    secondary_ipv4: list[str] | None
    secondary_ipv6: list[str] | None
    link_local: str | None
    enabled: bool
    mtu: int | None
    tags: list[str]
    untagged_vlan: int | None
    tagged_vlans: list[int]
    vrf: str
    connected_interface: ConnectedInterface
    description: str
    role: str
    optic_type: str
    mgmt_only: bool
    member_interfaces: list[str]
    mac_address: str | None = None
    vip_ipv4: str | None = None

    @property
    def is_dot1q(self) -> bool:
        """Return True if this is a dot1q interface."""
        return bool(re.search(r"\d+\.\d+$", self.name))

    @property
    def optic_speed(self) -> str | None:
        """Return the optic speed."""
        match = re.search("A_(\\d+G?)BASE", self.optic_type)
        if match:
            if match.group(1).endswith("G"):
                return match.group(1)
            if match.group(1) == "1000":
                return "1G"
        return None

    @property
    def vlan_number(self) -> int | None:
        """Return the vlan number if this is an SVI."""
        match = re.search("vlan(\\d+)", self.name.lower())
        if match:
            return int(match.group(1))
        return None

    @property
    def parent(self) -> str | None:
        """Return the parent interface if applicable."""
        # Only applicable for Cumulus atm
        if self.is_dot1q:
            return self.name.split(".")[0]
        match = re.match(r"(swp\d+)s(\d)", self.name)
        if match:
            return match.group(1)
        return None

    def has_bgp_peer(self) -> bool:
        """Consolidated function to determine if a connected_interface is BGP eligible.

        Returns:
            bool: True if the interface is BGP eligible, False otherwise.
        """
        if not self.connected_interface:
            return False

        return not self.primary_ipv4 and not self.untagged_vlan

    @property
    def peer_tags(self) -> list[str] | None:
        """Return the peer tags if applicable."""
        if self.connected_interface:
            return self.connected_interface.device.tags
        return []

    @staticmethod
    def _build_addressing(
        entry: RenderInterface,
    ) -> tuple[str | None, str | None, list[str], list[str], str | None, str, str | None]:
        """Build template-facing interface addressing from typed render data."""
        primary_ipv4 = None
        primary_ipv6 = None
        secondary_ipv4 = []
        secondary_ipv6 = []
        link_local = None
        vip_ipv4 = None
        vrf = entry.vrf.name if entry.vrf else "default"
        for ip_entry in entry.addresses:
            if ip_entry.version == 4:
                role_name = ip_entry.role
                if role_name == "VIP":
                    vip_ipv4 = str(ip_entry.address)
                elif not primary_ipv4:
                    primary_ipv4 = str(ip_entry.address)
                else:
                    secondary_ipv4.append(str(ip_entry.address))
            else:
                # Set primary, secondary, and link_local IPv6 Addresses
                if ipaddress.ip_interface(ip_entry.address).is_link_local:
                    link_local = str(ip_entry.address)
                elif not primary_ipv6:
                    primary_ipv6 = str(ip_entry.address)
                else:
                    secondary_ipv6.append(str(ip_entry.address))

        # Strip site name from VRF name (e.g., "SITE_VRFNAME" becomes "VRFNAME")
        if "_" in vrf:
            vrf = vrf.split("_")[1]

        return (
            primary_ipv4,
            primary_ipv6,
            secondary_ipv4,
            secondary_ipv6,
            link_local,
            vrf,
            vip_ipv4,
        )

    @staticmethod
    def _build_connected_interface(entry: RenderInterface) -> ConnectedInterface | None:
        """Build a template-facing peer from the typed provider contract."""
        connected = entry.connected_interface
        if connected is None:
            return None
        peer_ipv4 = next(
            (str(address.host) for address in connected.addresses if address.version == 4), None
        )
        peer_ipv6 = next(
            (str(address.host) for address in connected.addresses if address.version == 6), None
        )
        return ConnectedInterface(
            name=connected.name,
            device=ConnectedDevice(
                name=connected.device.name,
                role=connected.device.role,
                tags=list(connected.device.tags),
                tenant=connected.device.tenant,
                asn=connected.device.routing_asn,
                peer_ipv4=peer_ipv4,
                peer_ipv6=peer_ipv6,
            ),
        )

    @staticmethod
    def _from_render_data(entry: RenderInterface) -> Interface:
        (
            primary_ipv4,
            primary_ipv6,
            secondary_ipv4,
            secondary_ipv6,
            link_local,
            vrf,
            vip_ipv4,
        ) = Interface._build_addressing(entry)

        connected_interface = Interface._build_connected_interface(entry)

        return Interface(
            name=entry.name,
            primary_ipv4=primary_ipv4,
            primary_ipv6=primary_ipv6,
            secondary_ipv4=secondary_ipv4,
            secondary_ipv6=secondary_ipv6,
            link_local=link_local,
            enabled=entry.enabled,
            mtu=entry.mtu,
            tags=list(entry.tags),
            untagged_vlan=entry.untagged_vlan.vid if entry.untagged_vlan else None,
            tagged_vlans=[vlan.vid for vlan in entry.tagged_vlans],
            vrf=vrf,
            connected_interface=connected_interface,
            description=entry.description,
            role=entry.role,
            optic_type=entry.type,
            mgmt_only=entry.management_only,
            member_interfaces=list(entry.member_interfaces),
            mac_address=entry.mac_address,
            vip_ipv4=vip_ipv4,
        )

    @staticmethod
    def from_render_data(entry: RenderInterface) -> Interface:
        """Create an interface object from normalized render data."""
        return Interface._from_render_data(entry)
