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
from nv_config_manager_dcim_nautobot_2x.events import _id, cable, configcontext, prefix
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient, NautobotProvider

from nv_config_manager.dcim import DCIMChangeEvent, RenderEventRequest


def _event(object_type: str, record: dict) -> DCIMChangeEvent:
    """Build a representative normalized Nautobot changelog event."""
    return DCIMChangeEvent(
        provider="nautobot-2x",
        operation="update",
        object_type=object_type,
        object_id=str(record.get("id", "event-id")),
        timestamp="2026-07-20T00:00:00Z",
        actor="test-user",
        record=record,
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
        "ipam.vrf",
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
    )

    requests = await cable(event, client)

    assert requests == (
        RenderEventRequest(
            device_id="leaf-1",
            commit_message="Triggered from nb dcim.cable update on uplink by test-user at 2026-07-20T00:00:00Z",
        ),
        RenderEventRequest(
            device_id="leaf-2",
            commit_message="Triggered from nb dcim.cable update on uplink by test-user at 2026-07-20T00:00:00Z",
        ),
    )
    assert client.get_cable_termination_device_id.await_count == 2


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
