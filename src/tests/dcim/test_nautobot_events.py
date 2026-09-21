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
"""Tests for Nautobot-owned render event interpretation."""

from unittest.mock import AsyncMock

import pytest
from nv_config_manager_dcim.errors import DCIMInvalidDataError
from nv_config_manager_dcim_nautobot_2x.events import (
    _id,
    cable,
    configcontext,
    prefix,
    relationshipassociation,
    vlan,
)
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient, NautobotProvider

from nv_config_manager.dcim import DCIMChangeEvent, RenderEventRequest


def _event(
    object_type: str,
    record: dict,
    changed_fields: tuple[str, ...] = (),
    operation: str = "update",
) -> DCIMChangeEvent:
    """Build a representative normalized Nautobot changelog event."""
    return DCIMChangeEvent(
        provider="nautobot-2x",
        operation=operation,
        object_type=object_type,
        object_id=str(record.get("id", "event-id")),
        timestamp="2026-07-20T00:00:00Z",
        actor="test-user",
        record=record,
        changed_fields=changed_fields,
    )


def _client() -> NautobotDCIMClient:
    """Build a provider client without opening an HTTP session."""
    return NautobotDCIMClient("https://nautobot.example", "token")


@pytest.mark.parametrize("value", [None, "", 0, False])
def test_event_id_rejects_falsy_values(value: object) -> None:
    """Falsy identifiers are consistently treated as missing event data."""
    with pytest.raises(DCIMInvalidDataError, match="missing device id"):
        _id(value, "device id")


def test_nautobot_provider_registers_its_event_types():
    """The provider, not core render code, chooses event types and handlers."""

    class Registry:
        handlers: dict[str, object] = {}

        def register_render_event_handler(self, object_type, handler) -> None:
            self.handlers[object_type] = handler

    registry = Registry()
    NautobotProvider().register_render_event_handlers(registry)

    assert {
        "dcim.cable",
        "extras.configcontext",
        "extras.relationshipassociation",
        "ipam.vrf",
        "ipam.vlan",
        "nautobot_bgp_models.peering",
        "nv_config_manager.configmanagerdevicestatus",
    } <= registry.handlers.keys()


@pytest.mark.asyncio
async def test_cable_handler_resolves_compact_nautobot_terminations():
    """Compact Nautobot termination references are resolved by the provider."""
    client = _client()
    client.get_cable_termination_device_id = AsyncMock(side_effect=["leaf-1", "leaf-2"])
    event = _event(
        "dcim.cable",
        {
            "id": "cable-1",
            "name": "uplink",
            "termination_a": {"id": "port-1", "url": "/api/dcim/interfaces/port-1/"},
            "termination_b": {"id": "port-2", "url": "/api/dcim/interfaces/port-2/"},
        },
        operation="create",
    )

    requests = await cable(event, client)

    assert requests == (
        RenderEventRequest(
            device_id="leaf-1",
            commit_message="Triggered from nb dcim.cable create on uplink by test-user at 2026-07-20T00:00:00Z",
        ),
        RenderEventRequest(
            device_id="leaf-2",
            commit_message="Triggered from nb dcim.cable create on uplink by test-user at 2026-07-20T00:00:00Z",
        ),
    )
    assert client.get_cable_termination_device_id.await_count == 2


@pytest.mark.asyncio
async def test_cable_handler_skips_all_updates():
    """Nautobot cable updates cannot change immutable terminations."""
    client = _client()
    client.get_cable_termination_device_id = AsyncMock()
    event = _event(
        "dcim.cable",
        {"id": "cable-1"},
        changed_fields=("status", "label", "color"),
    )

    assert await cable(event, client) == ()
    client.get_cable_termination_device_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_cable_handler_renders_delete_events():
    """Deleting a cable changes topology and renders both former endpoints."""
    client = _client()
    client.get_cable_termination_device_id = AsyncMock(side_effect=["leaf-1", "leaf-2"])
    record = {
        "id": "cable-1",
        "termination_a": {"id": "port-1"},
        "termination_b": {"id": "port-2"},
    }

    assert len(await cable(_event("dcim.cable", record, operation="delete"), client)) == 2


