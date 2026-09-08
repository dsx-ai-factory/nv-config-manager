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
"""Tests for the reusable asynchronous Config Store client."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio

from nv_config_manager_workflows.clients.config_store import (
    ConfigStoreClient,
    ConfigStoreClientSettings,
    ConfigStoreException,
    ConfigStoreFileNotFound,
    ConfigStoreType,
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


def test_config_store_type_values_are_stable() -> None:
    assert ConfigStoreType.BACKUP.value == "backup"
    assert ConfigStoreType.INTENDED.value == "intended"


@pytest_asyncio.fixture
async def async_config_store_client():
    """Create a ConfigStoreClient instance for testing."""
    settings: ConfigStoreClientSettings = {
        "target": "http://config-store.example.com",
        "file_type": ConfigStoreType.INTENDED,
        "ui_url": "https://config-manager.example.com",
    }
    client = ConfigStoreClient(**settings)
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_init(async_config_store_client):
    """Test client initialization."""
    assert async_config_store_client.target == "http://config-store.example.com"
    assert async_config_store_client.file_type == "intended"
    assert async_config_store_client.base_url == "http://config-store.example.com"
    assert async_config_store_client.config_url == "http://config-store.example.com/v1/config"
    assert async_config_store_client.timeout.total == 30
    assert async_config_store_client.timeout.connect == 10
    assert async_config_store_client.retry_options.attempts == 5
    assert async_config_store_client.retry_options.get_timeout(0) == 1.0
    assert async_config_store_client.retry_options.get_timeout(4) == 16.0
    assert async_config_store_client.retry_options.statuses == {429, 500, 502, 503, 504}


@pytest.mark.asyncio
async def test_init_with_ca_cert_disabled():
    """Test client initialization with CA certificate disabled."""
    client = ConfigStoreClient(
        target="http://config-store.example.com",
        file_type=ConfigStoreType.INTENDED,
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

    with patch("nv_config_manager_workflows.clients._http.RetryClient", return_value=mock_session):
        device_uuid = "123e4567-e89b-12d3-a456-426614174000"
        config_file = await async_config_store_client.load_file(device_uuid, "startup.yaml")

    assert config_file.commit == "5"
    assert config_file.content == "test content"
    assert config_file.sha == "abc123def456"
    assert config_file.filename == "startup.yaml"
    mock_session.get.assert_called_once_with(
        "http://config-store.example.com/v1/config/"
        "123e4567-e89b-12d3-a456-426614174000/startup.yaml",
        params={"file_type": "intended"},
    )


@pytest.mark.asyncio
async def test_load_file_not_found_remains_distinguishable(async_config_store_client):
    mock_session = _mock_retry_client({})
    response = mock_session.get.return_value
    response.raise_for_status.side_effect = aiohttp.ClientResponseError(
        MagicMock(),
        (),
        status=404,
        message="Not Found",
    )

    with (
        patch(
            "nv_config_manager_workflows.clients._http.RetryClient",
            return_value=mock_session,
        ),
        pytest.raises(ConfigStoreFileNotFound),
    ):
        await async_config_store_client.load_file("device-1", "startup config")

    assert issubclass(ConfigStoreFileNotFound, ConfigStoreException)


@pytest.mark.asyncio
async def test_load_file_other_http_error_is_generic(async_config_store_client):
    mock_session = _mock_retry_client({})
    response = mock_session.get.return_value
    response.raise_for_status.side_effect = aiohttp.ClientResponseError(
        MagicMock(),
        (),
        status=500,
        message="Server Error",
    )

    with (
        patch(
            "nv_config_manager_workflows.clients._http.RetryClient",
            return_value=mock_session,
        ),
        pytest.raises(ConfigStoreException) as exc_info,
    ):
        await async_config_store_client.load_file("device-1", "startup.yaml")

    assert not isinstance(exc_info.value, ConfigStoreFileNotFound)


@pytest.mark.asyncio
async def test_config_query_paths_and_parameters_are_unchanged(async_config_store_client):
    sessions = [_mock_retry_client({}) for _ in range(4)]
    sessions[0].get.return_value.json.return_value = []

    with patch(
        "nv_config_manager_workflows.clients._http.RetryClient",
        side_effect=sessions,
    ):
        assert await async_config_store_client.list_device_configs("device-1") == []
        await async_config_store_client.get_config_file(
            "device-1",
            "startup config",
            file_type=None,
            version=7,
        )
        await async_config_store_client.get_config_versions(
            "device-1",
            "startup config",
            file_type=ConfigStoreType.BACKUP,
            limit=20,
        )
        await async_config_store_client.get_config_diff(
            "device-1",
            "startup config",
            3,
            7,
        )

    sessions[0].get.assert_called_once_with(
        "http://config-store.example.com/v1/config/device/device-1",
        params={"file_type": "intended"},
    )
    sessions[1].get.assert_called_once_with(
        "http://config-store.example.com/v1/config/device-1/startup%20config",
        params={"version": 7},
    )
    sessions[2].get.assert_called_once_with(
        "http://config-store.example.com/v1/config/device-1/startup%20config/versions",
        params={"file_type": "backup", "limit": 20},
    )
    sessions[3].get.assert_called_once_with(
        "http://config-store.example.com/v1/config/device-1/startup%20config/diff",
        params={"file_type": "intended", "from_version": 3, "to_version": 7},
    )


@pytest.mark.asyncio
async def test_whoami_uses_service_root(async_config_store_client):
    """Test whoami uses root /whoami while config APIs stay under /v1/config."""
    mock_session = _mock_retry_client(
        {"user": "config-store-api", "roles": ["all", "nv-config-manager"]}
    )

    with patch("nv_config_manager_workflows.clients._http.RetryClient", return_value=mock_session):
        result = await async_config_store_client.whoami()

    assert result == {"user": "config-store-api", "roles": ["all", "nv-config-manager"]}
    mock_session.get.assert_called_once_with("http://config-store.example.com/whoami")
    assert async_config_store_client.base_url == "http://config-store.example.com"
    assert async_config_store_client.config_url == "http://config-store.example.com/v1/config"


@pytest.mark.asyncio
async def test_persist_files_new(async_config_store_client):
    """Test persisting new files."""
    device_uuid = "123e4567-e89b-12d3-a456-426614174000"

    async def mock_load_file(dev_uuid, filename):
        raise ConfigStoreFileNotFound(f"File {filename} not found")

    async_config_store_client.load_file = mock_load_file

    mock_session = _mock_retry_client(MOCK_BATCH_POST_RESPONSE)

    with patch("nv_config_manager_workflows.clients._http.RetryClient", return_value=mock_session):
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
    mock_session.post.assert_called_once_with(
        "http://config-store.example.com/v1/config/123e4567-e89b-12d3-a456-426614174000/batch",
        json={
            "files": [
                {
                    "filename": "startup.yaml",
                    "content": "new content",
                    "author": "testuser@config-manager.example.com",
                    "commit_message": "test commit message",
                    "file_type": "intended",
                }
            ]
        },
    )


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
        file_type=ConfigStoreType.INTENDED,
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
        file_type=ConfigStoreType.INTENDED,
        ui_url="https://config-manager.example.com",
        headers=headers,
    )
    assert client.target == "http://config-store.example.com"
    await client.close()
