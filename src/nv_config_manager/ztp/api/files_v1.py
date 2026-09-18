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
"""S3 File Endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from nv_config_manager.common.auth import SSOIdentity, require_authenticated_identity
from nv_config_manager.common.config import get_storage_client
from nv_config_manager.ztp.api.schemas import ChecksumResponse, FileInfo, ObjectInfo
from nv_config_manager.ztp.api.streaming import create_object_storage_streaming_response
from nv_config_manager.ztp.storage import (
    ObjectStorageExistsException,
    ObjectStorageNotAuthorizedException,
    ObjectStorageNotFoundException,
)

router = APIRouter(prefix="/files", tags=["files"], responses={404: {"description": "Not found"}})


@router.head("/{platform}/{version}/{filename}", response_class=Response)
async def check_object(platform: str, version: str, filename: str) -> Response:
    """Check whether a file exists without opening a download stream."""
    storage_client = get_storage_client()
    try:
        async with storage_client:
            await storage_client.get_object_metadata(platform, version, filename)
        return Response(status_code=200)
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="File not found in storage.") from exc


@router.get(
    "/{platform}/{version}/{filename}",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Firmware file content",
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
    },
)
async def load_object(
    platform: str, version: str, filename: str, request: Request
) -> StreamingResponse:
    """Load the firmware by platform and version."""
    storage_client = get_storage_client()
    try:
        return await create_object_storage_streaming_response(
            storage_client,
            storage_client.get_object,
            platform,
            version,
            filename,
            request=request,
        )
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="File not found in S3.") from exc


@router.get("/{platform}/{version}/{filename}/checksum")
async def load_checksum(platform: str, version: str, filename: str) -> ChecksumResponse:
    """Load the firmware checksum by platform and version."""
    storage_client = get_storage_client()
    try:
        async with storage_client:
            checksum = await storage_client.get_checksum(platform, version, filename)
        return ChecksumResponse(checksum=checksum)
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Checksum data not found for {platform}/{version}/{filename}.",
        ) from exc


@router.get("/{platform}/{version}/")
async def list_files(platform: str, version: str) -> list[FileInfo]:
    """List files associated with the given platform and version."""
    storage_client = get_storage_client()
    async with storage_client:
        objects = await storage_client.list_object_keys(platform, version)
    return [FileInfo(**obj) for obj in objects]


@router.get("/")
async def list_all_files() -> list[ObjectInfo]:
    """List all files and their metadata in the storage backend."""
    storage_client = get_storage_client()
    async with storage_client:
        objects = await storage_client.list_all_objects()
    return [ObjectInfo(**obj) for obj in objects]


@router.post("/{platform}/{version}/{filename}")
async def upload_file(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    platform: str,
    version: str,
    filename: str,
    file: UploadFile,
    checksum: str,
    overwrite: bool = False,
    firmware_image: bool = False,
    identity: SSOIdentity = Depends(require_authenticated_identity),  # noqa: B008
) -> str:
    """Upload a file to the storage backend.

    Set ``firmware_image=true`` to tag this file as the OS/firmware image for
    the given platform and version. Only one firmware image is allowed per
    platform/version directory; the upload will be rejected if a *different*
    file already occupies that slot.
    """
    storage_client = get_storage_client()
    try:
        async with storage_client:
            await storage_client.upload_file(
                platform, version, filename, checksum, file.file, overwrite, firmware_image
            )
        return "OK"
    except ObjectStorageExistsException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ObjectStorageNotAuthorizedException as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
