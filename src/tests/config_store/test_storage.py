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
"""Tests for core storage logic."""

from uuid import uuid4

import pytest

from nv_config_manager.config_store.core import (
    calculate_content_hash,
    compress_content,
    create_or_update_config,
    decompress_content,
    delete_device_configs,
    get_latest_version,
    get_specific_version,
    get_version_history,
)
from nv_config_manager.config_store.db.models import FileType


def test_compress_decompress():
    """Test content compression and decompression."""
    # Use a larger, more repetitive config that will actually compress well
    # Small strings often don't compress due to gzip header overhead
    original = """hostname device01
interface Ethernet1
  description Uplink to spine01
  ip address 10.0.0.1/24
  no shutdown
interface Ethernet2
  description Uplink to spine02
  ip address 10.0.0.2/24
  no shutdown
interface Ethernet3
  description Server connection
  ip address 10.0.0.3/24
  no shutdown
interface Ethernet4
  description Server connection
  ip address 10.0.0.4/24
  no shutdown
router bgp 65001
  neighbor 10.0.0.10 remote-as 65002
  neighbor 10.0.0.11 remote-as 65002
  neighbor 10.0.0.12 remote-as 65002
"""

    compressed = compress_content(original)
    assert isinstance(compressed, bytes)
    # For larger, repetitive content, compression should help
    assert len(compressed) < len(original)

    decompressed = decompress_content(compressed)
    assert decompressed == original


def test_compress_decompress_small():
    """Test that compression/decompression works correctly even for small strings."""
    # Small strings may not compress well (overhead > savings)
    original = "hostname device01\n"

    compressed = compress_content(original)
    assert isinstance(compressed, bytes)
    # Don't assert size - small strings may be larger when compressed

    decompressed = decompress_content(compressed)
    assert decompressed == original


def test_content_hash():
    """Test content hash calculation."""
    content1 = "test content"
    content2 = "test content"
    content3 = "different content"

    hash1 = calculate_content_hash(content1)
    hash2 = calculate_content_hash(content2)
    hash3 = calculate_content_hash(content3)

    assert hash1 == hash2  # Same content = same hash
    assert hash1 != hash3  # Different content = different hash
    assert len(hash1) == 64  # SHA256 hex = 64 characters


@pytest.mark.asyncio
async def test_create_config(db_session):
    """Test creating a new config file."""
    device_uuid = str(uuid4())
    filename = "config.yaml"
    content = "hostname test-device"
    author = "test@example.com"
    message = "Initial config"

    async with db_session.begin():
        config = await create_or_update_config(
            db_session, device_uuid, filename, content, author, message
        )

    assert config.device_uuid == device_uuid
    assert config.filename == filename
    assert config.file_type == FileType.INTENDED
    assert config.version == 1
    assert config.author == author
    assert config.commit_message == message


@pytest.mark.asyncio
async def test_update_config(db_session):
    """Test updating an existing config file."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create version 1
    async with db_session.begin():
        config1 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "content v1",
            "test@example.com",
            "Version 1",
        )

    assert config1.version == 1

    # Create version 2
    async with db_session.begin():
        config2 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "content v2",
            "test@example.com",
            "Version 2",
        )

    assert config2.version == 2


@pytest.mark.asyncio
async def test_no_change_no_version(db_session):
    """Test that no new version is created if content hasn't changed."""
    device_uuid = str(uuid4())
    filename = "config.yaml"
    content = "same content"

    # Create version 1
    async with db_session.begin():
        config1 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            content,
            "test@example.com",
            "Version 1",
        )

    # Try to create with same content
    async with db_session.begin():
        config2 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            content,
            "test@example.com",
            "Version 2",
        )

    assert config1.id == config2.id  # Same record returned
    assert config2.version == 1  # No new version created


@pytest.mark.asyncio
async def test_get_latest_version(db_session):
    """Test getting the latest version of a config."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create multiple versions
    for i in range(3):
        async with db_session.begin():
            await create_or_update_config(
                db_session,
                device_uuid,
                filename,
                f"content v{i + 1}",
                "test@example.com",
                f"Version {i + 1}",
            )

    # Get latest
    latest = await get_latest_version(db_session, device_uuid, filename, FileType.INTENDED)

    assert latest is not None
    assert latest.version == 3
    assert decompress_content(latest.content) == "content v3"


@pytest.mark.asyncio
async def test_get_specific_version(db_session):
    """Test getting a specific version of a config."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create multiple versions
    for i in range(3):
        async with db_session.begin():
            await create_or_update_config(
                db_session,
                device_uuid,
                filename,
                f"content v{i + 1}",
                "test@example.com",
                f"Version {i + 1}",
            )

    # Get version 2
    version2 = await get_specific_version(db_session, device_uuid, filename, FileType.INTENDED, 2)

    assert version2 is not None
    assert version2.version == 2
    assert decompress_content(version2.content) == "content v2"


