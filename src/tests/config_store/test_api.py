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
"""Tests for API endpoints."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from nv_config_manager.config_store.api.main import app


@pytest.mark.asyncio
async def test_healthcheck(client):
    """Test healthcheck endpoint."""
    response = await client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_whoami_requires_auth(client):
    """Test authenticated identity probe endpoint."""
    response = await client.get("/whoami", headers={"X-Auth-Request-Email": ""})
    assert response.status_code == 403

    response = await client.get("/whoami", headers={"X-Auth-Request-Email": "admin@example.com"})
    assert response.status_code == 200
    assert response.json() == {"user": "admin", "roles": ["all"]}


@pytest.mark.asyncio
async def test_create_config(client):
    """Test creating a config via API."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    response = await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "hostname test-device",
            "author": "test@example.com",
            "commit_message": "Initial config",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["device_uuid"] == device_uuid
    assert data["filename"] == filename
    assert data["file_type"] == "intended"
    assert data["version"] == 1
    assert data["content"] == "hostname test-device"


@pytest.mark.asyncio
async def test_device_identifier_uses_configured_provider_validation(client):
    """Config Store accepts NetBox IDs and rejects IDs invalid for that provider."""
    cache_service = MagicMock()
    cache_service.dcim_client.is_valid_device_id.side_effect = str.isdigit
    cache_service.get_device_metadata = AsyncMock(return_value=None)
    app.state.cache_service = cache_service
    try:
        response = await client.post(
            "/v1/config/42/config.yaml",
            json={
                "content": "hostname netbox-device",
                "author": "test@example.com",
                "commit_message": "Store config for NetBox device",
            },
        )
        assert response.status_code == 201
        assert response.json()["device_uuid"] == "42"

        response = await client.post(
            "/v1/config/not-an-integer/config.yaml",
            json={
                "content": "hostname invalid-device",
                "author": "test@example.com",
                "commit_message": "Invalid provider identifier",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"].endswith("not-an-integer")
    finally:
        app.state.cache_service = None


@pytest.mark.asyncio
async def test_device_identifier_validation_fails_closed(client):
    """Unchecked identifiers are not accepted when the DCIM client is unavailable."""
    app.state.dcim_client = None
    response = await client.get(f"/v1/config/device/{uuid4()}")
    assert response.status_code == 503
    assert response.json()["detail"] == "DCIM provider validation is unavailable"


@pytest.mark.asyncio
async def test_get_config(client):
    """Test getting a config via API."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create config
    await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "hostname test-device",
            "author": "test@example.com",
            "commit_message": "Initial config",
        },
    )

    # Get config
    response = await client.get(f"/v1/config/{device_uuid}/{filename}")

    assert response.status_code == 200
    data = response.json()
    assert data["device_uuid"] == device_uuid
    assert data["filename"] == filename
    assert data["file_type"] == "intended"
    assert data["content"] == "hostname test-device"


@pytest.mark.asyncio
async def test_get_config_not_found(client):
    """Test getting a non-existent config."""
    device_uuid = str(uuid4())
    filename = "nonexistent.yaml"

    response = await client.get(f"/v1/config/{device_uuid}/{filename}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_device_configs(client):
    """Test getting all configs for a device (tests route ordering)."""
    device_uuid = str(uuid4())

    # Create multiple config files for the same device
    filenames = ["config1.yaml", "config2.yaml", "startup-config.txt"]
    for filename in filenames:
        await client.post(
            f"/v1/config/{device_uuid}/{filename}",
            json={
                "content": f"content for {filename}",
                "author": "test@example.com",
                "commit_message": f"Create {filename}",
            },
        )

    # Get all configs for the device
    response = await client.get(f"/v1/config/device/{device_uuid}")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3

    # Verify all configs are returned
    returned_filenames = {config["filename"] for config in data}
    assert returned_filenames == set(filenames)

    # Verify each config has the expected structure
    for config in data:
        assert config["device_uuid"] == str(device_uuid)
        assert config["filename"] in filenames
        assert config["file_type"] == "intended"
        assert config["version"] == 1
        assert "content" in config
        assert "author" in config


@pytest.mark.asyncio
async def test_list_versions(client):
    """Test listing config versions."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create multiple versions
    for i in range(3):
        await client.post(
            f"/v1/config/{device_uuid}/{filename}",
            json={
                "content": f"content v{i + 1}",
                "author": "test@example.com",
                "commit_message": f"Version {i + 1}",
            },
        )

    # List versions
    response = await client.get(f"/v1/config/{device_uuid}/{filename}/versions")

    assert response.status_code == 200
    data = response.json()
    assert data["device_uuid"] == device_uuid
    assert data["filename"] == filename
    assert len(data["versions"]) == 3
    # Should be in descending order
    assert data["versions"][0]["version"] == 3
    assert data["versions"][0]["file_type"] == "intended"
    assert data["versions"][2]["version"] == 1
    assert data["versions"][2]["file_type"] == "intended"


@pytest.mark.asyncio
async def test_batch_create(client):
    """Test batch config creation."""
    device_uuid = str(uuid4())

    response = await client.post(
        f"/v1/config/{device_uuid}/batch",
        json={
            "files": [
                {
                    "filename": "config1.yaml",
                    "content": "content 1",
                    "author": "test@example.com",
                    "commit_message": "Batch config 1",
                },
                {
                    "filename": "config2.yaml",
                    "content": "content 2",
                    "author": "test@example.com",
                    "commit_message": "Batch config 2",
                },
            ]
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["created"]) == 2
    assert len(data["skipped"]) == 0


@pytest.mark.asyncio
async def test_get_diff(client):
    """Test getting diff between versions."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create two versions
    await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "line 1\nline 2\nline 3",
            "author": "test@example.com",
            "commit_message": "Version 1",
        },
    )

    await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "line 1\nline 2 modified\nline 3\nline 4",
            "author": "test@example.com",
            "commit_message": "Version 2",
        },
    )

    # Get diff
    response = await client.get(
        f"/v1/config/{device_uuid}/{filename}/diff?from_version=1&to_version=2"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["from_version"] == 1
    assert data["to_version"] == 2
    assert "line 2 modified" in data["diff"]


@pytest.mark.asyncio
async def test_admin_stats(client):
    """Test admin stats endpoint."""
    response = await client.get("/v1/admin/stats")

    assert response.status_code == 200
    data = response.json()
    assert "total_config_versions" in data
    assert "unique_devices" in data
    assert "storage_bytes" in data


@pytest.mark.asyncio
async def test_backup_config_isolation(client):
    """Test that backup configs are isolated from intended configs."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create intended config
    await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "intended config",
            "author": "test@example.com",
            "commit_message": "Intended config",
            "file_type": "intended",
        },
    )

    # Create backup config
    await client.post(
        f"/v1/config/{device_uuid}/{filename}",
        json={
            "content": "backup config",
            "author": "test@example.com",
            "commit_message": "Backup config",
            "file_type": "backup",
        },
    )

    # Get intended config
    response = await client.get(f"/v1/config/{device_uuid}/{filename}?file_type=intended")
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "intended config"
    assert data["file_type"] == "intended"

    # Get backup config
    response = await client.get(f"/v1/config/{device_uuid}/{filename}?file_type=backup")
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "backup config"
    assert data["file_type"] == "backup"


@pytest.mark.asyncio
async def test_file_type_filter_device_configs(client):
    """Test filtering device configs by file type."""
    device_uuid = str(uuid4())

    # Create intended configs
    await client.post(
        f"/v1/config/{device_uuid}/intended1.yaml",
        json={
            "content": "intended 1",
            "author": "test@example.com",
            "commit_message": "Intended 1",
            "file_type": "intended",
        },
    )

    await client.post(
        f"/v1/config/{device_uuid}/intended2.yaml",
        json={
            "content": "intended 2",
            "author": "test@example.com",
            "commit_message": "Intended 2",
            "file_type": "intended",
        },
    )

    # Create backup configs
    await client.post(
        f"/v1/config/{device_uuid}/backup1.yaml",
        json={
            "content": "backup 1",
            "author": "test@example.com",
            "commit_message": "Backup 1",
            "file_type": "backup",
        },
    )

    # Get only intended configs
    response = await client.get(f"/v1/config/device/{device_uuid}?file_type=intended")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(config["file_type"] == "intended" for config in data)

    # Get only backup configs
    response = await client.get(f"/v1/config/device/{device_uuid}?file_type=backup")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert all(config["file_type"] == "backup" for config in data)

    # Get all configs (no filter)
    response = await client.get(f"/v1/config/device/{device_uuid}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_delete_device_configs(client):
    """Test deleting all configs for a device."""
    device_uuid = str(uuid4())

    # Create some configs
    for filename in ["config1.yaml", "config2.yaml"]:
        await client.post(
            f"/v1/config/{device_uuid}/{filename}",
            json={
                "content": f"content for {filename}",
                "author": "test@example.com",
                "commit_message": f"Create {filename}",
            },
        )

    # Verify configs exist
    response = await client.get(f"/v1/config/device/{device_uuid}")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Delete all configs
    response = await client.delete(f"/v1/admin/devices/{device_uuid}")
    assert response.status_code == 200
    data = response.json()
    assert data["device_uuid"] == device_uuid
    assert data["deleted_versions"] == 2
    assert "deleted" in data["message"].lower()

    # Verify configs are gone
    response = await client.get(f"/v1/config/device/{device_uuid}")
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_delete_device_configs_not_found(client):
    """Test deleting configs for a device that has none."""
    device_uuid = str(uuid4())

    response = await client.delete(f"/v1/admin/devices/{device_uuid}")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted_versions"] == 0


@pytest.mark.asyncio
async def test_delete_device_configs_removes_all_types(client):
    """Test that delete removes both intended and backup configs."""
    device_uuid = str(uuid4())

    # Create intended config
    await client.post(
        f"/v1/config/{device_uuid}/intended.yaml",
        json={
            "content": "intended content",
            "author": "test@example.com",
            "commit_message": "Intended",
            "file_type": "intended",
        },
    )

    # Create backup config
    await client.post(
        f"/v1/config/{device_uuid}/backup.yaml",
        json={
            "content": "backup content",
            "author": "test@example.com",
            "commit_message": "Backup",
            "file_type": "backup",
        },
    )

    # Delete all
    response = await client.delete(f"/v1/admin/devices/{device_uuid}")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted_versions"] == 2

    # Verify both types are gone
    response = await client.get(f"/v1/config/device/{device_uuid}?file_type=intended")
    assert len(response.json()) == 0
    response = await client.get(f"/v1/config/device/{device_uuid}?file_type=backup")
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_search_devices_returns_active_field(client):
    """Test that search results include the active field."""
    device_uuid = str(uuid4())

    await client.post(
        f"/v1/config/{device_uuid}/config.yaml",
        json={
            "content": "test",
            "author": "test@example.com",
            "commit_message": "Test",
        },
    )

    response = await client.get("/v1/admin/devices/search?include_inactive=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "active" in data[0]


@pytest.mark.asyncio
async def test_search_devices_include_inactive_param(client):
    """Test that include_inactive parameter is accepted."""
    response = await client.get("/v1/admin/devices/search?include_inactive=true")
    assert response.status_code == 200

    response = await client.get("/v1/admin/devices/search?include_inactive=false")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_batch_create_with_file_types(client):
    """Test batch config creation with different file types."""
    device_uuid = str(uuid4())

    response = await client.post(
        f"/v1/config/{device_uuid}/batch",
        json={
            "files": [
                {
                    "filename": "intended.yaml",
                    "content": "intended content",
                    "author": "test@example.com",
                    "commit_message": "Intended config",
                    "file_type": "intended",
                },
                {
                    "filename": "backup.yaml",
                    "content": "backup content",
                    "author": "test@example.com",
                    "commit_message": "Backup config",
                    "file_type": "backup",
                },
            ]
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["created"]) == 2
    assert data["created"][0]["file_type"] in ["intended", "backup"]
    assert data["created"][1]["file_type"] in ["intended", "backup"]
