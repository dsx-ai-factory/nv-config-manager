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
"""Topology mapping helpers for InfiniBand GUID discovery."""

from typing import Any

from nv_config_manager_workflows.activities.ib_guid_discovery.models import IBGuidMapping


def build_switch_neighbor_index(
    switch_id_to_name: dict[str, str],
    neighbors_by_switch_id: dict[str, dict[str, dict[str, str]]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Build an index of switch interfaces."""
    index: dict[tuple[str, str], dict[str, str]] = {}
    for switch_id, switch_name in switch_id_to_name.items():
        neighbors = neighbors_by_switch_id.get(switch_id, {})
        for local_iface_name, neighbor in neighbors.items():
            far_device = (neighbor or {}).get("device_name", "")
            far_iface = (neighbor or {}).get("name", "")
            if not far_device or not far_iface:
                continue
            index[(switch_name.lower(), str(local_iface_name))] = {
                "device_name": far_device,
                "interface_name": far_iface,
            }
    return index


def _extract_iface_display_names(iface: dict[str, Any]) -> tuple[str, str]:
    """Resolve human-readable device and interface names from a Nautobot interface record."""
    iface_name = str(iface.get("name") or "")
    device_raw = iface.get("device")
    device_name = ""
    if isinstance(device_raw, dict):
        device_name = str(device_raw.get("display") or device_raw.get("name") or "")
    return device_name, iface_name


def _extract_port_fields(port: dict[str, Any]) -> tuple[str, str, str, str, str]:
    ufm_guid = str(port.get("guid", "") or "").strip()
    system_name = str(port.get("system_name", "") or "")
    ufm_port = str(port.get("port", "") or "")
    peer_switch = str(port.get("peer_node_name", "") or "")
    peer_port = str(port.get("peer_port", "") or "")
    return ufm_guid, system_name, ufm_port, peer_switch, peer_port


def _should_skip_port(
    *,
    ufm_guid: str,
    system_name: str,
    peer_switch: str,
    peer_port: str,
    managed_switch_names: set[str],
) -> bool:
    if not ufm_guid:
        return True
    if system_name and system_name.lower() in managed_switch_names:
        return True
    if not peer_switch or not peer_port:
        return True
    if peer_switch.lower() not in managed_switch_names:
        return True
    return False


def compute_guid_mappings(
    ib_ports: list[dict[str, Any]],
    switch_id_to_name: dict[str, str],
    neighbors_by_switch_id: dict[str, dict[str, dict[str, str]]],
    nautobot_interface_by_dev_iface: dict[tuple[str, str], dict[str, str]],
) -> list[IBGuidMapping]:
    """Pure join: UFM ports -> DCIM interfaces -> sync actions.

    Args:
        ib_ports: list of dicts as returned by UFMClient.get_ports().
        switch_id_to_name: Nautobot switch UUID -> hostname.
        neighbors_by_switch_id: per-switch intended-neighbor dict
        nautobot_interface_by_dev_iface: compute-side interfaces

    Returns:
        A list of IBGuidMapping records.
    """
    managed_switch_names = {name.lower() for name in switch_id_to_name.values()}
    neighbor_index = build_switch_neighbor_index(switch_id_to_name, neighbors_by_switch_id)

    mappings: list[IBGuidMapping] = []

    for port in ib_ports:
        ufm_guid, system_name, ufm_port, peer_switch, peer_port = _extract_port_fields(port)
        if _should_skip_port(
            ufm_guid=ufm_guid,
            system_name=system_name,
            peer_switch=peer_switch,
            peer_port=peer_port,
            managed_switch_names=managed_switch_names,
        ):
            continue

        neighbor = neighbor_index.get((peer_switch.lower(), peer_port))
        if not neighbor:
            mappings.append(
                IBGuidMapping.from_skip_no_cable(
                    ufm_guid=ufm_guid,
                    system_name=system_name,
                    ufm_port=ufm_port,
                    peer_switch=peer_switch,
                    peer_port=peer_port,
                )
            )
            continue

        dev_name = neighbor["device_name"]
        iface_name = neighbor["interface_name"]
        cache_key = (dev_name.lower(), iface_name)
        nb_iface = nautobot_interface_by_dev_iface.get(cache_key)

        if not nb_iface:
            mappings.append(
                IBGuidMapping.from_skip_iface_missing(
                    ufm_guid=ufm_guid,
                    system_name=system_name,
                    ufm_port=ufm_port,
                    peer_switch=peer_switch,
                    peer_port=peer_port,
                    device_name=dev_name,
                    interface_name=iface_name,
                )
            )
            continue

        mappings.append(
            IBGuidMapping.from_resolved_iface(
                ufm_guid=ufm_guid,
                system_name=system_name,
                ufm_port=ufm_port,
                peer_switch=peer_switch,
                peer_port=peer_port,
                device_name=dev_name,
                interface_name=iface_name,
                interface_id=str(nb_iface.get("id", "")),
                current_guid=str(nb_iface.get("ib_guid", "") or ""),
            )
        )

    return mappings


def _intended_neighbor_from_interface(interface: Any) -> tuple[str, dict[str, str]] | None:
    """Parse one interface node into (local_port_name, neighbor record) or None."""
    iface: dict[str, Any] = interface if isinstance(interface, dict) else {}
    connected = iface.get("connected_interface")
    if not connected:
        return None
    far_device = (connected.get("device") or {}).get("name", "")
    far_iface = connected.get("name", "")
    if not far_device or not far_iface:
        return None
    local_name = iface.get("name") or ""
    if not local_name:
        return None
    return str(local_name), {"device_name": far_device, "name": far_iface}


def _neighbors_for_switch_device(device: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Build local_port -> intended neighbor map for one GraphQL device."""
    neighbors: dict[str, dict[str, str]] = {}
    for interface in device.get("interfaces") or []:
        parsed = _intended_neighbor_from_interface(interface)
        if parsed is None:
            continue
        local_name, entry = parsed
        neighbors[local_name] = entry
    return neighbors