@pytest.mark.asyncio
async def test_version_history(db_session):
    """Test getting version history."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create multiple versions
    for i in range(5):
        async with db_session.begin():
            await create_or_update_config(
                db_session,
                device_uuid,
                filename,
                f"content v{i + 1}",
                "test@example.com",
                f"Version {i + 1}",
            )

    # Get history
    history = await get_version_history(
        db_session, device_uuid, filename, FileType.INTENDED, limit=10
    )

    assert len(history) == 5
    # Should be in descending order
    assert history[0].version == 5
    assert history[4].version == 1


@pytest.mark.asyncio
async def test_file_type_isolation(db_session):
    """Test that intended and backup configs are isolated from each other."""
    device_uuid = str(uuid4())
    filename = "config.yaml"

    # Create intended config versions
    async with db_session.begin():
        intended_v1 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "intended v1",
            "test@example.com",
            "Intended version 1",
            FileType.INTENDED,
        )

    async with db_session.begin():
        intended_v2 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "intended v2",
            "test@example.com",
            "Intended version 2",
            FileType.INTENDED,
        )

    # Create backup config versions
    async with db_session.begin():
        backup_v1 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "backup v1",
            "test@example.com",
            "Backup version 1",
            FileType.BACKUP,
        )

    async with db_session.begin():
        backup_v2 = await create_or_update_config(
            db_session,
            device_uuid,
            filename,
            "backup v2",
            "test@example.com",
            "Backup version 2",
            FileType.BACKUP,
        )

    # Verify intended configs
    intended_latest = await get_latest_version(db_session, device_uuid, filename, FileType.INTENDED)
    assert intended_latest.version == 2
    assert decompress_content(intended_latest.content) == "intended v2"

    intended_history = await get_version_history(
        db_session, device_uuid, filename, FileType.INTENDED, limit=10
    )
    assert len(intended_history) == 2

    # Verify backup configs
    backup_latest = await get_latest_version(db_session, device_uuid, filename, FileType.BACKUP)
    assert backup_latest.version == 2
    assert decompress_content(backup_latest.content) == "backup v2"

    backup_history = await get_version_history(
        db_session, device_uuid, filename, FileType.BACKUP, limit=10
    )
    assert len(backup_history) == 2

    # Verify they're different records
    assert intended_v1.id != backup_v1.id
    assert intended_v2.id != backup_v2.id


@pytest.mark.asyncio
async def test_delete_device_configs(db_session):
    """Test deleting all config versions for a device."""
    device_uuid = str(uuid4())

    # Create multiple configs with multiple versions
    for filename in ["config1.yaml", "config2.yaml"]:
        for i in range(2):
            async with db_session.begin():
                await create_or_update_config(
                    db_session,
                    device_uuid,
                    filename,
                    f"content v{i + 1} for {filename}",
                    "test@example.com",
                    f"Version {i + 1}",
                )

    # Verify configs exist
    latest = await get_latest_version(db_session, device_uuid, "config1.yaml", FileType.INTENDED)
    assert latest is not None
    assert latest.version == 2

    # Delete all configs (session auto-begins)
    count = await delete_device_configs(db_session, device_uuid)
    await db_session.commit()

    assert count == 4  # 2 files x 2 versions

    # Verify configs are gone
    latest = await get_latest_version(db_session, device_uuid, "config1.yaml", FileType.INTENDED)
    assert latest is None


@pytest.mark.asyncio
async def test_delete_device_configs_no_configs(db_session):
    """Test deleting configs for a device that has none."""
    device_uuid = str(uuid4())

    count = await delete_device_configs(db_session, device_uuid)

    assert count == 0


@pytest.mark.asyncio
async def test_delete_device_configs_does_not_affect_other_devices(db_session):
    """Test that deleting one device's configs does not affect another device."""
    device1 = str(uuid4())
    device2 = str(uuid4())

    # Create configs for both devices
    for device_uuid in [device1, device2]:
        async with db_session.begin():
            await create_or_update_config(
                db_session,
                device_uuid,
                "config.yaml",
                f"content for {device_uuid}",
                "test@example.com",
                "Test",
            )

    # Delete device1's configs
    count = await delete_device_configs(db_session, device1)
    await db_session.commit()
    assert count == 1

    # device2's configs should still exist
    latest = await get_latest_version(db_session, device2, "config.yaml", FileType.INTENDED)
    assert latest is not None
