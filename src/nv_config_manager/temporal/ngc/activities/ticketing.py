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
"""Ticketing activities — validate ticket, upload attachment, post comment.

Each activity resolves its provider via get_ticketing_provider(platform), so
the workflow only needs to pass a platform name string (e.g. "jira") rather
than knowing anything about the underlying client.

The side-effect import of nv_config_manager.temporal.client.jira ensures that
JiraTicketingProvider is registered in TICKETING_PROVIDERS before any
activity runs.  Adding a new backend only requires adding a similar import.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, field_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

import nv_config_manager.temporal.client.jira  # noqa: F401 — registers JiraTicketingProvider
from nv_config_manager.common.client.redis import redis_settings
from nv_config_manager.common.config import load_config
from nv_config_manager.temporal.client.jira import JiraClientError
from nv_config_manager.temporal.client.ticketing import get_ticketing_provider
from nv_config_manager_workflows.clients import RedisClient

# =============================================================================
# Input / Output Models
# =============================================================================


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
    def _coerce_bytes(cls, v: object) -> object:
        # Temporal's JSON encoder serialises bytes as list[int]; convert back.
        if isinstance(v, list):
            return bytes(cast(list[int], v))
        return v


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


# =============================================================================
# Activity Functions
# =============================================================================


@activity.defn
async def validate_ticket(activity_input: ValidateTicketInput) -> ValidateTicketOutput:
    """Confirm the ticket exists and return its key metadata.

    Intended as a fast pre-flight check at the start of the workflow — fail
    early if the ticket is not found before running any device commands.

    The issue dict returned by the provider is expected to contain at minimum:
      - "summary" — the issue title
      - "status"  — the current status name
      - "url"     — a link to the issue (REST self-link or browse URL)
    For Jira these are extracted from the nested fields structure.
    """
    try:
        async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
            issue = await provider.validate_issue(activity_input.issue_key)
    except JiraClientError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc

    # Normalise the raw provider dict into the three standard output fields.
    # Jira returns a nested structure under "fields"; other providers should
    # return a flat dict with "summary", "status", and "url" directly.
    fields = issue.get("fields", issue)
    summary = fields.get("summary", "")
    status_field = fields.get("status", {})
    status = status_field.get("name", "") if isinstance(status_field, dict) else str(status_field)
    url = issue.get("self", "")

    return ValidateTicketOutput(summary=summary, status=status, url=url)


@activity.defn
async def upload_attachment(activity_input: UploadAttachmentInput) -> UploadAttachmentOutput:
    """Upload a file as a direct attachment on the ticket.

    The provider returns a single string that is either the attachment URL
    (Jira returns the ``self`` API URL) or the attachment ID.  Both output
    fields are populated with this value so the workflow can use whichever
    is most convenient.
    """
    async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
        result = await provider.upload_attachment(
            activity_input.issue_key,
            activity_input.filename,
            activity_input.content,
            activity_input.content_type,
        )
    return UploadAttachmentOutput(attachment_id=result, attachment_url=result)


@activity.defn
async def add_ticket_comment(activity_input: AddCommentInput) -> AddCommentOutput:
    """Post a plain-text comment on the ticket."""
    async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
        comment_id = await provider.add_comment(activity_input.issue_key, activity_input.body)
    return AddCommentOutput(comment_id=comment_id)


@activity.defn
async def upload_tech_support_from_redis(
    activity_input: UploadTechSupportFromRedisInput,
) -> UploadAttachmentOutput:
    """Read a tech-support bundle from Redis and upload it as a ticket attachment.

    The bundle bytes are stored in Redis by collect_tech_support_bundle to avoid
    passing a large payload through Temporal.  This activity reads them back and
    forwards them directly to the ticketing provider.
    """
    async with RedisClient(**redis_settings(load_config())) as cache:
        content: bytes | None = await cache.get(activity_input.redis_key, deserialize=False)
    if content is None:
        raise ApplicationError(
            f"Tech-support bundle for '{activity_input.device_name}' not found in Redis "
            f"(key={activity_input.redis_key}). It may have expired.",
            non_retryable=True,
        )
    filename = f"tech-support_{activity_input.device_name}.tar.gz"
    async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
        if provider.max_attachment_size is not None and len(content) > provider.max_attachment_size:
            limit_mb = provider.max_attachment_size // (1024 * 1024)
            raise ApplicationError(
                f"Tech-support bundle for '{activity_input.device_name}' ({len(content) // (1024 * 1024)} MB) "
                f"exceeds the {activity_input.ticketing_platform} attachment size limit ({limit_mb} MB).",
                type="attachment_too_large",
                non_retryable=True,
            )
        result = await provider.upload_attachment(
            activity_input.issue_key,
            filename,
            content,
            "application/gzip",
        )
    return UploadAttachmentOutput(attachment_id=result, attachment_url=result)
