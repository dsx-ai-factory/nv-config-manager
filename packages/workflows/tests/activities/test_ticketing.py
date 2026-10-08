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
"""Tests for package-owned ticketing activities."""

from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import diagnostics, ticketing
from nv_config_manager_workflows.activities.diagnostics import activities as diagnostics_activities
from nv_config_manager_workflows.activities.ticketing import activities as ticketing_activities


def _provider(
    *,
    issue: dict[str, object] | None = None,
    attachment: str = "attachment-1",
    comment: str = "comment-1",
    limit: int | None = None,
) -> AsyncMock:
    provider = AsyncMock()
    provider.__aenter__.return_value = provider
    provider.__aexit__.return_value = None
    provider.validate_issue.return_value = issue or {
        "self": "https://jira.example.test/issue/ABC-1",
        "fields": {"summary": "A summary", "status": {"name": "In Progress"}},
    }
    provider.upload_attachment.return_value = attachment
    provider.add_comment.return_value = comment
    provider.max_attachment_size = limit
    return provider


def test_attachment_input_coerces_temporal_lists_including_empty() -> None:
    """Temporal JSON list[int] payloads become the original bytes."""
    common = {
        "ticketing_platform": "jira",
        "issue_key": "ABC-1",
        "filename": "diagnostics.txt",
        "content_type": "text/plain",
    }
    assert (
        ticketing.UploadAttachmentInput.model_validate({**common, "content": [104, 105]}).content
        == b"hi"
    )
    assert ticketing.UploadAttachmentInput.model_validate({**common, "content": []}).content == b""
    assert ticketing.UploadAttachmentInput(**common, content=b"raw").content == b"raw"


async def test_validate_ticket_normalizes_nested_and_flat_provider_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested Jira fields and flat provider values preserve their current fallbacks."""
    nested = _provider()
    factory = Mock(return_value=nested)
    monkeypatch.setattr(ticketing_activities, "get_ticketing_provider", factory)
    result = await ticketing.validate_ticket(
        ticketing.ValidateTicketInput(ticketing_platform="jira", issue_key="ABC-1")
    )
    assert result == ticketing.ValidateTicketOutput(
        summary="A summary",
        status="In Progress",
        url="https://jira.example.test/issue/ABC-1",
    )
    nested.validate_issue.assert_awaited_once_with("ABC-1")

    flat = _provider(issue={"summary": "Flat", "status": 3})
    factory.return_value = flat
    result = await ticketing.validate_ticket(
        ticketing.ValidateTicketInput(ticketing_platform="custom", issue_key="C-2")
    )
    assert result == ticketing.ValidateTicketOutput(summary="Flat", status="3", url="")
    assert factory.call_args_list[1].args == ("custom",)

    missing = _provider(issue={"fields": {}})
    factory.return_value = missing
    result = await ticketing.validate_ticket(
        ticketing.ValidateTicketInput(ticketing_platform="jira", issue_key="ABC-3")
    )
    assert result == ticketing.ValidateTicketOutput(summary="", status="", url="")


async def test_only_validation_converts_jira_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation makes Jira failures permanent while direct operations propagate them."""
    provider = _provider()
    error = ticketing.JiraClientError("not found")
    provider.validate_issue.side_effect = error
    monkeypatch.setattr(ticketing_activities, "get_ticketing_provider", Mock(return_value=provider))

    with pytest.raises(ApplicationError, match="not found") as exc_info:
        await ticketing.validate_ticket(
            ticketing.ValidateTicketInput(ticketing_platform="jira", issue_key="ABC-1")
        )
    assert exc_info.value.non_retryable is True
    assert exc_info.value.__cause__ is error

    provider.upload_attachment.side_effect = error
    with pytest.raises(ticketing.JiraClientError, match="not found"):
        await ticketing.upload_attachment(
            ticketing.UploadAttachmentInput(
                ticketing_platform="jira",
                issue_key="ABC-1",
                filename="x",
                content=b"x",
                content_type="text/plain",
            )
        )


async def test_upload_and_comment_pass_through_all_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct ticket operations preserve payload values and result mapping."""
    provider = _provider(attachment="url-7", comment="comment-8")
    monkeypatch.setattr(ticketing_activities, "get_ticketing_provider", Mock(return_value=provider))

    uploaded = await ticketing.upload_attachment(
        ticketing.UploadAttachmentInput(
            ticketing_platform="jira",
            issue_key="ABC-1",
            filename="diagnostics.txt",
            content=b"payload",
            content_type="text/plain",
        )
    )
    commented = await ticketing.add_ticket_comment(
        ticketing.AddCommentInput(ticketing_platform="jira", issue_key="ABC-1", body="Completed")
    )
    assert uploaded == ticketing.UploadAttachmentOutput(
        attachment_id="url-7", attachment_url="url-7"
    )
    assert commented == ticketing.AddCommentOutput(comment_id="comment-8")
    provider.upload_attachment.assert_awaited_once_with(
        "ABC-1", "diagnostics.txt", b"payload", "text/plain"
    )
    provider.add_comment.assert_awaited_once_with("ABC-1", "Completed")


async def test_tech_support_upload_reads_raw_bytes_and_retains_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis bytes are uploaded unchanged and the source key is not deleted."""
    cache = Mock()
    cache.get = AsyncMock(return_value=b"bundle-bytes")
    provider = _provider(attachment="attachment-9", limit=100)
    monkeypatch.setattr(diagnostics_activities, "get_redis_client", Mock(return_value=cache))
    monkeypatch.setattr(
        diagnostics_activities, "get_ticketing_provider", Mock(return_value=provider)
    )
    activity_input = diagnostics.UploadTechSupportFromRedisInput(
        ticketing_platform="jira",
        issue_key="ABC-1",
        device_name="switch-1",
        redis_key="tech-support:wf:switch-1",
    )

    result = await diagnostics.upload_tech_support_from_redis(activity_input)

    assert result.attachment_id == "attachment-9"
    cache.get.assert_awaited_once_with("tech-support:wf:switch-1", deserialize=False)
    assert not any(record[0] == "delete" for record in cache.mock_calls)
    provider.upload_attachment.assert_awaited_once_with(
        "ABC-1",
        "tech-support_switch-1.tar.gz",
        b"bundle-bytes",
        "application/gzip",
    )


async def test_tech_support_upload_missing_and_oversize_failures_are_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing and oversized bundles retain exact messages, type, and retry policy."""
    cache = Mock()
    cache.get = AsyncMock(return_value=None)
    monkeypatch.setattr(diagnostics_activities, "get_redis_client", Mock(return_value=cache))
    activity_input = diagnostics.UploadTechSupportFromRedisInput(
        ticketing_platform="jira",
        issue_key="ABC-1",
        device_name="switch-1",
        redis_key="bundle-key",
    )
    with pytest.raises(ApplicationError, match=r"key=bundle-key.*may have expired") as missing:
        await diagnostics.upload_tech_support_from_redis(activity_input)
    assert missing.value.non_retryable is True

    cache.get.return_value = b"x" * (2 * 1024 * 1024)
    provider = _provider(limit=1024 * 1024)
    monkeypatch.setattr(
        diagnostics_activities, "get_ticketing_provider", Mock(return_value=provider)
    )
    with pytest.raises(ApplicationError, match=r"\(2 MB\).*limit \(1 MB\)\.") as oversized:
        await diagnostics.upload_tech_support_from_redis(activity_input)
    assert oversized.value.type == "attachment_too_large"
    assert oversized.value.non_retryable is True
    provider.upload_attachment.assert_not_awaited()
