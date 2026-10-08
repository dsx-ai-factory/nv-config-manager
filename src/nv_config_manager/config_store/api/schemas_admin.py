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
"""Pydantic schemas for admin API responses."""

from pydantic import BaseModel, Field

_DEVICE_UUID_DESCRIPTION = "DCIM provider device identifier"
_DEVICE_NAME_DESCRIPTION = "Device name"


class StatsResponse(BaseModel):
    """Database statistics response."""

    total_config_versions: int = Field(description="Total number of config file versions")
    unique_devices: int = Field(description="Number of unique devices with configs")
    unique_files: int = Field(description="Number of unique config files (device + filename)")
    storage_bytes: int = Field(description="Total storage used in bytes (compressed)")
    storage_mb: float = Field(description="Total storage used in MB (compressed)")


class CacheStatusResponse(BaseModel):
    """Cache service status response."""

    enabled: bool = Field(description="Whether cache service is enabled")
    message: str | None = Field(None, description="Status message if disabled")
    cache_ttl: int | None = Field(None, description="Cache TTL in seconds")
    redis_connected: bool | None = Field(None, description="Whether Redis is connected")
    dcim_connected: bool | None = Field(
        None, description="Whether the selected DCIM client is available"
    )
    nautobot_connected: bool | None = Field(None, description="Legacy alias for dcim_connected")


class CacheTestFoundResponse(BaseModel):
    """Cache test response when device is found."""

    found: bool = Field(True, description="Whether device was found")
    device_uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)
    device_name: str = Field(description=_DEVICE_NAME_DESCRIPTION)
    site: str = Field(description="Site name")
    platform: str | None = Field(description="Platform name")


class CacheTestNotFoundResponse(BaseModel):
    """Cache test response when device is not found."""

    found: bool = Field(False, description="Whether device was found")
    device_uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)
    message: str = Field(description="Not found message")


class CacheTestErrorResponse(BaseModel):
    """Cache test response when there's an error."""

    error: str = Field(description="Error message")
    device_uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)


class DeviceLatestConfig(BaseModel):
    """Device with its latest config metadata."""

    uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)
    name: str = Field(description=_DEVICE_NAME_DESCRIPTION)
    site: str = Field(description="Site name")
    latest_update: str = Field(description="Latest config update timestamp")
    latest_author: str = Field(description="Author of latest change")
    latest_message: str = Field(description="Commit message of latest change")
    active: bool = Field(
        True, description="Whether the device is currently active in nv-config-manager"
    )


class DeviceUUID(BaseModel):
    """Provider-owned device identifier entry.

    The class and response field names are retained for API compatibility.
    """

    uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)


class DeleteDeviceResponse(BaseModel):
    """Response for device config deletion."""

    device_uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)
    deleted_versions: int = Field(description="Number of config versions deleted")
    message: str = Field(description="Human-readable result message")


class ConfigSearchResult(BaseModel):
    """Config file search result with metadata."""

    id: str = Field(description="Config file ID")
    device_uuid: str = Field(description=_DEVICE_UUID_DESCRIPTION)
    device_name: str | None = Field(None, description=_DEVICE_NAME_DESCRIPTION)
    site: str | None = Field(None, description="Site name")
    filename: str = Field(description="Filename")
    file_type: str = Field(description="Config file type")
    version: int = Field(description="Version number")
    author: str = Field(description="Author")
    commit_message: str = Field(description="Commit message")
    created_at: str = Field(description="Creation timestamp")
