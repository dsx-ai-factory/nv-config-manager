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
"""Ticket validation, attachment upload, and comment activities."""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ticketing.helpers import (
    normalize_issue,
    validate_attachment_size,
)
from nv_config_manager_workflows.activities.ticketing.models import (
    AddCommentInput,
    AddCommentOutput,
    UploadAttachmentInput,
    UploadAttachmentOutput,
    UploadTechSupportFromRedisInput,
    ValidateTicketInput,
    ValidateTicketOutput,
)
from nv_config_manager_workflows.clients.ticketing.jira import JiraClientError
from nv_config_manager_workflows.runtime import get_redis_client, get_ticketing_provider


@activity.defn
async def validate_ticket(activity_input: ValidateTicketInput) -> ValidateTicketOutput:
    """Confirm the ticket exists and return its key metadata."""
    try:
        async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
            issue = await provider.validate_issue(activity_input.issue_key)
    except JiraClientError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc

    summary, status, url = normalize_issue(issue)
    return ValidateTicketOutput(summary=summary, status=status, url=url)


@activity.defn
async def upload_attachment(activity_input: UploadAttachmentInput) -> UploadAttachmentOutput:
    """Upload a file as a direct attachment on the ticket."""
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
    """Read a tech-support bundle from Redis and upload it as a ticket attachment."""
    cache = get_redis_client()
    content: bytes | None = await cache.get(activity_input.redis_key, deserialize=False)
    if content is None:
        raise ApplicationError(
            f"Tech-support bundle for '{activity_input.device_name}' not found in Redis "
            f"(key={activity_input.redis_key}). It may have expired.",
            non_retryable=True,
        )

    filename = f"tech-support_{activity_input.device_name}.tar.gz"
    async with get_ticketing_provider(activity_input.ticketing_platform) as provider:
        validate_attachment_size(
            device_name=activity_input.device_name,
            ticketing_platform=activity_input.ticketing_platform,
            content_size=len(content),
            max_attachment_size=provider.max_attachment_size,
        )
        result = await provider.upload_attachment(
            activity_input.issue_key,
            filename,
            content,
            "application/gzip",
        )
    return UploadAttachmentOutput(attachment_id=result, attachment_url=result)


TICKETING_ACTIVITIES = (
    validate_ticket,
    upload_attachment,
    upload_tech_support_from_redis,
    add_ticket_comment,
)

__all__ = [
    "AddCommentInput",
    "AddCommentOutput",
    "JiraClientError",
    "TICKETING_ACTIVITIES",
    "UploadAttachmentInput",
    "UploadAttachmentOutput",
    "UploadTechSupportFromRedisInput",
    "ValidateTicketInput",
    "ValidateTicketOutput",
    "add_ticket_comment",
    "upload_attachment",
    "upload_tech_support_from_redis",
    "validate_ticket",
]
