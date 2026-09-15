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
"""V1 Config API endpoints."""

import difflib

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.config_store.api.device_ids import (
    get_cache_service,
    validate_device_id,
)
from nv_config_manager.config_store.api.schemas import (
    BatchConfigRequest,
    BatchConfigResponse,
    ConfigCreateRequest,
    ConfigResponse,
    ConfigVersionResponse,
    ConfigVersionsResponse,
    DiffResponse,
    FileType,
)
from nv_config_manager.config_store.core import (
    create_or_update_config,
    decompress_content,
    get_all_device_configs,
    get_latest_version,
    get_specific_version,
    get_version_history,
)
from nv_config_manager.config_store.core.enrichment import enrich_with_device_metadata
from nv_config_manager.config_store.db import get_db

logger = get_logger(__name__, category=LogCategory.CONFIG_STORE)

router = APIRouter()


@router.get(
    "/device/{device_uuid}",
    response_model=list[ConfigResponse],
    summary="Get all configs for a device",
)
async def get_device_configs(
    device_uuid: str,
    request: Request,
    file_type: FileType | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ConfigResponse]:
    """Get all latest configuration files for a device."""
    validate_device_id(device_uuid, request)
    try:
        configs = await get_all_device_configs(db, device_uuid, file_type)

        # Enrich with device metadata (shared across all configs)
        cache_service = get_cache_service(request)
        device_metadata = await enrich_with_device_metadata(device_uuid, cache_service)

        return [
            ConfigResponse(
                id=c.id,
                device_uuid=c.device_uuid,
                filename=c.filename,
                file_type=c.file_type,
                version=c.version,
                content=decompress_content(c.content),
                content_hash=c.content_hash,
                author=c.author,
                commit_message=c.commit_message,
                created_at=c.created_at,
                device=device_metadata,
            )
            for c in configs
        ]
    except Exception as exc:
        logger.exception("Failed to get device configs for %s", device_uuid)
        raise HTTPException(status_code=500, detail="Failed to get device configs") from exc


@router.post(
    "/{device_uuid}/batch",
    response_model=BatchConfigResponse,
    summary="Batch create/update config files",
)
async def batch_create_configs(
    device_uuid: str,
    request_obj: Request,
    request: BatchConfigRequest,
    db: AsyncSession = Depends(get_db),
) -> BatchConfigResponse:
    """Batch create or update configuration files for a device."""
    validate_device_id(device_uuid, request_obj)
    created = []
    skipped = []

    try:
        async with db.begin():
            for item in request.files:
                # Check if content changed by attempting to get latest
                latest = await get_latest_version(db, device_uuid, item.filename, item.file_type)

                if latest:
                    latest_content = decompress_content(latest.content)
                    if latest_content == item.content:
                        # Skip if no changes
                        skipped.append(item.filename)
                        continue

                config = await create_or_update_config(
                    db,
                    device_uuid,
                    item.filename,
                    item.content,
                    item.author,
                    item.commit_message,
                    item.file_type,
                    item.created_at,
                )

                created.append(
                    ConfigVersionResponse(
                        version=config.version,
                        file_type=config.file_type,
                        author=config.author,
                        commit_message=config.commit_message,
                        created_at=config.created_at,
                        content_hash=config.content_hash,
                    )
                )

        return BatchConfigResponse(created=created, skipped=skipped)
    except Exception as exc:
        logger.exception("Failed to batch create configs for %s", device_uuid)
        raise HTTPException(status_code=500, detail="Failed to batch create configs") from exc


