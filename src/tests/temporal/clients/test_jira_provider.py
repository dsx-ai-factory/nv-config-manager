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
"""Tests for JiraTicketingProvider.

Code under test:
  src/nv_config_manager/temporal/client/jira.py
    - JiraTicketingProvider.__init__             line 51
    - JiraTicketingProvider.validate_issue       line 97
    - JiraTicketingProvider.upload_attachment    line 126
    - JiraTicketingProvider.add_comment          line 170

HTTP responses are intercepted via aioresponses — no real connections are made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.jira import JiraClientError, JiraTicketingProvider


# =============================================================================
# Shared helpers
# =============================================================================

BASE_URL = "https://jira.example.com"
API_TOKEN = "secret-token"
ISSUE_KEY = "GNI-1234"

_ISSUE_URL = f"{BASE_URL}/rest/api/latest/issue/{ISSUE_KEY}"
_ATTACHMENTS_URL = f"{BASE_URL}/rest/api/latest/issue/{ISSUE_KEY}/attachments"
_COMMENT_URL = f"{BASE_URL}/rest/api/latest/issue/{ISSUE_KEY}/comment"


def _make_provider() -> JiraTicketingProvider:
    """Return a provider instance with known credentials."""
    return JiraTicketingProvider(base_url=BASE_URL, api_token=API_TOKEN)


# =============================================================================
# JiraTicketingProvider.__init__
# =============================================================================


def test_init_strips_trailing_slash():
    """base_url trailing slash is stripped to avoid double-slash in URLs."""
    provider = JiraTicketingProvider(base_url="https://jira.example.com/", api_token="tok")
    assert provider._base_url == "https://jira.example.com"


def test_init_sets_bearer_auth_header():
    """Authorization header uses Bearer scheme with the provided token."""
    provider = JiraTicketingProvider(base_url=BASE_URL, api_token="my-token")
    assert provider._headers["Authorization"] == "Bearer my-token"


def test_attachment_size_limit_is_ten_mebibytes():
    """The activity reports this limit and rejects larger bundles."""
    assert JiraTicketingProvider.max_attachment_size == 10 * 1024 * 1024


def test_from_config_resolves_jira_credentials():
    """The legacy constructor delegates configuration to the service adapter."""
    with patch(
        "nv_config_manager.temporal.client.jira.ticketing_client_settings",
        return_value={"base_url": BASE_URL, "api_token": API_TOKEN},
    ) as ticketing_client_settings:
        provider = JiraTicketingProvider.from_config()

    ticketing_client_settings.assert_called_once_with(platform="jira")
    assert provider._base_url == BASE_URL
    assert provider._headers["Authorization"] == f"Bearer {API_TOKEN}"


async def test_session_uses_current_timeout_contract():
    """Jira requests retain the current total and connection timeouts."""
    session = MagicMock()
    session.close = AsyncMock()

    with patch(
        "nv_config_manager_workflows.clients.ticketing.jira.aiohttp.ClientSession",
        return_value=session,
    ) as client_session:
        async with _make_provider() as provider:
            assert provider._session is session

    timeout = client_session.call_args.kwargs["timeout"]
    assert timeout.total == 60
    assert timeout.connect == 10
    assert client_session.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
    }
    session.close.assert_awaited_once_with()


# =============================================================================
# JiraTicketingProvider.validate_issue
# =============================================================================


async def test_validate_issue_returns_dict():
    """validate_issue returns a dict from the parsed JSON response."""
    issue_body = {"self": _ISSUE_URL, "fields": {}}
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.get(_ISSUE_URL, status=200, payload=issue_body)
            result = await provider.validate_issue(ISSUE_KEY)
    assert result == issue_body


async def test_validate_issue_calls_correct_url():
    """GET request is sent to /rest/api/latest/issue/{issue_key}."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.get(_ISSUE_URL, status=200, payload={"self": "", "fields": {}})
            await provider.validate_issue(ISSUE_KEY)
        # aioresponses raises ConnectionError for unmatched requests,
        # so reaching here confirms the correct URL was called.


