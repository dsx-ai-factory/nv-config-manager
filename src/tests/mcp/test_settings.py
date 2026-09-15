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

from configparser import ConfigParser

import pytest

from nv_config_manager.mcp import settings as mcp_settings
from nv_config_manager.mcp.settings import MCPOAuthSettings, MCPSettings


def _config(nautobot_server: str, auth_mode: str = "auto") -> ConfigParser:
    config = ConfigParser()
    config["mcp"] = {
        "use_internal_endpoints": "true",
        "nautobot_auth_mode": auth_mode,
        "nautobot_read_only_token": "read-only-token",
    }
    config["temporal"] = {
        "api_service": "http://workflow:9000",
        "api_url": "https://workflow.example.test",
        "ui_url": "https://config-manager.example.test",
    }
    config["config_store.client"] = {
        "api_service": "http://config-store:9000",
        "api_url": "https://config-store.example.test",
    }
    config["dhcp"] = {
        "api_service": "http://dhcp:9000",
        "api_url": "https://dhcp.example.test",
    }
    config["dcim"] = {
        "provider": "nautobot-2x",
        "server": nautobot_server,
        "token": "rw-token",
        "verify": "true",
    }
    return config


def test_nautobot_auth_auto_uses_jwt_for_local_nautobot() -> None:
    settings = MCPSettings.from_config(_config("http://nv-config-manager-nautobot"))

    assert settings.nautobot_mcp_enabled is True
    assert settings.nautobot_auth_mode == "jwt"
    assert settings.workflow_ui_url == "https://config-manager.example.test"


def test_nautobot_auth_auto_uses_token_for_external_nautobot() -> None:
    settings = MCPSettings.from_config(_config("https://nautobot.example.test"))

    assert settings.nautobot_auth_mode == "token"
    assert settings.nautobot_read_only_token == "read-only-token"


def test_nautobot_read_only_token_does_not_fallback_to_rw_token() -> None:
    config = _config("https://nautobot.example.test")
    del config["mcp"]["nautobot_read_only_token"]

    settings = MCPSettings.from_config(config)

    assert settings.nautobot_auth_mode == "token"
    assert settings.nautobot_read_only_token == ""


def test_nautobot_auth_explicit_override() -> None:
    settings = MCPSettings.from_config(_config("https://nautobot.example.test", auth_mode="jwt"))

    assert settings.nautobot_auth_mode == "jwt"


def test_mcp_tracks_the_selected_dcim_provider() -> None:
    config = _config("https://nautobot.example.test")
    config["dcim"] = {"provider": "synthetic"}

    settings = MCPSettings.from_config(config)

    assert settings.dcim_provider_name == "synthetic"


def test_non_nautobot_provider_does_not_require_nautobot_settings() -> None:
    """Generic MCP tools remain available when no Nautobot connection exists."""
    config = _config("https://nautobot.example.test")
    config["dcim"] = {"provider": "synthetic"}

    settings = MCPSettings.from_config(config)

    assert settings.dcim_provider_name == "synthetic"
    assert settings.nautobot_mcp_enabled is False
    assert settings.nautobot_url == ""


def test_nautobot_mcp_settings_follow_provider_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A versioned provider name alone does not enable Nautobot MCP settings."""
    config = _config("https://nautobot.example.test")
    config["dcim"]["provider"] = "nautobot-3x"
    monkeypatch.setattr(mcp_settings, "supports_nautobot_mcp", lambda _config: False)

    settings = MCPSettings.from_config(config)

    assert settings.nautobot_mcp_enabled is False
    assert settings.nautobot_url == ""


def test_nautobot_mcp_settings_support_a_capable_nautobot_3x_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nautobot 3.x receives the same MCP auth settings through the protocol."""
    config = _config("https://nautobot.example.test")
    config["dcim"]["provider"] = "nautobot-3x"
    monkeypatch.setattr(mcp_settings, "supports_nautobot_mcp", lambda _config: True)

    settings = MCPSettings.from_config(config)

    assert settings.nautobot_mcp_enabled is True
    assert settings.nautobot_url == "https://nautobot.example.test"


