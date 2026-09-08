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

import pytest

from nv_config_manager.mcp.auth import (
    MissingBearerTokenError,
    RequestAuth,
    downstream_auth_headers,
)
from nv_config_manager.mcp.clients import (
    MCPAuthError,
    _nautobot_auth_headers,
    config_store_client,
    dhcp_client,
    fetch_workflows,
    workflow_client,
)
from nv_config_manager.mcp.settings import MCPSettings


def test_request_auth_extracts_bearer_header() -> None:
    auth = RequestAuth.from_asgi_headers(
        [(b"authorization", b"Bearer token-123"), (b"x-auth-request-email", b"user@example.com")]
    )

    assert auth.bearer_authorization == "Bearer token-123"
    assert auth.identity_headers == {"X-Auth-Request-Email": "user@example.com"}


def test_downstream_auth_headers_requires_incoming_bearer_token() -> None:
    with pytest.raises(MissingBearerTokenError):
        downstream_auth_headers()


async def test_workflow_api_adapter_does_not_fallback_without_bearer_token() -> None:
    with pytest.raises(MCPAuthError, match="Bearer token"):
        await fetch_workflows(_settings(nautobot_auth_mode="jwt"), {})


async def test_downstream_clients_omit_auth_headers_when_auth_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nv_config_manager.mcp.clients.config_auth_required", lambda: False)
    settings = _settings(nautobot_auth_mode="jwt")
    store_client = config_store_client(settings)

    assert workflow_client(settings)._resolve_headers() == {}
    try:
        assert store_client._resolve_headers() == {}
    finally:
        await store_client.close()
    assert dhcp_client(settings)._resolve_headers() == {}


def test_nautobot_token_mode_uses_configured_token_without_bearer_token() -> None:
    headers = _nautobot_auth_headers(
        _settings(nautobot_auth_mode="token", nautobot_read_only_token="ro-token")
    )

    assert headers == {"Authorization": "Token ro-token"}


def test_nautobot_token_mode_requires_read_only_token() -> None:
    with pytest.raises(MCPAuthError, match="nautobot_read_only_token"):
        _nautobot_auth_headers(_settings(nautobot_auth_mode="token"))


def test_nautobot_jwt_mode_does_not_fallback_to_configured_token() -> None:
    with pytest.raises(MCPAuthError, match="Bearer token"):
        _nautobot_auth_headers(
            _settings(nautobot_auth_mode="jwt", nautobot_read_only_token="ro-token")
        )


def test_nautobot_jwt_mode_uses_configured_token_when_auth_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nv_config_manager.mcp.clients.config_auth_required", lambda: False)
    headers = _nautobot_auth_headers(
        _settings(nautobot_auth_mode="jwt", nautobot_read_only_token="ro-token")
    )

    assert headers == {"Authorization": "Token ro-token"}


def test_nautobot_jwt_mode_omits_auth_when_auth_is_disabled_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nv_config_manager.mcp.clients.config_auth_required", lambda: False)
    headers = _nautobot_auth_headers(_settings(nautobot_auth_mode="jwt"))

    assert headers == {}


def _settings(
    nautobot_auth_mode: str,
    nautobot_read_only_token: str = "",
    nautobot_token_fallback_enabled: bool = False,
) -> MCPSettings:
    return MCPSettings(
        workflow_api_url="http://workflow:9000",
        workflow_ui_url="https://config-manager.example.test",
        config_store_api_url="http://config-store:9000",
        dhcp_api_url="http://dhcp:9000",
        nautobot_url="http://nautobot",
        nautobot_read_only_token=nautobot_read_only_token,
        nautobot_verify=True,
        nautobot_auth_mode=nautobot_auth_mode,
        nautobot_token_fallback_enabled=nautobot_token_fallback_enabled,
        max_response_bytes=1000,
    )
