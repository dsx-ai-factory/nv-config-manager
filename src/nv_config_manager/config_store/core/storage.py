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
"""Core storage logic for config files."""

import gzip
from datetime import datetime
from hashlib import sha256

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nv_config_manager.config_store.config import settings
from nv_config_manager.config_store.db.models import ConfigFile as ConfigFileModel
from nv_config_manager.config_store.db.models import FileType


def compress_content(content: str) -> bytes:
    """
    Compress content using gzip.

    Args:
        content: Uncompressed content string

    Returns:
        Compressed bytes
    """
    return gzip.compress(content.encode("utf-8"), compresslevel=settings.compression_level)


def decompress_content(compressed: bytes) -> str:
    """
    Decompress gzip content.

    Args:
        compressed: Compressed bytes

    Returns:
        Decompressed string
    """
    return gzip.decompress(compressed).decode("utf-8")


def calculate_content_hash(content: str) -> str:
    """
    Calculate SHA256 hash of content.

    Args:
        content: Content to hash

    Returns:
        Hex-encoded SHA256 hash
    """
    return sha256(content.encode("utf-8")).hexdigest()


def get_lock_id(device_uuid: str, filename: str, file_type: FileType) -> int:
    """
    Generate consistent lock ID for device_uuid + filename + file_type combination.

    Args:
        device_uuid: DCIM provider device identifier
        filename: File name
        file_type: File type (intended or backup)

    Returns:
        Lock ID for PostgreSQL advisory lock
    """
    lock_string = f"{device_uuid}:{filename}:{file_type.value}"
    lock_bytes = sha256(lock_string.encode()).digest()[:8]
    lock_id = int.from_bytes(lock_bytes, byteorder="big", signed=True)
    # Ensure it fits in PostgreSQL BIGINT range
    return lock_id % (2**63)


async def acquire_file_lock(
    session: AsyncSession, device_uuid: str, filename: str, file_type: FileType
) -> None:
    """
    Acquire advisory lock for device_uuid + filename + file_type combination.

    Uses PostgreSQL advisory locks which are automatically released on
    transaction commit/rollback. For SQLite (used in tests), advisory locks
    are skipped as SQLite provides sufficient serialization guarantees.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier
        filename: File name
        file_type: File type (intended or backup)
    """
    # Skip advisory locks for SQLite (used in testing)
    dialect_name = session.bind.dialect.name if session.bind else "unknown"
    if dialect_name == "sqlite":
        return

    lock_id = get_lock_id(device_uuid, filename, file_type)
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


