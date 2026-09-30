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
"""Command catalogs and validation helpers for diagnostics activities."""

from nv_config_manager_dcim.workflow_models import Platform

# Master list of all diagnostic commands and their human-readable descriptions.
# Adding a new command only requires an entry here — no per-platform duplication.
COMMAND_DESCRIPTIONS: dict[str, str] = {
    # Shared across platforms
    "show_version": "System version, hostname, and uptime",
    "show_interfaces": "All interface states and statistics",
    "show_bgp_summary": "BGP neighbor summary across all VRFs",
    "show_lldp_neighbors": "LLDP neighbor table per interface",
    "show_platform": "Hardware model, serial, and component firmware",
    "show_route_table": "IP routing table across all VRFs",
    # Shared: Arista + Cumulus
    "show_vlan": "VLAN table with member ports",
    "show_mac_table": "MAC address table by interface",
    "show_mlag": "MLAG / CLAG bond state and peer info",
    "show_platform_environment": "Fan, PSU, and temperature sensor readings",
    "show_platform_transceiver": "Optic transceiver DOM values per port",
    "show_system_health": "System health checks and component status",
    # Arista-only
    "show_vrf": "VRF list and routing instance detail",
    "show_arp_table": "ARP / neighbor resolution table",
    "show_spanning_tree": "Spanning tree bridge and port states",
    "show_port_channels": "Port-channel / LAG member ports and state",
    "show_isis_neighbors": "IS-IS adjacency table",
    "show_isis_interfaces": "IS-IS enabled interfaces and metric",
    "show_isis_database": "IS-IS LSP database",
    "show_mpls_interfaces": "MPLS enabled interfaces",
    "show_mpls_rsvp_neighbors": "MPLS RSVP neighbor summary",
    "show_mac_security": "MACsec session state per interface",
    "show_mac_security_counters": "MACsec encrypted/unencrypted byte counters",
    "show_vrrp": "VRRP group states and priorities",
    "show_inventory": "Full hardware inventory (chassis, modules, SFPs)",
    # Cumulus-only
    "show_interface_counters": "Interface traffic counters and error statistics",
    "show_interface_mac": "MAC addresses learned per interface",
}

# Per-platform support sets — each platform declares which commands it supports.
# NV-OS exposes the same NVUE API as Cumulus but does not do IP routing,
# so BGP summary and route table are excluded.
PLATFORM_COMMANDS: dict[Platform, set[str]] = {
    Platform.CUMULUS_LINUX: {
        # Existing
        "show_version",
        "show_interfaces",
        "show_bgp_summary",
        "show_lldp_neighbors",
        "show_platform",
        "show_route_table",
        # New — NVUE REST API
        "show_system_health",
        "show_interface_counters",
        "show_interface_mac",
        "show_mac_table",
        "show_mlag",
        "show_platform_environment",
        "show_vlan",
        "show_platform_transceiver",
    },
    Platform.NV_OS: {
        "show_version",
        "show_interfaces",
        "show_lldp_neighbors",
        "show_platform",
        # New — NVUE REST API (same endpoints as Cumulus, port 443)
        "show_system_health",
        "show_interface_counters",
        "show_interface_mac",
        "show_mac_table",
        "show_mlag",
        "show_platform_environment",
        "show_vlan",
        "show_platform_transceiver",
    },
    Platform.ARISTA_EOS: {
        # All via eAPI JSON-RPC (HTTPS port 443)
        "show_version",
        "show_interfaces",
        "show_vlan",
        "show_lldp_neighbors",
        "show_vrf",
        "show_port_channels",
        "show_spanning_tree",
        "show_route_table",
        "show_bgp_summary",
        "show_isis_neighbors",
        "show_isis_interfaces",
        "show_isis_database",
        "show_mpls_interfaces",
        "show_mpls_rsvp_neighbors",
        "show_mac_security",
        "show_mac_security_counters",
        "show_vrrp",
        "show_arp_table",
        "show_mac_table",
        "show_inventory",
        "show_mlag",
    },
    Platform.JUNIPER_JUNOS: {
        "show_version",
        "show_interfaces",
        "show_lldp_neighbors",
        "show_route_table",
        "show_arp_table",
    },
    Platform.MLNX_OS: set(),
    Platform.UFM: set(),
}


def get_available_commands(platform: Platform) -> dict[str, str]:
    """Return the {name: description} map for a given platform."""
    supported = PLATFORM_COMMANDS.get(platform, set())
    return {name: COMMAND_DESCRIPTIONS[name] for name in supported if name in COMMAND_DESCRIPTIONS}


def validate_commands(platform: Platform, names: list[str]) -> list[str]:
    """Return only the catalog names that are valid for the given platform.

    Normalises each input name by lower-casing and replacing spaces/hyphens
    with underscores so that ``"show version"`` resolves to ``"show_version"``.
    Names with no match in the catalog are silently dropped.
    """
    available = get_available_commands(platform)
    result = []
    for name in names:
        normalised = name.strip().lower().replace(" ", "_").replace("-", "_")
        if normalised in available:
            result.append(normalised)
    return result


__all__ = [
    "COMMAND_DESCRIPTIONS",
    "PLATFORM_COMMANDS",
    "get_available_commands",
    "validate_commands",
]