@pytest.mark.asyncio
async def test_config_context_handler_uses_nautobot_filtering():
    """Nautobot owns config-context filter interpretation and affected-device lookup."""
    client = _client()
    client.get_render_enabled_devices_matching = AsyncMock(return_value=["leaf-1", "leaf-2"])
    event = _event(
        "extras.configcontext",
        {
            "id": "context-1",
            "name": "base-settings",
            "locations": [{"id": "site-1"}],
            "roles": [{"id": "leaf"}],
            "platforms": [],
        },
    )

    requests = await configcontext(event, client)

    client.get_render_enabled_devices_matching.assert_awaited_once_with(
        {"locations": ["site-1"], "roles": ["leaf"]}
    )
    assert [request.device_id for request in requests] == ["leaf-1", "leaf-2"]


@pytest.mark.asyncio
async def test_prefix_without_locations_does_not_request_a_render():
    """Provider-specific impact rules can ignore events without usable scope."""
    client = _client()
    client.get_render_enabled_devices_matching = AsyncMock()

    requests = await prefix(
        _event("ipam.prefix", {"id": "prefix-1", "prefix": "10.0.0.0/8", "locations": []}),
        client,
    )

    assert requests == ()
    client.get_render_enabled_devices_matching.assert_not_awaited()


@pytest.mark.asyncio
async def test_vlan_handler_renders_devices_with_assigned_interfaces():
    """A VLAN update renders every managed device with an interface using it."""
    client = _client()
    client.find_switches_by_vlan = AsyncMock(return_value=["leaf-1", "leaf-2", "leaf-1"])

    requests = await vlan(_event("ipam.vlan", {"id": "vlan-1", "vid": 900, "name": "data"}), client)

    client.find_switches_by_vlan.assert_awaited_once_with(900)
    assert [request.device_id for request in requests] == ["leaf-1", "leaf-2"]


@pytest.mark.asyncio
async def test_vlan_delete_is_ignored_after_interface_associations_are_gone():
    """A deleted VLAN cannot have remaining interface owners to resolve."""
    client = _client()
    client.find_switches_by_vlan = AsyncMock()

    assert await vlan(_event("ipam.vlan", {"id": "vlan-1"}, operation="delete"), client) == ()
    client.find_switches_by_vlan.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_address_relationship_renders_devices_using_its_vlan():
    """Creating or deleting the helper link re-renders devices using its VLAN."""
    client = _client()
    client.get_relationship_source_record = AsyncMock(
        return_value=("ipam.vlan", {"id": "vlan-1", "vid": 900, "name": "data"})
    )
    client.find_switches_by_vlan = AsyncMock(return_value=["leaf-1"])
    event = _event(
        "extras.relationshipassociation",
        {
            "id": "association-1",
            "relationship": {"id": "relationship-1", "key": "vlan_to_helper_address"},
            "source_type": "ipam.vlan",
            "source_id": "vlan-1",
            "destination_type": "ipam.ipaddress",
            "destination_id": "address-1",
        },
        operation="delete",
    )

    requests = await relationshipassociation(event, client)

    client.get_relationship_source_record.assert_awaited_once_with(event.record)
    client.find_switches_by_vlan.assert_awaited_once_with(900)
    assert [request.device_id for request in requests] == ["leaf-1"]
    assert requests[0].commit_message.startswith(
        "Triggered from nb extras.relationshipassociation delete"
    )


