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
"""Tests for the configuration-independent Jira ticketing provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager_workflows.clients import (
    JiraClientError,
    JiraTicketingProvider,
)

BASE_URL = "https://jira.example.com"
API_TOKEN = "secret-token"
ISSUE_KEY = "GNI-1234"

ISSUE_URL = f"{BASE_URL}/rest/api/latest/issue/{ISSUE_KEY}"
ATTACHMENTS_URL = f"{ISSUE_URL}/attachments"
COMMENT_URL = f"{ISSUE_URL}/comment"


def _provider() -> JiraTicketingProvider:
    return JiraTicketingProvider(base_url=BASE_URL, api_token=API_TOKEN)


def _session_response(
    *,
    status: int,
    payload: object = None,
    body: str = "",
) -> tuple[MagicMock, MagicMock]:
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=payload)
    response.text = AsyncMock(return_value=body)
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    response_context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get.return_value = response_context
    session.post.return_value = response_context
    return session, response


def test_provider_constructs_from_explicit_settings() -> None:
    provider = JiraTicketingProvider.from_settings(
        {"base_url": f"{BASE_URL}/", "api_token": API_TOKEN}
    )

    assert provider._base_url == BASE_URL
    assert provider._headers == {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
    }


def test_attachment_size_limit_is_ten_mebibytes() -> None:
    assert JiraTicketingProvider.max_attachment_size == 10 * 1024 * 1024


async def test_session_preserves_headers_timeouts_and_cleanup() -> None:
    session = MagicMock()
    session.close = AsyncMock()

    with patch(
        "nv_config_manager_workflows.clients.ticketing.jira.aiohttp.ClientSession",
        return_value=session,
    ) as client_session:
        async with _provider() as provider:
            assert provider._session is session

    assert client_session.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
    }
    timeout = client_session.call_args.kwargs["timeout"]
    assert timeout.total == 60
    assert timeout.connect == 10
    session.close.assert_awaited_once_with()
    assert provider._session is None


async def test_validate_issue_returns_jira_metadata() -> None:
    payload = {"self": ISSUE_URL, "fields": {"summary": "Example"}}
    session, _ = _session_response(status=200, payload=payload)
    provider = _provider()

    with patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)):
        result = await provider.validate_issue(ISSUE_KEY)

    assert result == payload
    session.get.assert_called_once_with(ISSUE_URL)


async def test_validate_issue_reports_missing_issue() -> None:
    session, _ = _session_response(status=404)
    provider = _provider()

    with (
        patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)),
        pytest.raises(JiraClientError, match=ISSUE_KEY),
    ):
        await provider.validate_issue(ISSUE_KEY)


async def test_validate_issue_reports_other_http_errors() -> None:
    session, response = _session_response(status=500, body="internal error")
    provider = _provider()

    with (
        patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)),
        pytest.raises(JiraClientError, match="500.*internal error"),
    ):
        await provider.validate_issue(ISSUE_KEY)

    response.text.assert_awaited_once_with()


@pytest.mark.parametrize("status", [200, 201])
async def test_upload_attachment_accepts_success_statuses(status: int) -> None:
    attachment_url = f"{BASE_URL}/secure/attachment/42/diagnostics.txt"
    session, _ = _session_response(
        status=status,
        payload=[{"content": attachment_url, "id": "42"}],
    )
    provider = _provider()

    with patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)):
        result = await provider.upload_attachment(
            ISSUE_KEY,
            "diagnostics.txt",
            b"diagnostics",
            "text/plain",
        )

    assert result == attachment_url
    assert session.post.call_args.args == (ATTACHMENTS_URL,)
    assert session.post.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
        "X-Atlassian-Token": "no-check",
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"self": f"{BASE_URL}/rest/api/latest/attachment/42", "id": "42"},
            f"{BASE_URL}/rest/api/latest/attachment/42",
        ),
        ({"id": 42}, "42"),
    ],
)
async def test_upload_attachment_fallback_identifiers(
    payload: dict[str, object],
    expected: str,
) -> None:
    session, _ = _session_response(status=200, payload=[payload])
    provider = _provider()

    with patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)):
        result = await provider.upload_attachment(
            ISSUE_KEY,
            "diagnostics.txt",
            b"diagnostics",
            "text/plain",
        )

    assert result == expected


async def test_upload_attachment_reports_http_errors() -> None:
    session, response = _session_response(status=403, body="forbidden")
    provider = _provider()

    with (
        patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)),
        pytest.raises(JiraClientError, match="403.*forbidden"),
    ):
        await provider.upload_attachment(
            ISSUE_KEY,
            "diagnostics.txt",
            b"diagnostics",
            "text/plain",
        )

    response.text.assert_awaited_once_with()


@pytest.mark.parametrize("status", [200, 201])
async def test_add_comment_accepts_success_statuses(status: int) -> None:
    session, _ = _session_response(status=status, payload={"id": 77321})
    provider = _provider()

    with patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)):
        result = await provider.add_comment(ISSUE_KEY, "Diagnostics complete.")

    assert result == "77321"
    session.post.assert_called_once_with(
        COMMENT_URL,
        json={"body": "Diagnostics complete."},
    )


async def test_add_comment_reports_http_errors() -> None:
    session, response = _session_response(status=400, body="bad request")
    provider = _provider()

    with (
        patch.object(provider, "_ensure_session", new=AsyncMock(return_value=session)),
        pytest.raises(JiraClientError, match="400.*bad request"),
    ):
        await provider.add_comment(ISSUE_KEY, "Diagnostics complete.")

    response.text.assert_awaited_once_with()
