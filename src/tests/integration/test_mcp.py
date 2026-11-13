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
"""Verify sign-in-disabled MCP inventory against the deployed Nautobot and its secrets."""

import base64
import subprocess
from collections.abc import Generator
from typing import Any

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.dcim_provider("nautobot-2x")]


@pytest.fixture(scope="module", autouse=True)
def require_sign_in_disabled(sso_enabled: bool) -> None:
    """The regular Kind suite deploys without SSO; security runs cover other paths."""
    if sso_enabled:
        pytest.skip("This regression covers deployments with sign-in disabled")


@pytest.fixture(scope="module")
def inventory_device(nautobot_client: requests.Session, nautobot_url: str) -> dict[str, Any]:
    """Use the test administrator only to identify existing mock-topology inventory."""
    response = nautobot_client.get(
        f"{nautobot_url}/api/dcim/devices/", params={"limit": 1}, timeout=15
    )
    response.raise_for_status()
    devices = response.json()["results"]
    assert devices, "Mock topology must contain a device to test MCP inventory access"
    return devices[0]


@pytest.fixture(scope="module")
def anonymous_mcp(mcp_url: str) -> Generator[requests.Session]:
    """Initialize MCP without user Bearer tokens, API tokens, or mock SSO headers."""
    with requests.Session() as session:
        session.trust_env = False
        session.verify = False
        session.headers.update({"Accept": "application/json, text/event-stream"})
        response = session.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "nvcm-integration", "version": "1"},
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        initialized = response.json()
        assert "error" not in initialized, initialized
        session.headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
        response = session.post(
            mcp_url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=15,
        )
        response.raise_for_status()
        yield session


def _call_tool(
    session: requests.Session, url: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Require a successful MCP tool result, including downstream API authentication."""
    assert "Authorization" not in session.headers
    assert not any(name.lower().startswith("x-auth-") for name in session.headers)
    response = session.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    assert not result.get("isError", False), result
    return result["structuredContent"]


@pytest.mark.parametrize("tool", ["search_devices", "get_device_id"])
def test_mcp_resolves_inventory_without_sign_in(
    anonymous_mcp: requests.Session, mcp_url: str, inventory_device: dict[str, Any], tool: str
) -> None:
    result = _call_tool(anonymous_mcp, mcp_url, tool, {"hostname": inventory_device["name"]})
    if tool == "get_device_id":
        assert result["found"] is True
        assert result["device_id"] == inventory_device["id"]
    else:
        assert result["truncated"] is False
        assert any(device["id"] == inventory_device["id"] for device in result["data"]["results"])


def test_mcp_queries_inventory_graphql_without_sign_in(
    anonymous_mcp: requests.Session, mcp_url: str, inventory_device: dict[str, Any]
) -> None:
    result = _call_tool(
        anonymous_mcp, mcp_url, "query_nautobot", {"query": "query { devices { id name } }"}
    )
    assert result["truncated"] is False
    assert any(
        device["id"] == inventory_device["id"] for device in result["data"]["data"]["devices"]
    )


@pytest.fixture(scope="module")
def inventory_reader(config_manager_namespace: str) -> Generator[requests.Session]:
    """Use the provisioned read-only secret without exposing its value in test output."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "secret",
            "nautobot-token",
            "-n",
            config_manager_namespace,
            "-o",
            "jsonpath={.data.read-only-token}",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    if not result.stdout.strip():
        pytest.fail("Installer did not provision nautobot-token/read-only-token")
    with requests.Session() as session:
        session.trust_env = False
        session.verify = False
        session.headers["Authorization"] = f"Token {base64.b64decode(result.stdout).decode()}"
        yield session


def test_provisioned_inventory_token_allows_reads_and_denies_writes(
    inventory_reader: requests.Session, nautobot_url: str, inventory_device: dict[str, Any]
) -> None:
    read = inventory_reader.get(
        f"{nautobot_url}/api/dcim/devices/{inventory_device['id']}/", timeout=15
    )
    assert read.status_code == 200, read.text
    assert read.json()["id"] == inventory_device["id"]
    # An empty body cannot create a device even if the permission boundary regresses.
    # A writable administrator token returns validation errors (400), not denial (403).
    write = inventory_reader.post(f"{nautobot_url}/api/dcim/devices/", json={}, timeout=15)
    assert write.status_code == 403, write.text
