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
"""Provider-aware Config Store device identifier validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from nv_config_manager.config_store.core.device_cache_redis import DeviceCacheService


def get_cache_service(request: Request) -> DeviceCacheService | None:
    """Return the application cache service when it initialized successfully."""
    return getattr(request.app.state, "cache_service", None)


def validate_device_id(device_uuid: str, request: Request) -> str:
    """Validate an opaque device ID with the configured DCIM provider."""
    cache_service = get_cache_service(request)
    provider_client = (
        cache_service.dcim_client
        if cache_service
        else getattr(request.app.state, "dcim_client", None)
    )
    if provider_client is None:
        raise HTTPException(
            status_code=503,
            detail="DCIM provider validation is unavailable",
        )
    if not provider_client.is_valid_device_id(device_uuid):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid device identifier for configured DCIM provider: {device_uuid}",
        )
    return device_uuid
