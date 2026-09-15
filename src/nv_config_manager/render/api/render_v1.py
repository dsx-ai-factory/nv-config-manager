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
"""V1 Render API Endpoints."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from nv_config_manager.common.auth import get_sso_user
from nv_config_manager.common.client.render import FileCommit
from nv_config_manager.common.config import (
    is_aggregate_environment,
)
from nv_config_manager.dcim import dcim_client_session
from nv_config_manager.render.events.util import queue_render_batch
from nv_config_manager.render.lock import create_lock
from nv_config_manager.render.render import execute_render

responses: dict[int | str, dict[str, Any]] = {
    200: {"description": "Render produced no diff"},
    201: {"description": "Successfully rendered a new configuration version"},
    202: {"description": "Successfully queued renders for all devices"},
    404: {"description": "Not found"},
    409: {"description": "Could not acquire lock within timeout"},
}

router = APIRouter(prefix="/render", responses=responses)


class RenderRequest(BaseModel):
    """Request body for a render operation."""

    commit_message: str | None = None


class BatchRenderRequest(BaseModel):
    """Request body for a batch render operation."""

    device_uuids: list[str]
    commit_message: str | None = None
    max_concurrency: int = 20


class RenderResponse(BaseModel):
    """Response from a single device render operation."""

    updated_files: list[FileCommit] = Field(
        default_factory=list, description="Files that changed during the render"
    )


class FailedDevice(BaseModel):
    """A device that failed to queue for rendering."""

    device_uuid: str
    error: str


class BulkRenderResponse(BaseModel):
    """Response from a bulk render operation (render-all or batch)."""

    message: str
    queued_count: int
    total_devices: int = Field(default=0, description="Total number of devices targeted")
    max_concurrency: int | None = Field(
        default=None, description="Maximum concurrent renders (batch only)"
    )
    failed_devices: list[FailedDevice] | None = Field(
        default=None, description="Devices that failed to queue"
    )


async def get_render_enabled_devices() -> list[str]:
    """Get all render-enabled device IDs from the selected DCIM provider."""
    is_aggregate_managed = is_aggregate_environment()
    try:
        async with dcim_client_session() as dcim_client:
            return await dcim_client.get_render_enabled_device_ids(is_aggregate_managed)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to query render-enabled devices: {exc}"
        ) from exc


@router.post("/{device_uuid}/render")
async def render(
    device_uuid: str,
    request: Request,
    response: Response,
    body: RenderRequest | None = None,
) -> RenderResponse:
    """Perform a fresh render for a device."""
    user = get_sso_user(request)
    if body and body.commit_message:
        commit_message = body.commit_message
    else:
        commit_message = f"Manual render initiated by {user}"

    lock = await create_lock(device_uuid, blocking_timeout=30)
    if not await lock.acquire():
        raise HTTPException(status_code=409, detail="Could not acquire lock within timeout")
    try:
        updated_files = await execute_render(device_uuid, commit_message, user)
        response.status_code = 200 if not updated_files else 201
        return RenderResponse(updated_files=updated_files)
    finally:
        await lock.release()


@router.post("/all", response_model=BulkRenderResponse, response_model_exclude_none=True)
async def render_all(
    request: Request,
    response: Response,
    body: RenderRequest | None = None,
) -> BulkRenderResponse:
    """Queue renders for all render-enabled devices."""
    user = get_sso_user(request)

    if body and body.commit_message:
        commit_message = body.commit_message
    else:
        commit_message = f"Bulk render initiated by {user}"

    # Get all render-enabled devices
    device_uuids = await get_render_enabled_devices()

    if not device_uuids:
        response.status_code = 200
        return BulkRenderResponse(
            message="No render-enabled devices found", queued_count=0, total_devices=0
        )

    # Queue renders for all devices using optimized batch processing
    timestamp = datetime.now(UTC).isoformat()

    queued_count, failed_devices = await queue_render_batch(
        device_uuids, commit_message, user, timestamp
    )

    response.status_code = 202
    return BulkRenderResponse(
        message=f"Queued renders for {queued_count} devices",
        queued_count=queued_count,
        total_devices=len(device_uuids),
        failed_devices=[FailedDevice(**d) for d in failed_devices] if failed_devices else None,
    )


@router.post("/batch", response_model=BulkRenderResponse, response_model_exclude_none=True)
async def render_batch(
    request: Request,
    response: Response,
    body: BatchRenderRequest,
) -> BulkRenderResponse:
    """Queue renders for a specific list of devices with optimized batch processing."""
    user = get_sso_user(request)

    if body.commit_message:
        commit_message = body.commit_message
    else:
        commit_message = f"Batch render initiated by {user}"

    if not body.device_uuids:
        response.status_code = 200
        return BulkRenderResponse(message="No devices specified", queued_count=0, total_devices=0)

    # Queue renders for specified devices using batch processing
    timestamp = datetime.now(UTC).isoformat()

    queued_count, failed_devices = await queue_render_batch(
        body.device_uuids,
        commit_message,
        user,
        timestamp,
        max_concurrency=body.max_concurrency,
    )

    response.status_code = 202
    return BulkRenderResponse(
        message=f"Queued renders for {queued_count} devices",
        queued_count=queued_count,
        total_devices=len(body.device_uuids),
        max_concurrency=body.max_concurrency,
        failed_devices=[FailedDevice(**d) for d in failed_devices] if failed_devices else None,
    )
