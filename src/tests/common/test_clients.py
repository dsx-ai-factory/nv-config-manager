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
"""Tests for nv_config_manager.common.client module - internal auth headers."""

import os
from configparser import ConfigParser
from unittest.mock import patch

import pytest

from nv_config_manager.common.client import TemporalClient, ZTPClient
from nv_config_manager_workflows.clients.render import RenderClient

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_cls", "base_url"),
    [
        (ZTPClient, "http://ztp-service:9000"),
        (RenderClient, "http://render-service:9000"),
    ],
)
async def test_retry_client_does_not_own_shared_connector(client_cls, base_url):
    """Per-request sessions must not close the client-level connector."""
    client = client_cls(base_url=base_url)

    try:
        with patch("nv_config_manager_workflows.clients._http.RetryClient") as retry_client:
            client._new_session()

        retry_client.assert_called_once()
        kwargs = retry_client.call_args.kwargs
        assert kwargs["connector"] is client.connector
        assert kwargs["connector_owner"] is False
    finally:
        await client.connector.close()


class TestZTPClientInternalAuth:
    """Tests for ZTPClient internal auth headers."""

    @pytest.mark.asyncio
    async def test_init_with_headers(self):
        """Test client initialization with custom headers."""
        headers = {
            "X-Auth-Request-Email": "test-service",
            "X-Auth-Request-Groups": "nv-config-manager",
        }
        client = ZTPClient(
            base_url="http://ztp-service:9000",
            headers=headers,
        )
        assert client.base_url == "http://ztp-service:9000"
        assert client._headers == headers

    @pytest.mark.asyncio
    async def test_from_config_internal_endpoint(self):
        """Test from_config with internal endpoint uses callable headers."""
        config = ConfigParser()
        config.add_section("ztp")
        config.set("ztp", "api_service", "http://internal-ztp:9000")
        config.set("ztp", "api_url", "https://external-ztp.example.com")
        config.set("ztp", "use_internal_endpoint", "true")

        with patch.dict(os.environ, {"HOSTNAME": "nv-config-manager-render-5f8d9c7b6-abc12"}):
            client = ZTPClient.from_config(config)

        assert client.base_url == "http://internal-ztp:9000"
        assert callable(client._headers)
        resolved = client._headers()
        assert "X-Auth-Request-Email" in resolved

    @pytest.mark.asyncio
    async def test_from_config_external_endpoint(self):
        """Test from_config with external endpoint does not include auth headers."""
        config = ConfigParser()
        config.add_section("ztp")
        config.add_section("mtls")
        config.set("ztp", "api_service", "http://internal-ztp:9000")
        config.set("ztp", "api_url", "https://external-ztp.example.com")
        config.set("ztp", "use_internal_endpoint", "false")

        client = ZTPClient.from_config(config)

        assert client.base_url == "https://external-ztp.example.com"
        assert client._headers is None


class TestRenderClientInternalAuth:
    """Tests for RenderClient internal auth headers."""

    @pytest.mark.asyncio
    async def test_init_with_headers(self):
        """Test client initialization with custom headers."""
        headers = {
            "X-Auth-Request-Email": "test-service",
            "X-Auth-Request-Groups": "nv-config-manager",
        }
        client = RenderClient(
            base_url="http://render-service:9000",
            headers=headers,
        )
        assert client.base_url == "http://render-service:9000"
        assert client._headers == headers


class TestTemporalClientInternalAuth:
    """Tests for TemporalClient internal auth headers."""

    def test_init_with_headers(self):
        """Test client initialization with custom headers."""
        headers = {
            "X-Auth-Request-Email": "test-service",
            "X-Auth-Request-Groups": "nv-config-manager",
        }
        client = TemporalClient(
            base_url="http://temporal-api:9000",
            user_domain="nvidia.com",
            headers=headers,
        )
        assert client.base_url == "http://temporal-api:9000"
        assert client._headers == headers

    def test_from_config_internal_endpoint(self):
        """Test from_config with internal endpoint uses callable headers."""
        config = ConfigParser()
        config.add_section("temporal")
        config.set("temporal", "api_service", "http://internal-temporal:9000")
        config.set("temporal", "api_url", "https://external-temporal.example.com")
        config.set("temporal", "use_internal_endpoint", "true")
        config.set("temporal", "user_domain", "nvidia.com")

        with patch.dict(os.environ, {"HOSTNAME": "nv-config-manager-worker-5f8d9c7b6-abc12"}):
            client = TemporalClient.from_config(config)

        assert client.base_url == "http://internal-temporal:9000"
        assert callable(client._headers)
        resolved = client._headers()
        assert "X-Auth-Request-Email" in resolved

    def test_from_config_external_endpoint(self):
        """Test from_config with external endpoint does not include auth headers."""
        config = ConfigParser()
        config.add_section("temporal")
        config.add_section("mtls")
        config.set("temporal", "api_service", "http://internal-temporal:9000")
        config.set("temporal", "api_url", "https://external-temporal.example.com")
        config.set("temporal", "use_internal_endpoint", "false")
        config.set("temporal", "user_domain", "nvidia.com")

        client = TemporalClient.from_config(config)

        assert client.base_url == "https://external-temporal.example.com"
        assert client._headers is None

    @pytest.mark.asyncio
    async def test_session_includes_headers(self):
        """Test that async context manager creates session with headers."""
        headers = {
            "X-Auth-Request-Email": "test-service",
            "X-Auth-Request-Groups": "nv-config-manager",
        }
        client = TemporalClient(
            base_url="http://temporal-api:9000",
            user_domain="nvidia.com",
            headers=headers,
        )
        async with client:
            assert client._session is not None
            assert client._session._default_headers is not None
