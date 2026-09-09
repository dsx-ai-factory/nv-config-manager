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
"""Jira REST API ticketing provider.

Implements TicketingProvider for Jira Data Center, covering the three operations
needed by DiagnosticsWorkflow:

  - validate_issue      — confirm a ticket exists before running device commands
  - upload_attachment   — attach a file directly to the issue (multipart POST)
  - add_comment         — post a plain-text summary comment on the ticket

Auth uses a Bearer PAT (Personal Access Token) from the [jira] section of nv-config-manager.ini.
The client uses aiohttp for async HTTP, consistent with the rest of the codebase.

This module registers itself in TICKETING_PROVIDERS under the key "jira" so
that get_ticketing_provider("jira") resolves to this class automatically.

No Temporal imports — this file is intentionally framework-agnostic.
"""

from __future__ import annotations

import logging
import types
from typing import Any, Self

import aiohttp

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.client.ticketing import TICKETING_PROVIDERS, TicketingProvider
from nv_config_manager.temporal.common.secrets import get_credential

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class JiraClientError(Exception):
    """Raised when a Jira API request fails or returns an unexpected status."""


class JiraTicketingProvider(TicketingProvider):
    """Jira Data Center implementation of TicketingProvider.

    Uses aiohttp for async HTTP, consistent with NautobotClient and other
    clients in the codebase. Use as an async context manager:

        async with JiraTicketingProvider.from_config() as client:
            issue = await client.validate_issue("GNI-1234")

    Credential lookup order (via from_config):
      1. nv-config-manager.ini [jira] section — base_url, api_token
    """

    max_attachment_size: int = 10 * 1024 * 1024  # 10 MB — Jira Data Center server-side limit

    def __init__(self, base_url: str, api_token: str) -> None:
        """Initialise the provider with explicit credentials.

        Args:
            base_url:  Jira base URL, e.g. "https://jirasw.exmple.nvidia.com"
            api_token: Personal Access Token (PAT) for Bearer authentication.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(cls) -> JiraTicketingProvider:
        """Instantiate from the [jira] section of nv-config-manager.ini."""
        config = load_config()
        return cls(
            base_url=get_credential(config, "jira", "base_url"),
            api_token=get_credential(config, "jira", "api_token"),
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Lazily create and return the shared aiohttp session."""
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
        """Fetch a Jira issue and return its metadata.

        Args:
            issue_key: Jira issue key, e.g. "GNI-1234".

        Returns:
            The full issue dict from the Jira API response.

        Raises:
            JiraClientError: If the issue is not found (404) or the request
                returns any other non-200 status.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}"
        async with session.get(url) as rsp:
            if rsp.status == 404:
                raise JiraClientError(
                    f"Jira issue '{issue_key}' not found. "
                    "Verify the ticket ID before re-triggering the workflow."
                )
            if rsp.status != 200:
                text = await rsp.text()
                raise JiraClientError(
                    f"Failed to fetch issue '{issue_key}': HTTP {rsp.status} — {text}"
                )
            logger.debug("Fetched Jira issue %s", issue_key)
            return dict(await rsp.json())

    async def upload_attachment(
        self,
        issue_key: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Upload a file as a direct attachment on a Jira issue.

        Args:
            issue_key:    Jira issue key, e.g. "GNI-1234".
            filename:     Name to give the attachment, e.g. "diagnostics.txt".
            content:      Raw file bytes.
            content_type: MIME type, e.g. "text/plain" or "application/gzip".

        Returns:
            The ``self`` URL of the first attachment object in the response,
            falling back to the attachment ID as a string if no URL is present.

        Raises:
            JiraClientError: If the request fails.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}/attachments"
        form = aiohttp.FormData()
        form.add_field("file", content, filename=filename, content_type=content_type)

        async with session.post(
            url,
            data=form,
            headers={**self._headers, "X-Atlassian-Token": "no-check"},
        ) as rsp:
            if rsp.status not in (200, 201):
                text = await rsp.text()
                raise JiraClientError(
                    f"Failed to upload attachment to '{issue_key}': HTTP {rsp.status} — {text}"
                )
            attachments = await rsp.json()

        attachment = attachments[0]
        # "content" is the direct browser-accessible download URL.
        # "self" is the REST API URL which requires Bearer auth and gives 401 in a browser.
        result = attachment.get("content") or attachment.get("self") or str(attachment["id"])
        logger.debug("Uploaded attachment %r to %s: %s", filename, issue_key, result)
        return result

    async def add_comment(self, issue_key: str, body: str) -> str:
        """Post a plain-text comment on a Jira issue.

        Args:
            issue_key: Jira issue key, e.g. "GNI-1234".
            body:      Plain text comment body.

        Returns:
            The ID of the created comment as a string.

        Raises:
            JiraClientError: If the request fails.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/latest/issue/{issue_key}/comment"

        async with session.post(url, json={"body": body}) as rsp:
            if rsp.status not in (200, 201):
                text = await rsp.text()
                raise JiraClientError(
                    f"Failed to add comment to '{issue_key}': HTTP {rsp.status} — {text}"
                )
            data = await rsp.json()

        comment_id = str(data["id"])
        logger.debug("Added comment %s to %s", comment_id, issue_key)
        return comment_id


# Register this provider so get_ticketing_provider("jira") resolves here.
TICKETING_PROVIDERS["jira"] = JiraTicketingProvider