async def test_validate_issue_raises_on_404():
    """JiraClientError is raised when the issue is not found (HTTP 404)."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.get(_ISSUE_URL, status=404)
            with pytest.raises(JiraClientError, match=ISSUE_KEY):
                await provider.validate_issue(ISSUE_KEY)


async def test_validate_issue_raises_on_500():
    """JiraClientError is raised for any non-200 status other than 404."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.get(_ISSUE_URL, status=500, body="internal error")
            with pytest.raises(JiraClientError, match="500"):
                await provider.validate_issue(ISSUE_KEY)


async def test_validate_issue_error_message_contains_issue_key_on_404():
    """404 error message names the issue key to aid debugging."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.get(_ISSUE_URL, status=404)
            with pytest.raises(JiraClientError) as exc_info:
                await provider.validate_issue(ISSUE_KEY)
    assert ISSUE_KEY in str(exc_info.value)


# =============================================================================
# JiraTicketingProvider.upload_attachment
# =============================================================================


async def test_upload_attachment_returns_content_url():
    """Returns the 'content' URL from the first attachment object in the response."""
    attachment_url = f"{BASE_URL}/secure/attachment/42/diag.txt"
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_ATTACHMENTS_URL, status=200, payload=[{"content": attachment_url, "id": "42"}])
            result = await provider.upload_attachment(ISSUE_KEY, "diag.txt", b"data", "text/plain")
    assert result == attachment_url


async def test_upload_attachment_falls_back_to_self_when_no_content():
    """Falls back to 'self' URL when 'content' is absent."""
    self_url = f"{BASE_URL}/rest/api/latest/attachment/42"
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_ATTACHMENTS_URL, status=200, payload=[{"self": self_url, "id": "42"}])
            result = await provider.upload_attachment(ISSUE_KEY, "diag.txt", b"data", "text/plain")
    assert result == self_url


async def test_upload_attachment_falls_back_to_id_when_no_self_or_content():
    """Falls back to str(attachment['id']) when both 'content' and 'self' are absent."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_ATTACHMENTS_URL, status=200, payload=[{"id": 99}])
            result = await provider.upload_attachment(ISSUE_KEY, "diag.txt", b"data", "text/plain")
    assert result == "99"


async def test_upload_attachment_posts_to_correct_url():
    """POST is sent to /rest/api/latest/issue/{issue_key}/attachments."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_ATTACHMENTS_URL, status=201, payload=[{"content": "http://x", "id": "1"}])
            await provider.upload_attachment(ISSUE_KEY, "diag.txt", b"data", "text/plain")
        # Reaching here without ConnectionError confirms the URL was matched.


async def test_upload_attachment_raises_on_error():
    """JiraClientError is raised when the upload response is not 200/201."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_ATTACHMENTS_URL, status=403, body="forbidden")
            with pytest.raises(JiraClientError, match="403"):
                await provider.upload_attachment(ISSUE_KEY, "diag.txt", b"data", "text/plain")


async def test_upload_attachment_accepts_201():
    """HTTP 201 Created is treated as success."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(
                _ATTACHMENTS_URL, status=201, payload=[{"content": "http://x/attach/5", "id": "5"}]
            )
            result = await provider.upload_attachment(ISSUE_KEY, "f.txt", b"x", "text/plain")
    assert result == "http://x/attach/5"


# =============================================================================
# JiraTicketingProvider.add_comment
# =============================================================================


async def test_add_comment_returns_comment_id():
    """Returns the comment ID as a string from the JSON response."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_COMMENT_URL, status=201, payload={"id": 77321})
            result = await provider.add_comment(ISSUE_KEY, "All done.")
    assert result == "77321"


async def test_add_comment_posts_to_correct_url():
    """POST is sent to /rest/api/latest/issue/{issue_key}/comment."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_COMMENT_URL, status=201, payload={"id": 1})
            await provider.add_comment(ISSUE_KEY, "comment")


async def test_add_comment_raises_on_error():
    """JiraClientError is raised when the response is not 200/201."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_COMMENT_URL, status=400, body="bad request")
            with pytest.raises(JiraClientError, match="400"):
                await provider.add_comment(ISSUE_KEY, "comment")


async def test_add_comment_accepts_200():
    """HTTP 200 OK is also treated as success (some Jira versions return 200)."""
    async with _make_provider() as provider:
        with aioresponses() as m:
            m.post(_COMMENT_URL, status=200, payload={"id": "9876"})
            result = await provider.add_comment(ISSUE_KEY, "ok")
    assert result == "9876"
