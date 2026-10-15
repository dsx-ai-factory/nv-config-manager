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
"""Configuration-independent Jira Data Center and Cloud ticketing provider."""

from __future__ import annotations

import logging
import types
from typing import Any, NotRequired, Self, TypedDict, cast
from urllib.parse import urlsplit

import aiohttp
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.ticketing.base import (
    TicketingProvider,
    TicketingSettings,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class JiraSettings(TypedDict):
    """Explicit connection settings for :class:`JiraTicketingProvider`."""

    base_url: str
    api_token: str
    email: NotRequired[str]


class JiraClientError(Exception):
    """Raised when a Jira API request fails or returns an unexpected status."""


class JiraTicketingProvider(TicketingProvider):
    """Jira Data Center and Cloud implementation of :class:`TicketingProvider`."""

    max_attachment_size: int = 10 * 1024 * 1024

    def __init__(self, base_url: str, api_token: str, email: str = "") -> None:
        """Initialize the provider with explicit credentials."""
        self._base_url = base_url.rstrip("/")
        hostname = urlsplit(self._base_url).hostname or ""
        cloud = hostname.endswith(".atlassian.net") or hostname == "api.atlassian.com"
        # Cloud v2 supports the existing plain-text comment payload, unlike v3 (ADF).
        self._api_version = "2" if cloud or email else "latest"
        authorization = (
            aiohttp.BasicAuth(email, api_token).encode() if email else f"Bearer {api_token}"
        )
        self._headers = {
            "Authorization": authorization,
            "Accept": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_settings(cls, settings: TicketingSettings) -> Self:
        """Construct the provider from explicit service-owned settings."""
        return cls(
            base_url=cast("str", settings["base_url"]),
            api_token=cast("str", settings["api_token"]),
            email=cast("str", settings.get("email", "")),
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

    @staticmethod
    def _check_api_response(rsp: aiohttp.ClientResponse) -> None:
        """Reject login redirects and browser challenges before decoding API data."""
        if 300 <= rsp.status < 400 or rsp.headers.get("x-amzn-waf-action") == "challenge":
            raise JiraClientError(
                f"Jira API returned a login redirect or browser challenge (HTTP {rsp.status}). "
                "Configure the direct Jira API base_url and credentials for that endpoint. "
                "For Jira Cloud, use an Atlassian API token with email for Basic auth, "
                "or a service-account bearer token at api.atlassian.com/ex/jira/<cloud-id>."
            )

    async def validate_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch a Jira issue and return its metadata."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/{self._api_version}/issue/{issue_key}"
        async with session.get(url, allow_redirects=False) as rsp:
            self._check_api_response(rsp)
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
        """Upload a file as a direct attachment on a Jira issue."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/{self._api_version}/issue/{issue_key}/attachments"
        form = aiohttp.FormData()
        form.add_field("file", content, filename=filename, content_type=content_type)

        async with session.post(
            url,
            data=form,
            allow_redirects=False,
            headers={**self._headers, "X-Atlassian-Token": "no-check"},
        ) as rsp:
            self._check_api_response(rsp)
            if rsp.status not in (200, 201):
                text = await rsp.text()
                raise JiraClientError(
                    f"Failed to upload attachment to '{issue_key}': HTTP {rsp.status} — {text}"
                )
            attachments = await rsp.json()

        attachment = attachments[0]
        result = attachment.get("content") or attachment.get("self") or str(attachment["id"])
        logger.debug("Uploaded attachment %r to %s: %s", filename, issue_key, result)
        return result

    async def add_comment(self, issue_key: str, body: str) -> str:
        """Post a plain-text comment on a Jira issue."""
        session = await self._ensure_session()
        url = f"{self._base_url}/rest/api/{self._api_version}/issue/{issue_key}/comment"

        async with session.post(url, json={"body": body}, allow_redirects=False) as rsp:
            self._check_api_response(rsp)
            if rsp.status not in (200, 201):
                text = await rsp.text()
                raise JiraClientError(
                    f"Failed to add comment to '{issue_key}': HTTP {rsp.status} — {text}"
                )
            data = await rsp.json()

        comment_id = str(data["id"])
        logger.debug("Added comment %s to %s", comment_id, issue_key)
        return comment_id
