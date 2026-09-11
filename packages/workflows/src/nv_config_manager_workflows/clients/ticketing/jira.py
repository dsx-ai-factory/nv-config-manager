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
"""Configuration-independent Jira Data Center ticketing provider."""

from __future__ import annotations

import logging
import types
from typing import Any, Self, TypedDict, cast

import aiohttp

from nv_config_manager_workflows.clients.ticketing.base import (
    TicketingProvider,
    TicketingSettings,
)
from nv_config_manager_workflows.clients.ticketing.registry import TICKETING_PROVIDERS

logger = logging.getLogger(__name__)


class JiraSettings(TypedDict):
    """Explicit connection settings for :class:`JiraTicketingProvider`."""

    base_url: str
    api_token: str


class JiraClientError(Exception):
    """Raised when a Jira request fails or returns an unexpected response."""


class JiraTicketingProvider(TicketingProvider):
    """Asynchronous Jira Data Center ticketing provider."""

    max_attachment_size = 10 * 1024 * 1024 # 10 MB — Jira Data Center server-side limit

    def __init__(self, base_url: str, api_token: str) -> None:
        """Initialize the provider from explicit connection settings."""
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_settings(cls, settings: TicketingSettings) -> JiraTicketingProvider:
        """Construct the Jira provider from explicit settings."""
        return cls(
            base_url=cast("str", settings["base_url"]),
            api_token=cast("str", settings["api_token"]),
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the shared HTTP session on first use."""
        if not self._session:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=60, connect=10),
            )
        return self._session

    async def __aenter__(self) -> Self:
        await self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def validate_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch a Jira issue and return its metadata."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}"
        async with session.get(url) as response:
            if response.status == 404:
                raise JiraClientError(
                    f"Jira issue '{issue_key}' not found. "
                    "Verify the ticket ID before re-triggering the workflow."
                )
            if response.status != 200:
                text = await response.text()
                raise JiraClientError(
                    f"Failed to fetch issue '{issue_key}': HTTP {response.status} — {text}"
                )
            logger.debug("Fetched Jira issue %s", issue_key)
            return dict(await response.json())

    async def upload_attachment(
        self,
        issue_key: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Upload a direct attachment to a Jira issue."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}/attachments"
        form = aiohttp.FormData()
        form.add_field("file", content, filename=filename, content_type=content_type)

        async with session.post(
            url,
            data=form,
            headers={**self._headers, "X-Atlassian-Token": "no-check"},
        ) as response:
            if response.status not in (200, 201):
                text = await response.text()
                raise JiraClientError(
                    f"Failed to upload attachment to '{issue_key}': HTTP {response.status} — {text}"
                )
            attachments = await response.json()

        attachment = attachments[0]
        result = attachment.get("content") or attachment.get("self") or str(attachment["id"])
        logger.debug("Uploaded attachment %r to %s: %s", filename, issue_key, result)
        return result

    async def add_comment(self, issue_key: str, body: str) -> str:
        """Add a plain-text comment to a Jira issue."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}/comment"

        async with session.post(url, json={"body": body}) as response:
            if response.status not in (200, 201):
                text = await response.text()
                raise JiraClientError(
                    f"Failed to add comment to '{issue_key}': HTTP {response.status} — {text}"
                )
            data = await response.json()

        comment_id = str(data["id"])
        logger.debug("Added comment %s to %s", comment_id, issue_key)
        return comment_id


TICKETING_PROVIDERS["jira"] = JiraTicketingProvider
