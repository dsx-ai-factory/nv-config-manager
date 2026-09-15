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
"""Pydantic schemas for API request/response models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from nv_config_manager.config_store.db.models import FileType


class DeviceMetadata(BaseModel):
    """Device metadata from the selected DCIM provider."""

    name: str = Field(..., description="Device name")
    site: str = Field(..., description="Site name")
    platform: str | None = Field(None, description="Platform/OS name")
    role: str | None = Field(None, description="Device role")
    rack: str | None = Field(None, description="Rack name")
    primary_ip4: str | None = Field(None, description="Primary IPv4 address")
    device_url: str | None = Field(None, description="Provider UI link for the device")
    nautobot_url: str | None = Field(
        None,
        description="Legacy alias for device_url when the selected provider is Nautobot",
    )
    last_updated: datetime | None = Field(None, description="When metadata was last refreshed")


class ConfigCreateRequest(BaseModel):
    """Request to create/update a config file."""

    content: str = Field(..., description="Configuration file content")
    author: str = Field(..., description="Author email")
    commit_message: str = Field(..., description="Commit message describing the change")
    file_type: FileType = Field(
        default=FileType.INTENDED, description="Config file type (intended or backup)"
    )
    created_at: datetime | None = Field(
        None, description="Optional timestamp for the version (defaults to current time)"
    )


class ConfigVersionResponse(BaseModel):
    """Response with version metadata."""

    version: int = Field(..., description="Version number")
    file_type: FileType = Field(..., description="Config file type (intended or backup)")
    author: str = Field(..., description="Author email")
    commit_message: str = Field(..., description="Commit message")
    created_at: datetime = Field(..., description="Timestamp when version was created")
    content_hash: str = Field(..., description="SHA256 hash of content")


class ConfigResponse(BaseModel):
    """Response with full config data."""

    id: UUID = Field(..., description="Config file ID")
    device_uuid: str = Field(..., description="DCIM provider device identifier")
    filename: str = Field(..., description="File name")
    file_type: FileType = Field(..., description="Config file type (intended or backup)")
    version: int = Field(..., description="Version number")
    content: str = Field(..., description="Configuration file content")
    content_hash: str = Field(..., description="SHA256 hash of content")
    author: str = Field(..., description="Author email")
    commit_message: str = Field(..., description="Commit message")
    created_at: datetime = Field(..., description="Timestamp when version was created")
    device: DeviceMetadata | None = Field(
        None, description="Device metadata from the selected DCIM provider"
    )


class ConfigVersionsResponse(BaseModel):
    """Response with list of versions."""

    device_uuid: str = Field(..., description="DCIM provider device identifier")
    filename: str = Field(..., description="File name")
    versions: list[ConfigVersionResponse] = Field(..., description="List of versions")
    device: DeviceMetadata | None = Field(
        None, description="Device metadata from the selected DCIM provider"
    )


class BatchConfigItem(BaseModel):
    """Single item in batch request."""

    filename: str = Field(..., description="File name")
    content: str = Field(..., description="Configuration file content")
    author: str = Field(..., description="Author email")
    commit_message: str = Field(..., description="Commit message")
    file_type: FileType = Field(
        default=FileType.INTENDED, description="Config file type (intended or backup)"
    )
    created_at: datetime | None = Field(
        None, description="Optional timestamp for the version (defaults to current time)"
    )


class BatchConfigRequest(BaseModel):
    """Batch config creation request."""

    files: list[BatchConfigItem] = Field(..., description="List of files to create/update")


class BatchConfigResponse(BaseModel):
    """Batch config creation response."""

    created: list[ConfigVersionResponse] = Field(
        ..., description="Successfully created/updated files"
    )
    skipped: list[str] = Field(default_factory=list, description="Paths that had no changes")


class DiffResponse(BaseModel):
    """Response with diff between two versions."""

    device_uuid: str = Field(..., description="DCIM provider device identifier")
    filename: str = Field(..., description="File name")
    from_version: int = Field(..., description="Source version")
    to_version: int = Field(..., description="Target version")
    diff: str = Field(..., description="Unified diff output")
    old_content: str = Field(..., description="Content of source version")
    new_content: str = Field(..., description="Content of target version")
    diff_stats: dict[str, int] = Field(..., description="Statistics about the diff")
    device: DeviceMetadata | None = Field(
        None, description="Device metadata from the selected DCIM provider"
    )