def test_workflow_ui_url_falls_back_to_base_hostname() -> None:
    config = _config("http://nv-config-manager-nautobot")
    del config["temporal"]["ui_url"]

    settings = MCPSettings.from_config(config)

    assert settings.workflow_ui_url == "https://example.test"


def test_mcp_oauth_settings_disabled_without_section() -> None:
    settings = MCPOAuthSettings.from_config(ConfigParser())

    assert settings.enabled is False


def test_mcp_oauth_settings_parse_metadata_config() -> None:
    settings = MCPOAuthSettings.from_config(_oauth_config())

    assert settings.enabled is True
    assert settings.resource_url == "https://svc-mcp.example.test/mcp"
    assert settings.issuer_url == "https://idp.example.test/realms/nvcm"
    assert settings.client_id == "nvcm-cli"
    assert settings.scopes == ("openid", "email", "profile")
    assert (
        settings.resource_metadata_url
        == "https://svc-mcp.example.test/.well-known/oauth-protected-resource/mcp"
    )
    assert settings.authorization_server_url == "https://svc-mcp.example.test"
    assert settings.forward_resource_parameter is True
    assert settings.oauth_proxy_paths == frozenset()
    assert settings.well_known_paths == {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
    }


def test_mcp_oauth_settings_supports_resource_parameter_compatibility_proxy() -> None:
    config = _oauth_config()
    config["mcp.oauth"]["forward_resource_parameter"] = "false"

    settings = MCPOAuthSettings.from_config(config)

    assert settings.forward_resource_parameter is False
    assert settings.authorization_proxy_url == "https://svc-mcp.example.test/oauth/authorize"
    assert settings.callback_proxy_url == "https://svc-mcp.example.test/oauth/callback"
    assert settings.token_proxy_url == "https://svc-mcp.example.test/oauth/token"
    assert settings.oauth_proxy_paths == {
        "/oauth/authorize",
        "/oauth/callback",
        "/oauth/token",
    }


def test_mcp_oauth_well_known_paths_follow_resource_url_path() -> None:
    config = _oauth_config()
    config["mcp.oauth"]["resource_url"] = "https://svc-mcp.example.test/custom/mcp/"

    settings = MCPOAuthSettings.from_config(config)

    assert (
        settings.resource_metadata_url
        == "https://svc-mcp.example.test/.well-known/oauth-protected-resource/custom/mcp"
    )
    assert settings.well_known_paths == {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/custom/mcp",
        "/.well-known/oauth-authorization-server",
    }


@pytest.mark.parametrize(
    "resource_url",
    [
        "svc-mcp.example.test/mcp",
        "http://svc-mcp.example.test/mcp",
        "https://svc-mcp.example.test/mcp?tenant=test",
        "https://svc-mcp.example.test/mcp#fragment",
    ],
)
def test_mcp_oauth_settings_requires_rfc_9728_resource_url(resource_url: str) -> None:
    config = _oauth_config()
    config["mcp.oauth"]["resource_url"] = resource_url

    with pytest.raises(ValueError, match="resource_url"):
        MCPOAuthSettings.from_config(config)


def _oauth_config() -> ConfigParser:
    config = ConfigParser()
    config["mcp.oauth"] = {
        "enabled": "true",
        "resource_url": "https://svc-mcp.example.test/mcp/",
        "issuer_url": "https://idp.example.test/realms/nvcm/",
        "client_id": "nvcm-cli",
        "scopes": "openid,email profile",
        "authorization_endpoint": "https://idp.example.test/realms/nvcm/auth",
        "token_endpoint": "https://idp.example.test/realms/nvcm/token",
        "jwks_uri": "https://idp.example.test/realms/nvcm/certs/",
    }
    return config
