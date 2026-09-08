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
"""HTTP clients used by the MCP tool layer."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from nv_config_manager.common.auth import auth_required as config_auth_required
from nv_config_manager.common.client import (
    ConfigStoreClient,
    ConfigStoreException,
    DHCPClient,
    DHCPClientException,
    TemporalClient,
    TemporalClientException,
)
from nv_config_manager.dcim import DCIMError, NautobotMCPClient, create_nautobot_mcp_client
from nv_config_manager.mcp.auth import (
    MissingBearerTokenError,
    downstream_auth_headers,
    user_bearer_authorization,
)
from nv_config_manager.mcp.settings import MCPSettings

SECRET_KEY_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key)", re.I)
SECRET_LINE_RE = re.compile(
    r"(?i)^(\s*[^#\n]*(password|secret|token|api[_-]?key)[^:=\n]*\s*[:=]\s*).*$"
)
MAX_LIMIT = 100


class MCPClientError(Exception):
    """Raised when an MCP backing service call fails."""


class MCPAuthError(MCPClientError):
    """Raised when a tool needs user auth material that is not available."""


def clamp_limit(limit: int, maximum: int = MAX_LIMIT) -> int:
    """Clamp user-supplied limits to a bounded range."""
    return max(1, min(limit, maximum))


def redact_data(value: Any) -> Any:
    """Recursively redact likely secret values."""
    if isinstance(value, dict):
        return {
            key: "<redacted>" if SECRET_KEY_RE.search(str(key)) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    """Redact likely secret assignments from text blobs."""
    return "\n".join(SECRET_LINE_RE.sub(r"\1<redacted>", line) for line in value.splitlines())


def bounded_response(value: Any, max_response_bytes: int) -> dict[str, Any]:
    """Return redacted data with a byte-size boundary indicator."""
    redacted = redact_data(value)
    encoded = json.dumps(redacted, default=str, sort_keys=True)
    encoded_bytes = encoded.encode("utf-8")
    if len(encoded_bytes) <= max_response_bytes:
        return {"truncated": False, "data": redacted}
    return {
        "truncated": True,
        "max_response_bytes": max_response_bytes,
        "preview": encoded_bytes[:max_response_bytes].decode("utf-8", errors="ignore"),
    }


def reject_non_readonly_graphql(query: str) -> None:
    """Reject GraphQL operations that are not read-only queries."""
    normalized = re.sub(r"\s+", " ", query).strip().lower()
    if re.search(r"\b(mutation|subscription)\b", normalized):
        raise MCPClientError("Only read-only GraphQL query operations are allowed.")
    if not (normalized.startswith("query") or normalized.startswith("{")):
        raise MCPClientError("GraphQL operation must be a query.")


async def nautobot_graphql_query(
    settings: MCPSettings,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a bounded, read-only Nautobot GraphQL query for MCP."""
    reject_non_readonly_graphql(query)

    async def call() -> Any:
        client = nautobot_mcp_client(settings)
        if client is None:
            raise MCPClientError("The selected DCIM provider does not support Nautobot MCP tools.")
        try:
            return await client.graphql_query(query, variables)
        finally:
            await client.close()

    return await _bounded_client_call(call, settings.max_response_bytes)


