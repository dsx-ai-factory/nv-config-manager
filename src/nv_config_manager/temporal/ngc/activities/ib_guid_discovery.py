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

from __future__ import annotations

import logging
from typing import Any, Self

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.dcim import create_dcim_client
from nv_config_manager.temporal.client.ufm import UFMClient
from nv_config_manager.temporal.common.mixins.stage import StageOutput

log = logging.getLogger(__name__)

IB_GUID_CF_KEY = "ib_guid"


class IBGuidMapping(BaseModel):
    """One UFM port -> DCIM interface mapping with the desired GUID action."""

    ufm_system_name: str
    ufm_port: str
    ufm_guid: str
    peer_switch_name: str
    peer_switch_port: str

    device_name: str = ""
    interface_name: str = ""
    interface_id: str = ""
    current_guid: str = ""

    action: str
    reason: str = ""

    @property
    def discovered_guid(self) -> str:
        """Alias retained for workflow readability."""
        return self.ufm_guid

    @staticmethod
    def _classify_action(current_guid: str, discovered_guid: str) -> str:
        """Decide the sync action for a known current/desired GUID pair."""
        current = (current_guid or "").strip().lower()
        desired = (discovered_guid or "").strip().lower()
        if not desired:
            return "skip"
        if current == desired:
            return "noop"
        if not current:
            return "set"
        return "update"

    @classmethod
    def from_skip_no_cable(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
    ) -> Self:
        """UFM port has no modeled cable from the peer switch port."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            action="skip",
            reason=(
                f"No cable modeled on {peer_switch} port {peer_port}; "
                "cannot resolve compute interface."
            ),
        )

    @classmethod
    def from_skip_iface_missing(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
        device_name: str,
        interface_name: str,
    ) -> Self:
        """Cable topology points at a device/interface not present in the DCIM."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            device_name=device_name,
            interface_name=interface_name,
            action="skip",
            reason=f"DCIM interface {device_name}/{interface_name} not found.",
        )

    @classmethod
    def from_resolved_iface(
        cls,
        *,
        ufm_guid: str,
        system_name: str,
        ufm_port: str,
        peer_switch: str,
        peer_port: str,
        device_name: str,
        interface_name: str,
        interface_id: str,
        current_guid: str,
    ) -> Self:
        """Mapping with a resolved DCIM interface and classified sync action."""
        return cls(
            ufm_system_name=system_name,
            ufm_port=ufm_port,
            ufm_guid=ufm_guid,
            peer_switch_name=peer_switch,
            peer_switch_port=peer_port,
            device_name=device_name,
            interface_name=interface_name,
            interface_id=interface_id,
            current_guid=current_guid,
            action=cls._classify_action(current_guid, ufm_guid),
        )


class DiscoverIBPortGuidsInput(BaseModel):
    """Input for the discovery activity."""

    ufm_host: str
    site: str | None = None
    switch_device_ids: list[str]


class DiscoverIBPortGuidsOutput(StageOutput):
    """Result of discovery."""

    mappings: list[IBGuidMapping]


class SyncIBGuidInput(BaseModel):
    """Input for syncing a single interface's `ib_guid` custom field."""

    interface_id: str
    guid: str
    dry_run: bool = True


class SyncIBGuidOutput(BaseModel):
    """Result of a sync attempt."""

    interface_id: str
    device_name: str = ""
    interface_name: str = ""
    previous_guid: str
    new_guid: str
    changed: bool
    dry_run: bool
    reason: str = ""


