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
"""Tests for AsyncConfigStoreClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio

from nv_config_manager.common.client import (
    ConfigStoreClient,
    ConfigStoreFileNotFound,
)

MOCK_GET_RESPONSE = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "device_uuid": "123e4567-e89b-12d3-a456-426614174000",
    "filename": "startup.yaml",
    "file_type": "intended",
    "version": 5,
    "content": "test content",
    "content_hash": "abc123def456",
    "author": "ngc-cfa@nvidia.com",
    "commit_message": "Test commit",
    "created_at": "2024-11-26T20:43:48Z",
}

MOCK_BATCH_POST_RESPONSE = {
    "created": [
        {
            "version": 6,
            "file_type": "intended",
            "author": "testuser@config-manager.example.com",
            "commit_message": "test commit message",
            "created_at": "2024-11-26T20:43:48Z",
            "content_hash": "abc123def789",
        }
    ],
    "skipped": [],
}


@pytest_asyncio.fixture
async def async_config_store_client():
    """Create a ConfigStoreClient instance for testing."""
    client = ConfigStoreClient(
        target="http://config-store.example.com",
        file_type="intended",
        ui_url="https://config-manager.example.com",
    )
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_init(async_config_store_client):
    """Test client initialization."""
    assert async_config_store_client.target == "http://config-store.example.com"
    assert async_config_store_client.file_type == "intended"
    assert async_config_store_client.base_url == "http://config-store.example.com"
    assert async_config_store_client.config_url == "http://config-store.example.com/v1/config"


@pytest.mark.asyncio
async def test_init_with_ca_cert_disabled():
    """Test client initialization with CA certificate disabled."""
    client = ConfigStoreClient(
        target="http://config-store.example.com",
        file_type="intended",
        ui_url="https://config-manager.example.com",
        verify=False,
    )
    await client.close()


def _mock_retry_client(response_data):
    """Create a mock RetryClient context manager returning response_data."""
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value=response_data)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


@pytest.mark.asyncio
async def test_load_file(async_config_store_client):
    """Test loading a file from config store."""
    mock_session = _mock_retry_client(MOCK_GET_RESPONSE)

    with patch(
        "nv_config_manager.common.client.config_store.RetryClient", return_value=mock_session
    ):
        device_uuid = "123e4567-e89b-12d3-a456-426614174000"
        config_file = await async_config_store_client.load_file(device_uuid, "startup.yaml")

    assert config_file.commit == "5"
    assert config_file.content == "test content"
    assert config_file.sha == "abc123def456"
    assert config_file.filename == "startup.yaml"


@pytest.mark.asyncio
async def test_whoami_uses_service_root(async_config_store_client):
    """Test whoami uses root /whoami while config APIs stay under /v1/config."""
    mock_session = _mock_retry_client(
        {"user": "config-store-api", "roles": ["all", "nv-config-manager"]}
    )

    with patch(
        "nv_config_manager.common.client.config_store.RetryClient", return_value=mock_session
    ):
        result = await async_config_store_client.whoami()

    assert result == {"user": "config-store-api", "roles": ["all", "nv-config-manager"]}
    mock_session.get.assert_called_once_with("http://config-store.example.com/whoami")
    assert async_config_store_client.base_url == "http://config-store.example.com"
    assert async_config_store_client.config_url == "http://config-store.example.com/v1/config"


@pytest.mark.asyncio
async def test_load_file_not_found(async_config_store_client):
    """Test a 404 from the per-file endpoint maps to ConfigStoreFileNotFound."""
    mock_session = _mock_retry_client(None)
    mock_session.get.return_value.raise_for_status = MagicMock(
        side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=404, message="Not Found"
        )
    )

    with patch(
        "nv_config_manager.common.client.config_store.RetryClient", return_value=mock_session
    ):
        with pytest.raises(ConfigStoreFileNotFound):
            await async_config_store_client.load_file(
                "123e4567-e89b-12d3-a456-426614174000", "startup.yaml"
            )


@pytest.mark.asyncio
async def test_persist_files_new(async_config_store_client):
    """Test persisting files for a device with nothing stored yet."""
    device_uuid = "123e4567-e89b-12d3-a456-426614174000"

    list_configs = AsyncMock(return_value=[])
    async_config_store_client.list_device_configs = list_configs

    mock_session = _mock_retry_client(MOCK_BATCH_POST_RESPONSE)

    with patch(
        "nv_config_manager.common.client.config_store.RetryClient", return_value=mock_session
    ):
        config_files = await async_config_store_client.persist_files(
            device_uuid=device_uuid,
            files={"startup.yaml": "new content"},
            commit_message="test commit message",
            user="testuser",
            user_domain="config-manager.example.com",
        )

    assert config_files is not None
    assert len(config_files) == 1
    assert config_files[0].commit == "6"
    assert config_files[0].filename == "startup.yaml"
    # A device with no stored configs must be discovered without a per-file GET.
    list_configs.assert_awaited_once_with(device_uuid)


@pytest.mark.asyncio
async def test_persist_files_skips_unchanged(async_config_store_client):
    """Test files matching the stored content are not resubmitted."""
    device_uuid = "123e4567-e89b-12d3-a456-426614174000"

    async_config_store_client.list_device_configs = AsyncMock(
        return_value=[dict(MOCK_GET_RESPONSE)]
    )

    mock_session = _mock_retry_client(MOCK_BATCH_POST_RESPONSE)

    with patch(
        "nv_config_manager.common.client.config_store.RetryClient", return_value=mock_session
    ):
        config_files = await async_config_store_client.persist_files(
            device_uuid=device_uuid,
            files={"startup.yaml.j2": MOCK_GET_RESPONSE["content"]},
            commit_message="test commit message",
            user="testuser",
            user_domain="config-manager.example.com",
        )

    assert config_files is None
    mock_session.post.assert_not_called()


@pytest.mark.asyncio
async def test_file_url(async_config_store_client):
    """Test generating file URL."""
    device_uuid = "123e4567-e89b-12d3-a456-426614174000"
    url = async_config_store_client.file_url(device_uuid, "startup.yaml")
    assert (
        url
        == f"https://config-manager.example.com/device/{device_uuid}/startup.yaml?file_type=intended"
    )

    url_with_version = async_config_store_client.file_url(device_uuid, "startup.yaml", version="5")
    assert (
        url_with_version
        == f"https://config-manager.example.com/device/{device_uuid}/startup.yaml?file_type=intended&version=5"
    )


@pytest.mark.asyncio
async def test_history_url(async_config_store_client):
    """Test generating history URL."""
    device_uuid = "123e4567-e89b-12d3-a456-426614174000"
    url = async_config_store_client.history_url(device_uuid, "startup.yaml")
    assert (
        url
        == f"https://config-manager.example.com/device/{device_uuid}/startup.yaml/history?file_type=intended"
    )


@pytest.mark.asyncio
async def test_context_manager():
    """Test async context manager usage."""
    async with ConfigStoreClient(
        target="http://config-store.example.com",
        file_type="intended",
        ui_url="https://config-manager.example.com",
    ) as client:
        assert client.target == "http://config-store.example.com"


@pytest.mark.asyncio
async def test_init_with_headers():
    """Test client initialization with custom headers."""
    headers = {
        "X-Auth-Request-Email": "test-service",
        "X-Auth-Request-User": "test-service",
        "X-Auth-Request-Groups": "nv-config-manager",
    }
    client = ConfigStoreClient(
        target="http://config-store.example.com",
        file_type="intended",
        ui_url="https://config-manager.example.com",
        headers=headers,
    )
    assert client.target == "http://config-store.example.com"
    await client.close()


@pytest.mark.asyncio
async def test_from_config_internal_endpoint():
    """Test from_config with internal endpoint uses callable headers."""
    from configparser import ConfigParser
    from unittest.mock import patch

    config = ConfigParser()
    config.add_section("config_store.client")
    config.set("config_store.client", "api_service", "http://internal-service:9000")
    config.set("config_store.client", "api_url", "https://external.example.com")
    config.set("config_store.client", "ui_url", "https://config-manager.example.com")
    config.set("config_store.client", "use_internal_endpoint", "true")

    with patch.dict("os.environ", {"HOSTNAME": "nv-config-manager-render-5f8d9c7b6-abc12"}):
        client = ConfigStoreClient.from_config(config, file_type="intended")

    assert client.target == "http://internal-service:9000"
    assert callable(client._headers)
    await client.close()


@pytest.mark.asyncio
async def test_from_config_external_endpoint():
    """Test from_config with external endpoint does not include auth headers."""
    from configparser import ConfigParser

    config = ConfigParser()
    config.add_section("config_store.client")
    config.set("config_store.client", "api_service", "http://internal-service:9000")
    config.set("config_store.client", "api_url", "https://external.example.com")
    config.set("config_store.client", "ui_url", "https://config-manager.example.com")
    config.set("config_store.client", "use_internal_endpoint", "false")

    client = ConfigStoreClient.from_config(config, file_type="intended")

    assert client.target == "https://external.example.com"
    await client.close()
