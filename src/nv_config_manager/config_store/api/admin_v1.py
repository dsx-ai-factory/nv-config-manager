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
"""V1 Admin API endpoints."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.config_store.api.device_ids import validate_device_id
from nv_config_manager.config_store.api.schemas import FileType
from nv_config_manager.config_store.api.schemas_admin import (
    CacheStatusResponse,
    CacheTestErrorResponse,
    CacheTestFoundResponse,
    CacheTestNotFoundResponse,
    DeleteDeviceResponse,
    DeviceLatestConfig,
    DeviceUUID,
    StatsResponse,
)
from nv_config_manager.config_store.core.device_cache_redis import DeviceCacheService
from nv_config_manager.config_store.core.enrichment import enrich_with_device_metadata
from nv_config_manager.config_store.core.storage import delete_device_configs
from nv_config_manager.config_store.db import ConfigFile, get_db

logger = get_logger(__name__, category=LogCategory.CONFIG_STORE_API)
router = APIRouter()


@router.get("/stats", response_model=StatsResponse, summary="Get database statistics")
async def get_stats(db: AsyncSession = Depends(get_db)) -> StatsResponse:
    """Get statistics about the config store."""
    # Total configs
    total_result = await db.execute(select(func.count(ConfigFile.id)))
    total_configs = total_result.scalar() or 0

    # Unique devices
    devices_result = await db.execute(select(func.count(func.distinct(ConfigFile.device_uuid))))
    unique_devices = devices_result.scalar() or 0

    # Unique files (device_uuid + filename combinations)
    unique_files_query = select(ConfigFile.device_uuid, ConfigFile.filename).distinct()
    files_result = await db.execute(select(func.count()).select_from(unique_files_query.subquery()))
    unique_files = files_result.scalar() or 0

    # Storage used (approximate)
    storage_result = await db.execute(select(func.sum(func.length(ConfigFile.content))))
    storage_bytes = storage_result.scalar() or 0

    return StatsResponse(
        total_config_versions=total_configs,
        unique_devices=unique_devices,
        unique_files=unique_files,
        storage_bytes=storage_bytes,
        storage_mb=round(storage_bytes / (1024 * 1024), 2),
    )


@router.get("/devices", summary="List all devices")
async def list_devices(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[DeviceUUID]:
    """List all devices with configs."""
    # Get unique provider-owned device identifiers from ConfigFile.
    query = (
        select(ConfigFile.device_uuid)
        .distinct()
        .order_by(ConfigFile.device_uuid)
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    device_uuids = result.scalars().all()

    return [DeviceUUID(uuid=str(uuid)) for uuid in device_uuids]


@router.get(
    "/cache/status",
    response_model=CacheStatusResponse,
    summary="Check cache service status",
)
async def cache_status(request: Request) -> CacheStatusResponse:
    """Check if cache service is initialized and working."""
    cache_service = getattr(request.app.state, "cache_service", None)

    if not cache_service:
        return CacheStatusResponse(
            enabled=False,
            message="Cache service is not initialized",
            cache_ttl=None,
            redis_connected=None,
            dcim_connected=None,
            nautobot_connected=None,
        )

    return CacheStatusResponse(
        enabled=True,
        message=None,
        cache_ttl=cache_service.cache_ttl,
        redis_connected=cache_service.redis_client is not None,
        dcim_connected=cache_service.dcim_client is not None,
        nautobot_connected=cache_service.dcim_client is not None,
    )


@router.get(
    "/cache/test/{device_uuid}",
    response_model=CacheTestFoundResponse | CacheTestNotFoundResponse | CacheTestErrorResponse,
    summary="Test cache lookup for a device",
)
async def test_cache_lookup(
    device_uuid: str, request: Request
) -> CacheTestFoundResponse | CacheTestNotFoundResponse | CacheTestErrorResponse:
    """Test if a specific device can be found in cache."""
    validate_device_id(device_uuid, request)
    cache_service = getattr(request.app.state, "cache_service", None)

    if not cache_service:
        return CacheTestErrorResponse(
            error="Cache service is not initialized",
            device_uuid=str(device_uuid),
        )

    try:
        metadata = await cache_service.get_device_metadata(device_uuid, refresh_on_miss=False)

        if metadata:
            return CacheTestFoundResponse(
                found=True,
                device_uuid=str(device_uuid),
                device_name=metadata.name,
                site=metadata.site,
                platform=metadata.platform,
            )
        else:
            return CacheTestNotFoundResponse(
                found=False,
                device_uuid=str(device_uuid),
                message="Device not found in cache (not querying the DCIM provider)",
            )
    except Exception as e:
        return CacheTestErrorResponse(
            error=str(e),
            device_uuid=str(device_uuid),
        )


def _latest_config_query(
    file_type: FileType, device_uuids: list[str] | None = None, limit: int = 100
) -> Select:
    """Build a query for the latest config version per device."""
    where_clauses = [ConfigFile.file_type == file_type]
    if device_uuids is not None:
        where_clauses.append(ConfigFile.device_uuid.in_(device_uuids))

    subquery = (
        select(
            ConfigFile.device_uuid,
            func.max(ConfigFile.created_at).label("max_created_at"),
        )
        .where(*where_clauses)
        .group_by(ConfigFile.device_uuid)
        .subquery()
    )

    query = (
        select(ConfigFile)
        .join(
            subquery,
            (ConfigFile.device_uuid == subquery.c.device_uuid)
            & (ConfigFile.created_at == subquery.c.max_created_at),
        )
        .where(ConfigFile.file_type == file_type)
        .order_by(ConfigFile.created_at.desc())
    )

    if device_uuids is None:
        query = query.limit(limit)

    return query


async def _build_device_list(
    configs: Sequence[ConfigFile],
    cache_service: DeviceCacheService | None,
    active_uuids: set[str] | None,
    include_inactive: bool,
) -> list[DeviceLatestConfig]:
    """Map DB config rows to API response models, filtering by active status."""
    devices_list: list[DeviceLatestConfig] = []
    for config in configs:
        is_active = config.device_uuid in active_uuids if active_uuids is not None else True
        if not include_inactive and not is_active:
            continue

        device_metadata = await enrich_with_device_metadata(config.device_uuid, cache_service)
        devices_list.append(
            DeviceLatestConfig(
                uuid=str(config.device_uuid),
                name=device_metadata.name if device_metadata else str(config.device_uuid),
                site=device_metadata.site if device_metadata else "Unknown",
                latest_update=config.created_at.isoformat(),
                latest_author=config.author,
                latest_message=config.commit_message,
                active=is_active,
            )
        )
    return devices_list


@router.get(
    "/devices/search",
    response_model=list[DeviceLatestConfig],
    summary="Search devices by name with latest config info",
)
async def search_devices(
    request: Request,
    q: str | None = None,
    limit: int = 100,
    file_type: FileType = FileType.INTENDED,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[DeviceLatestConfig]:
    """
    Search devices by name with their latest configuration metadata.

    If q is not provided, returns the 100 most recently updated devices from DB.
    If q is provided, uses Redis cache to find matching device names and identifiers,
    then fetches their latest config info from DB.
    Results are sorted by most recent update first.
    Results are filtered by file_type (intended or backup).
    Devices no longer returned by the selected DCIM provider are hidden unless
    include_inactive is True.
    """
    cache_service = getattr(request.app.state, "cache_service", None) if request else None

    active_uuids: set[str] | None = None
    if cache_service:
        active_uuids = await cache_service.get_active_device_uuids()

    matching_uuids: list[str] | None = None
    if q and cache_service:
        try:
            matches = await cache_service.search_devices_by_name(q, limit=limit)
            matching_uuids = [device_uuid for device_uuid, _ in matches]
            if not matching_uuids:
                return []
        except Exception as e:
            logger.error("Redis search failed, falling back to DB query: %s", e)

    query = _latest_config_query(file_type, matching_uuids, limit)
    result = await db.execute(query)
    configs = result.scalars().all()

    return await _build_device_list(configs, cache_service, active_uuids, include_inactive)


@router.delete(
    "/devices/{device_uuid}",
    response_model=DeleteDeviceResponse,
    summary="Permanently delete all configs for a device",
)
async def delete_device(
    device_uuid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DeleteDeviceResponse:
    """Permanently delete all configuration files and version history for a device.

    This action cannot be undone. All config versions (intended and backup)
    for the specified device will be removed from the database.
    """
    validate_device_id(device_uuid, request)
    deleted_count = await delete_device_configs(db, device_uuid)
    await db.commit()

    cache_service = getattr(request.app.state, "cache_service", None)
    if cache_service:
        await cache_service.delete_device(device_uuid)

    if deleted_count == 0:
        return DeleteDeviceResponse(
            device_uuid=str(device_uuid),
            deleted_versions=0,
            message="No configs found for this device",
        )

    logger.info("Deleted %d config versions for device %s", deleted_count, device_uuid)

    return DeleteDeviceResponse(
        device_uuid=str(device_uuid),
        deleted_versions=deleted_count,
        message=f"Permanently deleted {deleted_count} config version(s)",
    )
