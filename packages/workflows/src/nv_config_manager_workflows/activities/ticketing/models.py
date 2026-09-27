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
"""Input and output models for ticketing activities."""

from typing import cast

from pydantic import BaseModel, field_validator


class ValidateTicketInput(BaseModel):
    ticketing_platform: str  # e.g. "jira"
    issue_key: str  # e.g. "GNI-1234"


class ValidateTicketOutput(BaseModel):
    summary: str  # issue title / summary line
    status: str  # workflow status name, e.g. "In Progress"
    url: str  # URL to the issue (REST self-link or browse URL)


class UploadAttachmentInput(BaseModel):
    ticketing_platform: str
    issue_key: str
    filename: str  # attachment filename, e.g. "diagnostics.txt"
    content: bytes  # raw file bytes
    content_type: str  # MIME type, e.g. "text/plain"

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_bytes(cls, value: object) -> object:
        """Convert Temporal's JSON list representation back to bytes."""
        if isinstance(value, list):
            return bytes(cast(list[int], value))
        return value


class UploadAttachmentOutput(BaseModel):
    attachment_id: str  # provider-returned ID or URL (whichever was returned)
    attachment_url: str  # same value — providers return one string covering both


class AddCommentInput(BaseModel):
    ticketing_platform: str
    issue_key: str
    body: str  # plain-text comment body


class AddCommentOutput(BaseModel):
    comment_id: str


class UploadTechSupportFromRedisInput(BaseModel):
    ticketing_platform: str
    issue_key: str
    device_name: str
    redis_key: str  # key used by collect_tech_support_bundle to store the bundle


__all__ = [
    "AddCommentInput",
    "AddCommentOutput",
    "UploadAttachmentInput",
    "UploadAttachmentOutput",
    "UploadTechSupportFromRedisInput",
    "ValidateTicketInput",
    "ValidateTicketOutput",
]