async def get_latest_version(
    session: AsyncSession, device_uuid: str, filename: str, file_type: FileType
) -> ConfigFileModel | None:
    """
    Get the latest version of a config file.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier
        filename: File name
        file_type: File type (intended or backup)

    Returns:
        Latest ConfigFile or None if not found
    """
    result = await session.execute(
        select(ConfigFileModel)
        .where(
            ConfigFileModel.device_uuid == device_uuid,
            ConfigFileModel.filename == filename,
            ConfigFileModel.file_type == file_type,
        )
        .order_by(ConfigFileModel.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_specific_version(
    session: AsyncSession, device_uuid: str, filename: str, file_type: FileType, version: int
) -> ConfigFileModel | None:
    """
    Get a specific version of a config file.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier
        filename: File name
        file_type: File type (intended or backup)
        version: Version number

    Returns:
        ConfigFile at specified version or None if not found
    """
    result = await session.execute(
        select(ConfigFileModel).where(
            ConfigFileModel.device_uuid == device_uuid,
            ConfigFileModel.filename == filename,
            ConfigFileModel.file_type == file_type,
            ConfigFileModel.version == version,
        )
    )
    return result.scalar_one_or_none()


async def create_or_update_config(
    session: AsyncSession,
    device_uuid: str,
    filename: str,
    content: str,
    author: str,
    commit_message: str,
    file_type: FileType = FileType.INTENDED,
    created_at: "datetime | None" = None,
) -> ConfigFileModel:
    """
    Create or update a config file with automatic versioning and locking.

    This function:
    1. Acquires an advisory lock for the device/filename/file_type combination
    2. Checks if content has changed
    3. Creates a new version if changed
    4. Returns the created/existing config file

    The lock is automatically released when the transaction ends.

    Args:
        session: Database session (must be in a transaction)
        device_uuid: DCIM provider device identifier
        filename: File name
        content: Uncompressed content
        author: Author email
        commit_message: Commit message
        file_type: File type (intended or backup)
        created_at: Optional timestamp for the version (defaults to current time)

    Returns:
        Created or existing ConfigFile
    """
    # Acquire lock (blocks until available)
    await acquire_file_lock(session, device_uuid, filename, file_type)

    # Get current max version
    result = await session.execute(
        select(func.max(ConfigFileModel.version)).where(
            ConfigFileModel.device_uuid == device_uuid,
            ConfigFileModel.filename == filename,
            ConfigFileModel.file_type == file_type,
        )
    )
    current_version = result.scalar() or 0

    # Check if content actually changed
    if current_version > 0:
        latest = await get_latest_version(session, device_uuid, filename, file_type)
        if latest:
            latest_content = decompress_content(latest.content)
            if latest_content == content:
                # No changes, return existing version
                return latest

    # Create new version
    new_version = current_version + 1
    compressed_content = compress_content(content)
    content_hash = calculate_content_hash(content)

    new_file = ConfigFileModel(
        device_uuid=device_uuid,
        filename=filename,
        file_type=file_type,
        version=new_version,
        content=compressed_content,
        content_hash=content_hash,
        author=author,
        commit_message=commit_message,
    )

    # Set custom timestamp if provided
    if created_at is not None:
        new_file.created_at = created_at

    session.add(new_file)
    await session.flush()

    return new_file


async def get_version_history(
    session: AsyncSession, device_uuid: str, filename: str, file_type: FileType, limit: int = 100
) -> list[ConfigFileModel]:
    """
    Get version history for a config file.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier
        filename: File name
        file_type: File type (intended or backup)
        limit: Maximum number of versions to return

    Returns:
        List of ConfigFiles ordered by version descending
    """
    result = await session.execute(
        select(ConfigFileModel)
        .where(
            ConfigFileModel.device_uuid == device_uuid,
            ConfigFileModel.filename == filename,
            ConfigFileModel.file_type == file_type,
        )
        .order_by(ConfigFileModel.version.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def delete_device_configs(session: AsyncSession, device_uuid: str) -> int:
    """Delete all config file versions for a device.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier

    Returns:
        Number of rows deleted
    """
    result = await session.execute(
        select(func.count(ConfigFileModel.id)).where(
            ConfigFileModel.device_uuid == device_uuid,
        )
    )
    count = result.scalar() or 0

    if count > 0:
        await session.execute(
            sa_delete(ConfigFileModel).where(
                ConfigFileModel.device_uuid == device_uuid,
            )
        )

    return count


async def get_all_device_configs(
    session: AsyncSession, device_uuid: str, file_type: FileType | None = None
) -> list[ConfigFileModel]:
    """
    Get latest version of all configs for a device.

    Args:
        session: Database session
        device_uuid: DCIM provider device identifier
        file_type: Optional file type filter (intended or backup)

    Returns:
        List of latest ConfigFiles for the device
    """
    # Using DISTINCT ON to get latest version of each filename + file_type
    subquery_filters = [ConfigFileModel.device_uuid == device_uuid]
    if file_type is not None:
        subquery_filters.append(ConfigFileModel.file_type == file_type)

    subquery = (
        select(
            ConfigFileModel.filename,
            ConfigFileModel.file_type,
            func.max(ConfigFileModel.version).label("max_version"),
        )
        .where(*subquery_filters)
        .group_by(ConfigFileModel.filename, ConfigFileModel.file_type)
        .subquery()
    )

    result = await session.execute(
        select(ConfigFileModel)
        .join(
            subquery,
            (ConfigFileModel.filename == subquery.c.filename)
            & (ConfigFileModel.file_type == subquery.c.file_type)
            & (ConfigFileModel.version == subquery.c.max_version),
        )
        .where(ConfigFileModel.device_uuid == device_uuid)
    )
    return list(result.scalars().all())