@router.post(
    "/{device_uuid}/{filename}",
    response_model=ConfigResponse,
    status_code=201,
    summary="Create or update a config file",
)
async def create_config(
    device_uuid: str,
    filename: str,
    request: ConfigCreateRequest,
    request_obj: Request,
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    """
    Create or update a configuration file.

    If the content has not changed, the existing version is returned without creating a new one.
    """
    validate_device_id(device_uuid, request_obj)
    try:
        async with db.begin():
            config = await create_or_update_config(
                db,
                device_uuid,
                filename,
                request.content,
                request.author,
                request.commit_message,
                request.file_type,
                request.created_at,
            )

        # Enrich with device metadata
        cache_service = get_cache_service(request_obj)
        device_metadata = await enrich_with_device_metadata(device_uuid, cache_service)

        return ConfigResponse(
            id=config.id,
            device_uuid=config.device_uuid,
            filename=config.filename,
            file_type=config.file_type,
            version=config.version,
            content=request.content,  # Return original content
            content_hash=config.content_hash,
            author=config.author,
            commit_message=config.commit_message,
            created_at=config.created_at,
            device=device_metadata,
        )
    except Exception as exc:
        logger.exception("Failed to create config %s for %s", filename, device_uuid)
        raise HTTPException(status_code=500, detail="Failed to create config") from exc


@router.get(
    "/{device_uuid}/{filename}",
    response_model=ConfigResponse,
    summary="Get a config file",
)
async def get_config(
    device_uuid: str,
    filename: str,
    request: Request,
    file_type: FileType = FileType.INTENDED,
    version: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    """
    Get a configuration file.

    If version is not specified, returns the latest version.
    """
    validate_device_id(device_uuid, request)
    try:
        if version is not None:
            config = await get_specific_version(db, device_uuid, filename, file_type, version)
        else:
            config = await get_latest_version(db, device_uuid, filename, file_type)

        if not config:
            raise HTTPException(status_code=404, detail="Config file not found")

        content = decompress_content(config.content)

        # Enrich with device metadata
        cache_service = get_cache_service(request)
        device_metadata = await enrich_with_device_metadata(device_uuid, cache_service)

        return ConfigResponse(
            id=config.id,
            device_uuid=config.device_uuid,
            filename=config.filename,
            file_type=config.file_type,
            version=config.version,
            content=content,
            content_hash=config.content_hash,
            author=config.author,
            commit_message=config.commit_message,
            created_at=config.created_at,
            device=device_metadata,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get config %s for %s", filename, device_uuid)
        raise HTTPException(status_code=500, detail="Failed to get config") from exc


@router.get(
    "/{device_uuid}/{filename}/versions",
    response_model=ConfigVersionsResponse,
    summary="List all versions of a config file",
)
async def list_versions(
    device_uuid: str,
    filename: str,
    request: Request,
    file_type: FileType = FileType.INTENDED,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> ConfigVersionsResponse:
    """List all versions of a configuration file."""
    validate_device_id(device_uuid, request)
    try:
        versions = await get_version_history(db, device_uuid, filename, file_type, limit)

        # Enrich with device metadata
        cache_service = get_cache_service(request)
        device_metadata = await enrich_with_device_metadata(device_uuid, cache_service)

        return ConfigVersionsResponse(
            device_uuid=device_uuid,
            filename=filename,
            versions=[
                ConfigVersionResponse(
                    version=v.version,
                    file_type=v.file_type,
                    author=v.author,
                    commit_message=v.commit_message,
                    created_at=v.created_at,
                    content_hash=v.content_hash,
                )
                for v in versions
            ],
            device=device_metadata,
        )
    except Exception as exc:
        logger.exception("Failed to list versions for %s/%s", device_uuid, filename)
        raise HTTPException(status_code=500, detail="Failed to list versions") from exc


@router.get(
    "/{device_uuid}/{filename}/diff",
    response_model=DiffResponse,
    summary="Get diff between two versions",
)
async def get_diff(
    device_uuid: str,
    filename: str,
    request: Request,
    from_version: int,
    to_version: int,
    file_type: FileType = FileType.INTENDED,
    db: AsyncSession = Depends(get_db),
) -> DiffResponse:
    """Generate a diff between two versions of a configuration file."""
    validate_device_id(device_uuid, request)
    try:
        from_config = await get_specific_version(db, device_uuid, filename, file_type, from_version)
        to_config = await get_specific_version(db, device_uuid, filename, file_type, to_version)

        if not from_config or not to_config:
            raise HTTPException(status_code=404, detail="One or both versions not found")

        from_content = decompress_content(from_config.content)
        to_content = decompress_content(to_config.content)

        # Generate unified diff
        from_lines = from_content.splitlines(keepends=True)
        to_lines = to_content.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(
                from_lines,
                to_lines,
                fromfile=f"Version {from_version}",
                tofile=f"Version {to_version}",
                lineterm="",
            )
        )

        # Enrich with device metadata
        cache_service = get_cache_service(request)
        device_metadata = await enrich_with_device_metadata(device_uuid, cache_service)

        return DiffResponse(
            device_uuid=device_uuid,
            filename=filename,
            from_version=from_version,
            to_version=to_version,
            diff="\n".join(diff),
            old_content=from_content,
            new_content=to_content,
            diff_stats={
                "from_lines": len(from_lines),
                "to_lines": len(to_lines),
                "additions": sum(1 for line in diff if line.startswith("+")),
                "deletions": sum(1 for line in diff if line.startswith("-")),
            },
            device=device_metadata,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to generate diff for %s/%s", device_uuid, filename)
        raise HTTPException(status_code=500, detail="Failed to generate diff") from exc
