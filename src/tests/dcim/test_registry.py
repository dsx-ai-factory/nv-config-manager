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
"""Tests for NVCM's INI-to-DCIM-SDK service adapter."""

from __future__ import annotations

from configparser import ConfigParser
from unittest.mock import AsyncMock

import pytest
from nv_config_manager_dcim import DCIMInvalidDataError, DCIMProviderConfigurationError
from nv_config_manager_dcim import registry as sdk_registry
from nv_config_manager_dcim_nautobot_2x.client import NautobotException
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient, NautobotProvider

from nv_config_manager.dcim import (
    create_dcim_client,
    create_nautobot_mcp_client,
    normalize_dcim_event,
    provider_settings,
    supports_nautobot_mcp,
)
from nv_config_manager.dcim import registry as service_registry


class FakeEntryPoint:
    """Minimal entry point used to isolate the service adapter."""

    name = "nautobot-2x"

    def load(self):
        """Return the provider target."""
        return NautobotProvider


@pytest.fixture(autouse=True)
def nautobot_provider(monkeypatch):
    """Install only the reference provider for each test."""
    monkeypatch.setattr(sdk_registry, "_entry_points_for_group", lambda: (FakeEntryPoint(),))


def _config() -> ConfigParser:
    config = ConfigParser()
    config.read_dict(
        {
            "dcim": {"provider": "nautobot-2x"},
            "dcim.nautobot-2x": {
                "server": "https://dcim.example",
                "public_url": "https://public-dcim.example",
                "token": "token",
                "verify": "false",
            },
        }
    )
    return config


def test_service_adapter_creates_the_selected_sdk_provider() -> None:
    """Core translates its INI into plain settings before creating a client."""
    client = create_dcim_client(_config())

    assert isinstance(client, NautobotDCIMClient)
    assert client.nautobot_url == "https://dcim.example/"
    assert (
        client.get_device_ui_url("device-1") == "https://public-dcim.example/dcim/devices/device-1/"
    )


def test_nautobot_provider_owns_identifier_shape_validation() -> None:
    """The Nautobot provider accepts only canonical UUID identifiers."""
    client = NautobotDCIMClient("https://dcim.example", "token")

    assert client.is_valid_device_id("910b85f8-e83c-48ad-9bbd-12b15e97a2d4") is True
    assert client.is_valid_location_id("b6f4972a-c6ab-4be1-96ac-72f4efc4f328") is True
    assert client.is_valid_device_id("910B85F8-E83C-48AD-9BBD-12B15E97A2D4") is False
    assert client.is_valid_device_id("{910b85f8-e83c-48ad-9bbd-12b15e97a2d4}") is False
    assert client.is_valid_device_id("42") is False
    assert client.is_valid_location_id("site-1") is False


@pytest.mark.asyncio
async def test_nautobot_location_metadata_uses_direct_id_lookup() -> None:
    """Location validation fetches one indexed record rather than listing locations."""
    location_id = "b6f4972a-c6ab-4be1-96ac-72f4efc4f328"
    client = NautobotDCIMClient("https://dcim.example", "token")
    client.graphql_query = AsyncMock(
        return_value={"data": {"location": {"id": location_id, "name": "SJC01"}}}
    )

    location = await client.get_location_metadata(location_id)

    assert location is not None
    assert location.id == location_id
    assert location.name == "SJC01"
    query, variables = client.graphql_query.await_args.args
    assert "query GetLocationById" in query
    assert variables == {"id": location_id}


@pytest.mark.asyncio
async def test_nautobot_device_metadata_propagates_lookup_failures() -> None:
    """Provider failures remain distinguishable from an absent device."""
    client = NautobotDCIMClient("https://dcim.example", "token")
    client.graphql_query = AsyncMock(side_effect=RuntimeError("DCIM unavailable"))

    with pytest.raises(RuntimeError, match="DCIM unavailable"):
        await client.get_device_metadata("910b85f8-e83c-48ad-9bbd-12b15e97a2d4")


@pytest.mark.asyncio
async def test_cable_termination_reference_cycles_are_rejected() -> None:
    """Malformed REST references cannot recurse indefinitely."""
    client = NautobotDCIMClient("https://dcim.example", "token")
    client.get = AsyncMock(return_value={"url": "/api/dcim/interfaces/interface-1/"})

    with pytest.raises(DCIMInvalidDataError, match="reference cycle"):
        await client.get_cable_termination_device_id({"url": "/api/dcim/interfaces/interface-1/"})

    client.get.assert_awaited_once_with("dcim/interfaces/interface-1/")


@pytest.mark.asyncio
async def test_idempotent_delete_uses_structured_http_status() -> None:
    """Retry cleanup treats a structured 404 as success without parsing its message."""
    client = NautobotDCIMClient("https://dcim.example", "token")
    client.delete = AsyncMock(side_effect=NautobotException("gone", status_code=404))

    await client._delete_ib_pkey_resource_if_present("resource/1/", "resource")