async def nautobot_rest_get(
    settings: MCPSettings,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a bounded Nautobot REST GET for MCP."""

    async def call() -> Any:
        client = nautobot_mcp_client(settings)
        if client is None:
            raise MCPClientError("The selected DCIM provider does not support Nautobot MCP tools.")
        try:
            return await client.get(path.lstrip("/"), params=params)
        finally:
            await client.close()

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_dhcp_config(settings: MCPSettings, ip_version: int) -> dict[str, Any]:
    """Get a bounded sanitized DHCP configuration."""

    async def call() -> Any:
        async with dhcp_client(settings) as client:
            return await client.get_config(ip_version)

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_device_configs(
    settings: MCPSettings,
    device_id: str,
    file_type: str | None = "intended",
) -> dict[str, Any]:
    """Return a bounded response containing a list of Config Store files."""

    async def call() -> list[dict[str, object]]:
        async with config_store_client(settings, file_type or "intended") as client:
            return await client.list_device_configs(device_id, file_type=file_type)

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_device_config(
    settings: MCPSettings,
    device_id: str,
    filename: str,
    file_type: str = "intended",
    version: int | None = None,
) -> dict[str, Any]:
    """Get a bounded Config Store file."""

    async def call() -> Any:
        async with config_store_client(settings, file_type) as client:
            return await client.get_config_file(
                device_id,
                filename,
                file_type=file_type,
                version=version,
            )

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_config_versions(
    settings: MCPSettings,
    device_id: str,
    filename: str,
    file_type: str = "intended",
    limit: int = 100,
) -> dict[str, Any]:
    """List bounded Config Store versions."""

    async def call() -> Any:
        async with config_store_client(settings, file_type) as client:
            return await client.get_config_versions(
                device_id,
                filename,
                file_type=file_type,
                limit=limit,
            )

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_config_diff(
    settings: MCPSettings,
    device_id: str,
    filename: str,
    from_version: int,
    to_version: int,
    file_type: str = "intended",
) -> dict[str, Any]:
    """Get a bounded Config Store diff."""

    async def call() -> Any:
        async with config_store_client(settings, file_type) as client:
            return await client.get_config_diff(
                device_id,
                filename,
                from_version,
                to_version,
                file_type=file_type,
            )

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_workflows(
    settings: MCPSettings,
    params: dict[str, Any],
) -> dict[str, Any]:
    """List bounded Workflow API executions."""

    async def call() -> Any:
        async with workflow_client(settings) as client:
            return _add_workflow_ui_links(settings, await client.list_workflows(params=params))

    return await _bounded_client_call(call, settings.max_response_bytes)


async def fetch_workflow_detail(settings: MCPSettings, workflow_id: str) -> dict[str, Any]:
    """Get bounded Workflow API execution details."""

    async def call() -> Any:
        async with workflow_client(settings) as client:
            return _add_workflow_ui_links(settings, await client.get_workflow(workflow_id))

    return await _bounded_client_call(call, settings.max_response_bytes)


async def start_workflow(
    settings: MCPSettings,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Start a workflow through the Workflow API and return a bounded result."""

    async def call() -> Any:
        async with workflow_client(settings) as client:
            return _add_workflow_ui_links(settings, await client.start_workflow(endpoint, payload))

    return await _bounded_client_call(call, settings.max_response_bytes)


def nautobot_mcp_client(settings: MCPSettings) -> NautobotMCPClient | None:
    """Create the optional Nautobot MCP adapter from the selected provider."""
    return create_nautobot_mcp_client(headers=lambda: _nautobot_auth_headers(settings))


def workflow_client(settings: MCPSettings) -> TemporalClient:
    """Create a common Workflow API client for MCP."""
    return TemporalClient.for_mcp(
        base_url=settings.workflow_api_url,
        headers=mcp_downstream_auth_headers,
    )


def config_store_client(settings: MCPSettings, file_type: str = "intended") -> ConfigStoreClient:
    """Create a Config Store API client."""
    return ConfigStoreClient.for_mcp(
        target=settings.config_store_api_url,
        file_type=file_type,
        headers=mcp_downstream_auth_headers,
    )


def dhcp_client(settings: MCPSettings) -> DHCPClient:
    """Create a DHCP API client."""
    return DHCPClient.for_mcp(
        base_url=settings.dhcp_api_url,
        headers=mcp_downstream_auth_headers,
    )


async def _bounded_client_call(
    call: Callable[[], Awaitable[Any]],
    max_response_bytes: int,
) -> dict[str, Any]:
    try:
        return bounded_response(await call(), max_response_bytes)
    except MissingBearerTokenError as exc:
        raise MCPAuthError(str(exc)) from exc
    except (
        ConfigStoreException,
        DCIMError,
        DHCPClientException,
        TemporalClientException,
    ) as exc:
        raise MCPClientError(str(exc)) from exc


def mcp_downstream_auth_headers() -> dict[str, str]:
    """Return caller-scoped headers, or no headers when service auth is disabled."""
    try:
        return downstream_auth_headers()
    except MissingBearerTokenError:
        if not config_auth_required():
            return {}
        raise


def _nautobot_auth_headers(settings: MCPSettings) -> dict[str, str]:
    if settings.nautobot_auth_mode == "token":
        if not settings.nautobot_read_only_token:
            raise MCPAuthError(
                "Nautobot token auth is configured but [mcp] "
                "nautobot_read_only_token is not configured."
            )
        return {"Authorization": f"Token {settings.nautobot_read_only_token}"}

    bearer = user_bearer_authorization()
    if bearer:
        return {"Authorization": bearer}

    if not config_auth_required():
        if settings.nautobot_read_only_token:
            return {"Authorization": f"Token {settings.nautobot_read_only_token}"}
        return {}

    if settings.nautobot_token_fallback_enabled and settings.nautobot_read_only_token:
        return {"Authorization": f"Token {settings.nautobot_read_only_token}"}

    raise MCPAuthError(
        "Nautobot JWT auth is configured, but the MCP request did not include a Bearer token."
    )


def _add_workflow_ui_links(settings: MCPSettings, value: Any) -> Any:
    """Add Config Manager UI links to Workflow API payloads without changing href."""
    if isinstance(value, list):
        return [_add_workflow_ui_links(settings, item) for item in value]
    if not isinstance(value, dict):
        return value

    result = dict(value)
    workflows = result.get("workflows")
    if isinstance(workflows, list):
        result["workflows"] = [_add_workflow_ui_links(settings, item) for item in workflows]

    workflow_id = result.get("id")
    if isinstance(workflow_id, str) and workflow_id:
        ui_href = build_workflow_ui_href(settings, workflow_id)
        if ui_href:
            result.setdefault("ui_href", ui_href)
        href = result.get("href")
        if isinstance(href, str) and href and href != ui_href:
            result.setdefault("temporal_href", href)

    return result


def build_workflow_ui_href(settings: MCPSettings, workflow_id: str) -> str:
    """Build the end-user Config Manager UI URL for a workflow execution."""
    if not settings.workflow_ui_url:
        return ""
    return f"{settings.workflow_ui_url.rstrip('/')}/workflows/{workflow_id}"
