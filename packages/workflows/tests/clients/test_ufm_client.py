# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for explicit UFM client construction."""

import logging
from ssl import PROTOCOL_TLS_CLIENT, SSLContext
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager_workflows.clients.ufm import (
    UFMAuthError,
    UFMClient,
    UFMClientError,
)


def test_client_constructs_from_explicit_settings_without_mutating_passwords() -> None:
    passwords = ["new-password", "old-password"]

    client = UFMClient(
        base_url="https://ufm.example.com/ufmRest/",
        username="ufm-user",
        passwords=passwords,
        timeout_seconds=17,
    )
    passwords.reverse()

    assert client._base_url == "https://ufm.example.com/ufmRest"
    assert client._host == "ufm.example.com"
    assert client._username == "ufm-user"
    assert client._passwords == ["new-password", "old-password"]
    assert client._timeout_seconds == 17
    assert client._ssl is False


def test_cached_working_password_is_tried_first_without_duplication() -> None:
    client = UFMClient(
        base_url="https://ufm.example.com/ufmRest",
        username="ufm-user",
        passwords=["new-password", "old-password"],
    )
    client._working_password = "old-password"

    assert client._get_passwords_to_try() == ["old-password", "new-password"]


@pytest.mark.asyncio
async def test_session_uses_explicit_username_password_and_timeout() -> None:
    ssl_context = SSLContext(PROTOCOL_TLS_CLIENT)
    client = UFMClient(
        base_url="https://ufm.example.com/ufmRest",
        username="ufm-user",
        passwords=["candidate-password"],
        ssl=ssl_context,
        timeout_seconds=19,
    )
    session = MagicMock()

    with patch(
        "nv_config_manager_workflows.clients.ufm.aiohttp.ClientSession",
        return_value=session,
    ) as create_session:
        result = await client._create_session("candidate-password")

    assert result is session
    auth = create_session.call_args.kwargs["auth"]
    timeout = create_session.call_args.kwargs["timeout"]
    assert auth.login == "ufm-user"
    assert auth.password == "candidate-password"
    assert timeout.total == 19
    assert client._ssl is ssl_context


def test_exception_names_and_inheritance_are_preserved() -> None:
    error = UFMClientError("failed", status_code=500)

    assert UFMAuthError.__name__ == "UFMAuthError"
    assert UFMClientError.__name__ == "UFMClientError"
    assert issubclass(UFMAuthError, Exception)
    assert issubclass(UFMClientError, Exception)
    assert error.status_code == 500


def test_constructor_does_not_log_credentials(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        UFMClient(
            base_url="https://ufm.example.com/ufmRest",
            username="credential-user",
            passwords=["credential-password"],
        )

    assert "credential-user" not in caplog.text
    assert "credential-password" not in caplog.text