def test_provider_specific_settings_override_generic_dcim_values() -> None:
    """Provider-specific INI sections remain a service concern."""
    config = _config()
    config["dcim"]["server"] = "https://generic.example"

    assert provider_settings(config)["server"] == "https://dcim.example"


def test_provider_settings_include_portable_dcim_options() -> None:
    """Generic provider options reach the SDK and override generic values."""
    config = ConfigParser()
    config.read_dict(
        {
            "dcim": {"provider": "nautobot-2x", "server": "https://generic.example"},
            "dcim.options": {"token": "portable-token", "verify": "false"},
        }
    )

    assert provider_settings(config) == {
        "server": "https://generic.example",
        "token": "portable-token",
        "verify": "false",
    }


def test_legacy_nautobot_settings_remain_supported_until_v2() -> None:
    """A 1.x deployment can still configure the built-in provider through [nautobot]."""
    config = ConfigParser()
    config.read_dict(
        {
            "nautobot": {
                "server": "https://legacy.example",
                "token": "legacy-token",
                "verify": "false",
            }
        }
    )

    with pytest.warns(DeprecationWarning, match=r"\[nautobot\].*deprecated"):
        client = create_dcim_client(config)

    assert isinstance(client, NautobotDCIMClient)
    assert client.nautobot_url == "https://legacy.example/"


def test_new_dcim_settings_override_legacy_nautobot_values() -> None:
    """Migrated values take precedence while an incomplete migration remains compatible."""
    config = ConfigParser()
    config.read_dict(
        {
            "nautobot": {
                "server": "https://legacy.example",
                "token": "legacy-token",
                "verify": "true",
            },
            "dcim": {
                "provider": "nautobot-2x",
                "server": "https://generic.example",
            },
            "dcim.options": {"verify": "false"},
            "dcim.nautobot-2x": {"token": "provider-token"},
        }
    )

    with pytest.warns(DeprecationWarning):
        settings = provider_settings(config)

    assert settings["server"] == "https://generic.example"
    assert settings["token"] == "provider-token"
    assert settings["verify"] == "false"


def test_external_provider_does_not_receive_legacy_nautobot_settings() -> None:
    """The compatibility fallback is isolated to the built-in Nautobot provider."""
    config = ConfigParser()
    config.read_dict(
        {
            "nautobot": {"server": "https://legacy.example", "token": "legacy-token"},
            "dcim": {"provider": "external", "server": "https://external.example"},
        }
    )

    assert provider_settings(config) == {"server": "https://external.example"}


def test_nautobot_mcp_is_gated_by_provider_capability() -> None:
    """The deliberately Nautobot-specific MCP adapter remains optional."""
    client = create_nautobot_mcp_client(lambda: {"Authorization": "Bearer caller"}, _config())

    assert isinstance(client, NautobotDCIMClient)
    assert client._resolve_headers() == {"Authorization": "Bearer caller"}


def test_nautobot_mcp_capability_does_not_depend_on_provider_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nautobot 2.x and 3.x providers opt in through the SDK protocol."""
    config = _config()
    config["dcim"]["provider"] = "nautobot-3x"

    monkeypatch.setattr(service_registry, "get_sdk_dcim_provider", lambda _name: object())
    assert supports_nautobot_mcp(config) is False

    monkeypatch.setattr(service_registry, "get_sdk_dcim_provider", lambda _name: NautobotProvider())
    assert supports_nautobot_mcp(config) is True


def test_legacy_nautobot_events_are_normalized_at_the_provider_boundary() -> None:
    """Core receives only the SDK event envelope."""
    event = normalize_dcim_event(
        {
            "model": "dcim.device",
            "event": "update",
            "record": {"id": "device-1", "name": "leaf-1"},
            "@timestamp": "2026-07-20T00:00:00Z",
            "request": {"user": "automation"},
        },
        _config(),
    )

    assert event.provider == "nautobot-2x"
    assert event.object_id == "device-1"


def test_provider_settings_require_connection_details() -> None:
    """Provider validation occurs after service configuration translation."""
    config = ConfigParser()
    config.read_dict({"dcim": {"provider": "nautobot-2x"}})

    with pytest.raises(DCIMProviderConfigurationError, match="server, token"):
        create_dcim_client(config)


def test_event_provider_mismatch_is_rejected() -> None:
    """One service deployment cannot consume another provider's event stream."""
    with pytest.raises(DCIMInvalidDataError, match="does not match configured provider"):
        normalize_dcim_event(
            {
                "contract_version": "1.0",
                "provider": "another-provider",
                "operation": "update",
                "object_type": "dcim.device",
                "object_id": "device-1",
                "timestamp": "2026-07-20T00:00:00Z",
            },
            _config(),
        )
