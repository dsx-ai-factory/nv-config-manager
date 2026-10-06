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
"""V1 Device API Endpoints."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from nv_config_manager.common.config import get_storage_client
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.ztp.api.schemas import ChecksumResponse
from nv_config_manager.ztp.api.storage_clients import get_object_storage_client, guarded_storage
from nv_config_manager.ztp.api.streaming import create_object_storage_streaming_response
from nv_config_manager.ztp.storage import ObjectStorageNotFoundException

logger = get_logger(__name__, category=LogCategory.ZTP)

router = APIRouter(
    prefix="/firmware", tags=["firmware"], responses={404: {"description": "Not found"}}
)


@router.get("/{platform}/{version}", response_class=StreamingResponse)
async def load_firmware(platform: str, version: str, request: Request) -> StreamingResponse:
    """Load the firmware by platform and version.

    Note: For large firmware files, use curl or direct browser download instead of
    the OpenAPI UI, which may not handle large streaming responses properly.
    """
    storage_client = get_storage_client()
    try:
        return await create_object_storage_streaming_response(
            storage_client,
            storage_client.get_firmware_object,
            platform,
            version,
            request=request,
        )
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="Firmware image not found in S3.") from exc


@router.get("/{platform}/{version}/checksum")
async def load_firmware_checksum(platform: str, version: str) -> ChecksumResponse:
    """Load the firmware checksum by platform and version."""
    storage_client = await get_object_storage_client()
    try:
        checksum = await guarded_storage(
            lambda: storage_client.get_firmware_checksum(platform, version)
        )
        return ChecksumResponse(checksum=checksum)
    except ObjectStorageNotFoundException as exc:
        raise HTTPException(status_code=404, detail="Firmware image not found in S3.") from exc