@pytest.mark.asyncio
async def test_prefix_gateway_relationship_uses_prefix_event_resolution():
    """Relationship sources reuse the corresponding model's affected-device logic."""
    client = _client()
    client.get_relationship_source_record = AsyncMock(
        return_value=(
            "ipam.prefix",
            {
                "id": "prefix-1",
                "prefix": "10.0.0.0/24",
                "locations": [{"id": "site-1"}],
            },
        )
    )
    client.get_render_enabled_devices_matching = AsyncMock(return_value=["leaf-1"])
    event = _event(
        "extras.relationshipassociation",
        {
            "id": "association-1",
            "relationship": {"id": "relationship-1", "key": "prefix_to_gateway"},
            "source_type": "ipam.prefix",
            "source_id": "prefix-1",
        },
        operation="create",
    )

    requests = await relationshipassociation(event, client)

    client.get_render_enabled_devices_matching.assert_awaited_once_with({"locations": ["site-1"]})
    assert [request.device_id for request in requests] == ["leaf-1"]
    assert requests[0].commit_message.startswith(
        "Triggered from nb extras.relationshipassociation create"
    )


@pytest.mark.asyncio
async def test_unsupported_relationship_source_is_ignored():
    """Relationship source models without event logic do not cause renders."""
    client = _client()
    client.get_relationship_source_record = AsyncMock(return_value=None)

    requests = await relationshipassociation(
        _event("extras.relationshipassociation", {"id": "association-1"}), client
    )

    assert requests == ()


@pytest.mark.asyncio
async def test_find_switches_by_vlan_uses_vlan_reverse_relations():
    """The provider reads tagged and untagged interface owners directly from the VLAN."""
    client = _client()
    client.graphql_query = AsyncMock(
        return_value={
            "data": {
                "vlans": [
                    {
                        "interfaces_as_tagged": [
                            {"device": {"id": "leaf-1"}},
                            {"device": {"id": "leaf-2"}},
                        ],
                        "interfaces_as_untagged": [
                            {"device": {"id": "leaf-1"}},
                        ],
                    }
                ]
            }
        }
    )

    assert await client.find_switches_by_vlan(900) == ["leaf-1", "leaf-2"]
    query, variables = client.graphql_query.await_args.args
    assert "query FindSwitchesByVLAN" in query
    assert "vlans(vid: $vid)" in query
    assert "interfaces_as_tagged" in query
    assert "interfaces_as_untagged" in query
    assert variables == {"vid": [900]}


@pytest.mark.asyncio
async def test_relationship_source_resolution_fetches_a_vlan():
    """A VLAN relationship source is fetched for source-model event dispatch."""
    client = _client()
    client.get = AsyncMock(return_value={"id": "vlan-1", "vid": 900})
    association = {
        "relationship": {"id": "relationship-1", "key": "vlan_to_helper_address"},
        "source_type": "ipam.vlan",
        "source_id": "vlan-1",
    }

    assert await client.get_relationship_source_record(association) == (
        "ipam.vlan",
        {"id": "vlan-1", "vid": 900},
    )
    client.get.assert_awaited_once_with("ipam/vlans/vlan-1/")


@pytest.mark.asyncio
async def test_relationship_source_resolution_fetches_a_prefix():
    """A prefix relationship source uses the same generic resolution path."""
    client = _client()
    client.get = AsyncMock(return_value={"id": "prefix-1", "prefix": "10.0.0.0/24"})

    source = await client.get_relationship_source_record(
        {"source_type": "ipam.prefix", "source_id": "prefix-1"}
    )

    assert source == ("ipam.prefix", {"id": "prefix-1", "prefix": "10.0.0.0/24"})
    client.get.assert_awaited_once_with("ipam/prefixes/prefix-1/")


@pytest.mark.asyncio
async def test_unknown_relationship_source_resolution_is_ignored():
    """Unsupported source models do not create an unbounded render fan-out."""
    client = _client()
    client.get = AsyncMock()

    assert (
        await client.get_relationship_source_record(
            {"source_type": "circuits.circuit", "source_id": "circuit-1"}
        )
        is None
    )
    client.get.assert_not_awaited()
