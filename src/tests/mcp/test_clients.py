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

from typing import Any

import pytest

from nv_config_manager.mcp import clients
from nv_config_manager.mcp.settings import MCPSettings


class FakeWorkflowClient:
    async def __aenter__(self) -> FakeWorkflowClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def start_workflow(self, endpoint: str, payload: dict[str, Any]) -> dict[str, str]:
        return {
            "id": "workflow-123",
            "href": "http://localhost:8080/namespaces/default/workflows/workflow-123",
        }

    async def get_workflow(self, workflow_id: str) -> dict[str, str]:
        return {
            "id": workflow_id,
            "href": f"http://localhost:8080/namespaces/default/workflows/{workflow_id}",
        }

    async def list_workflows(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "workflows": [
                {
                    "id": "workflow-123",
                    "href": "http://localhost:8080/namespaces/default/workflows/workflow-123",
                }
            ],
            "next_page_token": None,
        }


class FakeDCIMClient:
    """Provider client double that records MCP-scoped calls."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        """Record that the provider client was released."""
        self.closed = True

    async def graphql_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the request metadata needed by the test."""
        return {"query": query, "variables": variables}

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the request metadata needed by the test."""
        return {"path": path, "params": params}


@pytest.fixture
def settings() -> MCPSettings:
    return MCPSettings(
        workflow_api_url="http://workflow:9000",
        workflow_ui_url="https://config-manager.example.test",
        config_store_api_url="http://config-store:9000",
        dhcp_api_url="http://dhcp:9000",
        nautobot_url="http://nautobot",
        nautobot_read_only_token="token",
        nautobot_verify=True,
        nautobot_auth_mode="jwt",
        nautobot_token_fallback_enabled=False,
        max_response_bytes=10_000,
    )


@pytest.fixture(autouse=True)
def fake_workflow_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients, "workflow_client", lambda settings: FakeWorkflowClient())


def test_redact_text_handles_single_line_secret_assignments() -> None:
    assert clients.redact_text("token = secret-value") == "token = <redacted>"


async def test_start_workflow_adds_config_manager_ui_href(settings: MCPSettings) -> None:
    result = await clients.start_workflow(settings, "/ngc/backup", {"device_id": "device-1"})

    assert result["data"] == {
        "id": "workflow-123",
        "href": "http://localhost:8080/namespaces/default/workflows/workflow-123",
        "temporal_href": "http://localhost:8080/namespaces/default/workflows/workflow-123",
        "ui_href": "https://config-manager.example.test/workflows/workflow-123",
    }


async def test_workflow_detail_adds_config_manager_ui_href(settings: MCPSettings) -> None:
    result = await clients.fetch_workflow_detail(settings, "workflow-456")

    assert result["data"]["ui_href"] == "https://config-manager.example.test/workflows/workflow-456"
    assert result["data"]["temporal_href"] == (
        "http://localhost:8080/namespaces/default/workflows/workflow-456"
    )


async def test_workflow_list_adds_config_manager_ui_href(settings: MCPSettings) -> None:
    result = await clients.fetch_workflows(settings, {})

    workflow = result["data"]["workflows"][0]
    assert workflow["ui_href"] == "https://config-manager.example.test/workflows/workflow-123"
    assert workflow["temporal_href"] == (
        "http://localhost:8080/namespaces/default/workflows/workflow-123"
    )


async def test_fetch_device_configs_preserves_list_response_shape(
    settings: MCPSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_files = [{"filename": "startup.yaml", "version": 5}]

    class FakeConfigStoreClient:
        async def __aenter__(self) -> FakeConfigStoreClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def list_device_configs(
            self,
            device_id: str,
            file_type: str | None = "intended",
        ) -> list[dict[str, object]]:
            return config_files

    monkeypatch.setattr(
        clients,
        "config_store_client",
        lambda settings, file_type: FakeConfigStoreClient(),
    )

    result = await clients.fetch_device_configs(settings, "device-1")

    assert result == {"truncated": False, "data": config_files}


async def test_nautobot_graphql_uses_provider_owned_adapter(
    settings: MCPSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP obtains its Nautobot client through the selected provider."""
    dcim_client = FakeDCIMClient()
    monkeypatch.setattr(clients, "nautobot_mcp_client", lambda settings: dcim_client)
    monkeypatch.setattr(clients, "config_auth_required", lambda: False)

    result = await clients.nautobot_graphql_query(settings, "query { devices { id } }")

    assert result["data"] == {
        "query": "query { devices { id } }",
        "variables": None,
    }
    assert dcim_client.closed is True


async def test_nautobot_rest_get_uses_provider_owned_adapter(
    settings: MCPSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP REST access is delegated to the optional Nautobot provider adapter."""
    dcim_client = FakeDCIMClient()
    monkeypatch.setattr(clients, "nautobot_mcp_client", lambda settings: dcim_client)
    monkeypatch.setattr(clients, "config_auth_required", lambda: False)

    result = await clients.nautobot_rest_get(settings, "dcim/devices/", {"name": "leaf-1"})

    assert result["data"] == {
        "path": "dcim/devices/",
        "params": {"name": "leaf-1"},
    }
    assert dcim_client.closed is True