def _build_switch_neighbor_index(
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


def _compute_guid_mappings(
    ib_ports: list[dict[str, Any]],
    switch_id_to_name: dict[str, str],
    neighbors_by_switch_id: dict[str, dict[str, dict[str, str]]],
    nautobot_interface_by_dev_iface: dict[tuple[str, str], dict[str, str]],
) -> list[IBGuidMapping]:
    """Pure join: UFM ports -> Nautobot interfaces -> sync actions.

    Args:
        ib_ports: list of dicts as returned by UFMClient.get_ports().
        switch_id_to_name: Nautobot switch UUID -> hostname.
        neighbors_by_switch_id: per-switch intended-neighbor dict
        nautobot_interface_by_dev_iface: compute-side interfaces

    Returns:
        A list of IBGuidMapping records.
    """
    managed_switch_names = {name.lower() for name in switch_id_to_name.values()}
    neighbor_index = _build_switch_neighbor_index(switch_id_to_name, neighbors_by_switch_id)

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


@activity.defn
async def discover_ib_port_guids(
    input: DiscoverIBPortGuidsInput,
) -> DiscoverIBPortGuidsOutput:
    """Discover UFM-reported port GUIDs and map them to Nautobot interfaces."""

    async with UFMClient(host=input.ufm_host, site=input.site) as ufm:
        ib_ports = await ufm.get_ports(unhealthy_only=False)

    if not input.switch_device_ids:
        raise ApplicationError(
            "No switch_device_ids provided, cannot resolve topology.",
            non_retryable=True,
        )

    client = create_dcim_client()
    async with client:
        topology = await client.get_ib_switch_topology(input.switch_device_ids)
        switch_id_to_name = dict(topology.switch_names)
        neighbors_by_switch_id = {
            switch_id: {
                interface_name: {
                    "device_name": neighbor.device_name,
                    "name": neighbor.interface_name,
                }
                for interface_name, neighbor in neighbors.items()
            }
            for switch_id, neighbors in topology.intended_neighbors.items()
        }

        neighbor_index = _build_switch_neighbor_index(switch_id_to_name, neighbors_by_switch_id)
        dev_iface_pairs: set[tuple[str, str]] = {
            (entry["device_name"], entry["interface_name"]) for entry in neighbor_index.values()
        }
        interfaces = await client.get_ib_interface_guids(dev_iface_pairs)
        nautobot_interface_by_dev_iface = {
            (interface.device_name.lower(), interface.interface_name): {
                "id": interface.interface_id,
                "ib_guid": interface.guid,
            }
            for interface in interfaces
        }

    mappings = _compute_guid_mappings(
        ib_ports=ib_ports,
        switch_id_to_name=switch_id_to_name,
        neighbors_by_switch_id=neighbors_by_switch_id,
        nautobot_interface_by_dev_iface=nautobot_interface_by_dev_iface,
    )

    counts = {"set": 0, "update": 0, "noop": 0, "skip": 0}
    for m in mappings:
        counts[m.action] = counts.get(m.action, 0) + 1
    log.info(
        "IB GUID discovery: %d mappings (set=%d, update=%d, noop=%d, skip=%d)",
        len(mappings),
        counts["set"],
        counts["update"],
        counts["noop"],
        counts["skip"],
    )

    return DiscoverIBPortGuidsOutput(
        mappings=mappings,
        display=(
            f"Discovered {len(mappings)} IB port/interface mappings "
            f"(set={counts['set']}, update={counts['update']}, "
            f"noop={counts['noop']}, skip={counts['skip']})"
        ),
    )


@activity.defn
async def sync_ib_guid_on_interface(input: SyncIBGuidInput) -> SyncIBGuidOutput:
    """Sync the `ib_guid` custom field on one Nautobot interface."""

    if not input.interface_id:
        raise ApplicationError("interface_id is required", non_retryable=True)
    if not input.guid:
        raise ApplicationError("guid is required", non_retryable=True)

    client = create_dcim_client()
    async with client:
        current = await client.get_ib_interface_guid(input.interface_id)
        previous_guid = current.guid
        device_name = current.device_name
        interface_name = current.interface_name

        if previous_guid.lower() == input.guid.lower():
            return SyncIBGuidOutput(
                interface_id=input.interface_id,
                device_name=device_name,
                interface_name=interface_name,
                previous_guid=previous_guid,
                new_guid=previous_guid,
                changed=False,
                dry_run=input.dry_run,
                reason="ib_guid already up to date",
            )

        if input.dry_run:
            return SyncIBGuidOutput(
                interface_id=input.interface_id,
                device_name=device_name,
                interface_name=interface_name,
                previous_guid=previous_guid,
                new_guid=input.guid,
                changed=False,
                dry_run=True,
                reason="dry_run=True; no write performed",
            )

        await client.set_ib_interface_guid(input.interface_id, input.guid)
        log.info(
            "Synced ib_guid on interface %s (%s/%s): '%s' -> '%s'",
            input.interface_id,
            device_name,
            interface_name,
            previous_guid,
            input.guid,
        )

        return SyncIBGuidOutput(
            interface_id=input.interface_id,
            device_name=device_name,
            interface_name=interface_name,
            previous_guid=previous_guid,
            new_guid=input.guid,
            changed=True,
            dry_run=False,
        )
