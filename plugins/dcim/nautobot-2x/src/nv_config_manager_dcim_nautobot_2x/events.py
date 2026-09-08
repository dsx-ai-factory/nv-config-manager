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
"""Nautobot changelog event handlers for configuration rendering.

Nautobot owns both the changelog schema and the queries required to determine
which devices a change affects. The core render dispatcher only registers these
handlers and queues the returned :class:`RenderEventRequest` objects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from nv_config_manager_dcim.api import (
    DCIMClient,
    DCIMRenderEventRegistry,
)
from nv_config_manager_dcim.errors import DCIMInvalidDataError
from nv_config_manager_dcim.models import DCIMChangeEvent, RenderEventRequest


@runtime_checkable
class NautobotRenderEventClient(Protocol):
    """Nautobot data operations needed only to interpret its changelog events."""

    async def get_render_enabled_devices_matching(self, filters: Mapping[str, Any]) -> list[str]:
        """Resolve managed devices matching Nautobot object filters."""

    async def get_render_enabled_devices_for_vrf(self, vrf_id: str) -> list[str]:
        """Resolve managed devices affected by a Nautobot VRF."""

    async def get_render_enabled_devices_for_ip_address(self, ip_address_id: str) -> list[str]:
        """Resolve managed devices affected by a Nautobot IP address."""

    async def get_render_enabled_devices_for_autonomous_system(self, asn: str) -> list[str]:
        """Resolve managed devices affected by a Nautobot autonomous system."""

    async def get_render_enabled_devices_for_bgp_peering(self, peering_id: str) -> list[str]:
        """Resolve managed devices affected by a Nautobot BGP peering."""

    async def get_render_enabled_device_for_bgp_routing_instance(
        self, routing_instance_id: str
    ) -> str | None:
        """Resolve the managed device affected by a routing instance."""

    async def get_cable_termination_device_id(self, termination: Mapping[str, Any]) -> str:
        """Resolve a Nautobot cable termination to its owning device."""


def _nautobot_client(client: DCIMClient) -> NautobotRenderEventClient:
    """Validate the client supplied by the selected Nautobot provider."""
    if not isinstance(client, NautobotRenderEventClient):
        raise DCIMInvalidDataError(
            "Nautobot render event handler received an invalid provider client"
        )
    return client


def _record(event: DCIMChangeEvent) -> Mapping[str, Any]:
    """Return the Nautobot record attached to a changelog event."""
    if event.record is None:
        raise DCIMInvalidDataError(f"Nautobot event {event.object_type} has no record")
    return event.record


def _id(value: object, description: str) -> str:
    """Return a required provider object identifier."""
    if not value:
        raise DCIMInvalidDataError(f"Nautobot event is missing {description}")
    return str(value)


def _nested_id(record: Mapping[str, Any], field: str, description: str) -> str:
    """Return an identifier from a nested Nautobot object reference."""
    value = record.get(field)
    if not isinstance(value, Mapping):
        raise DCIMInvalidDataError(f"Nautobot event is missing {description}")
    return _id(value.get("id"), description)


def _commit_message(event: DCIMChangeEvent, name: str | None = None) -> str:
    """Build the existing Nautobot-specific render commit message."""
    if name is None and event.record is not None:
        record_name = event.record.get("name")
        name = str(record_name) if record_name else None
    subject = f" on {name}" if name else ""
    return (
        f"Triggered from nb {event.object_type} {event.operation}{subject} "
        f"by {event.actor} at {event.timestamp}"
    )


def _requests(
    event: DCIMChangeEvent,
    device_ids: Iterable[str],
    *,
    name: str | None = None,
) -> tuple[RenderEventRequest, ...]:
    """Create one deduplicated render request for each affected device."""
    commit_message = _commit_message(event, name)
    return tuple(
        RenderEventRequest(device_id=device_id, commit_message=commit_message)
        for device_id in dict.fromkeys(device_ids)
    )


async def device(event: DCIMChangeEvent, _: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.device`` event."""
    if event.operation == "delete":
        return ()
    return _requests(event, [_id(_record(event).get("id"), "device id")])


