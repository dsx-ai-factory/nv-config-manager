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
"""Provider-response and attachment validation helpers for ticketing activities."""

from typing import Any

from temporalio.exceptions import ApplicationError


def normalize_issue(issue: dict[str, Any]) -> tuple[str, str, str]:
    """Normalize nested Jira data or a flat provider response."""
    fields = issue.get("fields", issue)
    summary = fields.get("summary", "")
    status_field = fields.get("status", {})
    status = status_field.get("name", "") if isinstance(status_field, dict) else str(status_field)
    url = issue.get("self", "")
    return summary, status, url


def validate_attachment_size(
    *,
    device_name: str,
    ticketing_platform: str,
    content_size: int,
    max_attachment_size: int | None,
) -> None:
    """Raise a permanent activity error when an attachment exceeds its provider limit."""
    if max_attachment_size is None or content_size <= max_attachment_size:
        return

    limit_mb = max_attachment_size // (1024 * 1024)
    raise ApplicationError(
        f"Tech-support bundle for '{device_name}' ({content_size // (1024 * 1024)} MB) "
        f"exceeds the {ticketing_platform} attachment size limit ({limit_mb} MB).",
        type="attachment_too_large",
        non_retryable=True,
    )


__all__ = ["normalize_issue", "validate_attachment_size"]