async def _device_port(event: DCIMChangeEvent, _: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle interface, front-port, and rear-port events."""
    return _requests(event, [_nested_id(_record(event), "device", "port device id")])


async def interface(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.interface`` event."""
    return await _device_port(event, client)


async def rearport(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.rearport`` event."""
    return await _device_port(event, client)


async def frontport(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.frontport`` event."""
    return await _device_port(event, client)


async def cable(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.cable`` event, including compact termination references."""
    record = _record(event)
    termination_a = record.get("termination_a")
    termination_b = record.get("termination_b")
    if not isinstance(termination_a, Mapping) or not isinstance(termination_b, Mapping):
        raise DCIMInvalidDataError("Nautobot cable event has incomplete terminations")
    nautobot_client = _nautobot_client(client)
    device_ids = await asyncio.gather(
        nautobot_client.get_cable_termination_device_id(termination_a),
        nautobot_client.get_cable_termination_device_id(termination_b),
    )
    return _requests(event, device_ids)


async def cablepath(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.cablepath`` event."""
    record = _record(event)
    origin = record.get("origin")
    destination = record.get("destination")
    if not isinstance(origin, Mapping):
        raise DCIMInvalidDataError("Nautobot cable path event has no origin")
    nautobot_client = _nautobot_client(client)
    endpoints = [origin]
    if isinstance(destination, Mapping):
        endpoints.append(destination)
    device_ids = await asyncio.gather(
        *(nautobot_client.get_cable_termination_device_id(endpoint) for endpoint in endpoints)
    )
    return _requests(event, device_ids)


async def deviceredundancygroup(
    event: DCIMChangeEvent, client: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle a ``dcim.deviceredundancygroup`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    device_ids = await _nautobot_client(client).get_render_enabled_devices_matching(
        {"device_redundancy_groups": _id(record.get("id"), "device redundancy group id")}
    )
    return _requests(event, device_ids)


_CONFIG_CONTEXT_FILTER_FIELDS = (
    "locations",
    "roles",
    "device_types",
    "platforms",
    "tenant_groups",
    "tenants",
    "device_redundancy_groups",
    "tags",
)


async def configcontext(
    event: DCIMChangeEvent, client: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle an ``extras.configcontext`` event."""
    filters: dict[str, list[str]] = {}
    record = _record(event)
    for field in _CONFIG_CONTEXT_FILTER_FIELDS:
        values = record.get(field, ())
        if not isinstance(values, list):
            continue
        identifiers = [
            str(value["id"]) for value in values if isinstance(value, Mapping) and value.get("id")
        ]
        if identifiers:
            filters[field] = identifiers
    device_ids = await _nautobot_client(client).get_render_enabled_devices_matching(filters)
    return _requests(event, device_ids)


async def vrf(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle an ``ipam.vrf`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    device_ids = await _nautobot_client(client).get_render_enabled_devices_for_vrf(
        _id(record.get("id"), "VRF id")
    )
    return _requests(event, device_ids)


async def prefix(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle an ``ipam.prefix`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    locations = record.get("locations", ())
    location_ids = (
        [
            str(location["id"])
            for location in locations
            if isinstance(location, Mapping) and location.get("id")
        ]
        if isinstance(locations, list)
        else []
    )
    if not location_ids:
        return ()
    device_ids = await _nautobot_client(client).get_render_enabled_devices_matching(
        {"locations": location_ids}
    )
    return _requests(event, device_ids, name=str(record.get("prefix", "unknown")))


async def ipaddress(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle an ``ipam.ipaddress`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    device_ids = await _nautobot_client(client).get_render_enabled_devices_for_ip_address(
        _id(record.get("id"), "IP address id")
    )
    return _requests(event, device_ids, name=str(record.get("address", "unknown")))


async def autonomoussystem(
    event: DCIMChangeEvent, client: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle a ``nautobot_bgp_models.autonomoussystem`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    device_ids = await _nautobot_client(client).get_render_enabled_devices_for_autonomous_system(
        _id(record.get("asn"), "autonomous system ASN")
    )
    return _requests(event, device_ids)


async def peering(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``nautobot_bgp_models.peering`` event."""
    if event.operation == "delete":
        return ()
    record = _record(event)
    device_ids = await _nautobot_client(client).get_render_enabled_devices_for_bgp_peering(
        _id(record.get("id"), "BGP peering id")
    )
    return _requests(event, device_ids)


async def peergroup(event: DCIMChangeEvent, client: DCIMClient) -> tuple[RenderEventRequest, ...]:
    """Handle a ``nautobot_bgp_models.peergroup`` event."""
    record = _record(event)
    device_id = await _nautobot_client(client).get_render_enabled_device_for_bgp_routing_instance(
        _nested_id(record, "routing_instance", "peer group routing instance id")
    )
    return _requests(event, [device_id]) if device_id else ()


async def bgproutinginstance(
    event: DCIMChangeEvent, _: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle a ``nautobot_bgp_models.bgproutinginstance`` event."""
    return _requests(event, [_nested_id(_record(event), "device", "routing instance device id")])


async def peerendpoint(
    event: DCIMChangeEvent, client: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle a ``nautobot_bgp_models.peerendpoint`` event."""
    record = _record(event)
    device_id = await _nautobot_client(client).get_render_enabled_device_for_bgp_routing_instance(
        _nested_id(record, "routing_instance", "peer endpoint routing instance id")
    )
    return _requests(event, [device_id]) if device_id else ()


async def configmanagerdevicestatus(
    event: DCIMChangeEvent, _: DCIMClient
) -> tuple[RenderEventRequest, ...]:
    """Handle an ``nv_config_manager.configmanagerdevicestatus`` event."""
    if event.operation == "delete":
        return ()
    return _requests(event, [_id(_record(event).get("id"), "managed device id")])


_HANDLERS = {
    "dcim.device": device,
    "dcim.interface": interface,
    "dcim.rearport": rearport,
    "dcim.frontport": frontport,
    "dcim.cable": cable,
    "dcim.cablepath": cablepath,
    "dcim.deviceredundancygroup": deviceredundancygroup,
    "extras.configcontext": configcontext,
    "ipam.vrf": vrf,
    "ipam.prefix": prefix,
    "ipam.ipaddress": ipaddress,
    "nautobot_bgp_models.autonomoussystem": autonomoussystem,
    "nautobot_bgp_models.peering": peering,
    "nautobot_bgp_models.peergroup": peergroup,
    "nautobot_bgp_models.bgproutinginstance": bgproutinginstance,
    "nautobot_bgp_models.peerendpoint": peerendpoint,
    "nv_config_manager.configmanagerdevicestatus": configmanagerdevicestatus,
}


def register_render_event_handlers(registry: DCIMRenderEventRegistry) -> None:
    """Register every Nautobot changelog handler with a dispatcher."""
    for object_type, handler in _HANDLERS.items():
        registry.register_render_event_handler(object_type, handler)
