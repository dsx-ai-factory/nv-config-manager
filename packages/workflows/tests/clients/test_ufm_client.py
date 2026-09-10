# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for explicit UFM client construction."""

import logging
from ssl import PROTOCOL_TLS_CLIENT, SSLContext
from unittest.mock import AsyncMock, MagicMock, call, patch

import aiohttp
import pytest

from nv_config_manager_workflows.clients.ufm import (
    UFMAuthError,
    UFMClient,
    UFMClientError,
)

UFM_BASE_URL = "https://ufm.example.com/ufmRest"


def _client(
    passwords: list[str] | None = None,
    *,
    ssl: bool | SSLContext = False,
) -> UFMClient:
    return UFMClient(
        base_url=UFM_BASE_URL,
        username="ufm-user",
        passwords=passwords if passwords is not None else ["candidate-password"],
        ssl=ssl,
    )


def _session_response(
    *,
    status: int = 200,
    payload: object = None,
    body: str = "",
    content_type: str = "application/json",
) -> tuple[MagicMock, MagicMock]:
    response = MagicMock()
    response.status = status
    response.headers = {"Content-Type": content_type}
    response.json = AsyncMock(return_value={} if payload is None else payload)
    response.text = AsyncMock(return_value=body)
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    response_context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.request.return_value = response_context
    session.close = AsyncMock()
    return session, response


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


@pytest.mark.asyncio
async def test_successful_request_preserves_url_and_caches_working_password() -> None:
    client = _client()
    session, _ = _session_response(payload=[{"port": 1}])

    with patch.object(client, "_create_session", new=AsyncMock(return_value=session)):
        result = await client.request("GET", "/resources/ports")

    assert result == [{"port": 1}]
    assert client._working_password == "candidate-password"
    session.request.assert_called_once_with(
        "GET",
        f"{UFM_BASE_URL}/resources/ports",
        ssl=False,
    )
    session.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_retries_ordered_candidate_passwords(status: int) -> None:
    client = _client(["new-password", "old-password"])
    attempted_passwords: list[str] = []
    failed_session, _ = _session_response(status=status)
    successful_session, _ = _session_response(payload={"ok": True})
    sessions = iter((failed_session, successful_session))

    async def record_password(password: str) -> MagicMock:
        attempted_passwords.append(password)
        return next(sessions)

    with patch.object(client, "_create_session", side_effect=record_password):
        result = await client.request("GET", "/test")

    assert result == {"ok": True}
    assert attempted_passwords == ["new-password", "old-password"]
    assert client._working_password == "old-password"


@pytest.mark.asyncio
async def test_cached_password_is_prioritized_then_invalidated_after_failure() -> None:
    client = _client(["new-password", "old-password"])
    client._working_password = "old-password"
    attempted_passwords: list[str] = []
    failed_session, _ = _session_response(status=401)
    successful_session, _ = _session_response(payload={})
    sessions = iter((failed_session, successful_session))

    async def record_password(password: str) -> MagicMock:
        attempted_passwords.append(password)
        return next(sessions)

    with patch.object(client, "_create_session", side_effect=record_password):
        await client.request("GET", "/test")

    assert attempted_passwords == ["old-password", "new-password"]
    assert client._working_password == "new-password"


@pytest.mark.asyncio
async def test_all_auth_failures_raise_final_auth_error() -> None:
    client = _client(["new-password", "old-password"])
    unauthorized_session, _ = _session_response(status=401)
    forbidden_session, _ = _session_response(status=403)
    create_session = AsyncMock(side_effect=(unauthorized_session, forbidden_session))

    with patch.object(client, "_create_session", new=create_session):
        with pytest.raises(UFMAuthError) as raised:
            await client.request("GET", "/test")

    assert str(raised.value) == ("All 2 password attempts failed for UFM at ufm.example.com")
    assert isinstance(raised.value.__cause__, UFMAuthError)


@pytest.mark.asyncio
async def test_no_configured_passwords_raise_auth_error_without_request() -> None:
    client = _client([])

    with pytest.raises(UFMAuthError, match="No UFM passwords configured"):
        await client.request("GET", "/test")


@pytest.mark.asyncio
async def test_non_auth_http_error_preserves_classification_and_status() -> None:
    client = _client()
    session, _ = _session_response(status=500, body="Internal Server Error")

    with patch.object(client, "_create_session", new=AsyncMock(return_value=session)):
        with pytest.raises(UFMClientError) as raised:
            await client.request("GET", "/test")

    assert str(raised.value) == "UFM request failed: HTTP 500 - Internal Server Error"
    assert raised.value.status_code == 500


@pytest.mark.asyncio
async def test_aiohttp_client_error_is_wrapped() -> None:
    client = _client()
    session = MagicMock()
    session.request.side_effect = aiohttp.ClientConnectionError("connection refused")
    session.close = AsyncMock()

    with patch.object(client, "_create_session", new=AsyncMock(return_value=session)):
        with pytest.raises(UFMClientError) as raised:
            await client.request("GET", "/test")

    assert str(raised.value) == "UFM request failed: connection refused"
    assert isinstance(raised.value.__cause__, aiohttp.ClientConnectionError)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_ssl", [None, True])
async def test_request_preserves_method_kwargs_and_tls_policy(
    request_ssl: bool | None,
) -> None:
    ssl_context = SSLContext(PROTOCOL_TLS_CLIENT)
    client = _client(ssl=ssl_context)
    response = MagicMock()
    response.status = 200
    response.headers = {"Content-Type": "application/json"}
    response.json = AsyncMock(return_value={"ok": True})
    response_context = MagicMock()
    response_context.__aenter__ = AsyncMock(return_value=response)
    response_context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.request.return_value = response_context
    session.close = AsyncMock()
    kwargs: dict[str, object] = {"json": {"pkey": "0x1234"}}
    if request_ssl is not None:
        kwargs["ssl"] = request_ssl

    with patch.object(client, "_create_session", new=AsyncMock(return_value=session)):
        result = await client.request("POST", "/resources/pkeys", **kwargs)

    assert result == {"ok": True}
    session.request.assert_called_once_with(
        "POST",
        f"{UFM_BASE_URL}/resources/pkeys",
        json={"pkey": "0x1234"},
        ssl=ssl_context if request_ssl is None else request_ssl,
    )
    session.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_parse_response_uses_json_content_type() -> None:
    response = MagicMock()
    response.headers = {"Content-Type": "application/json; charset=utf-8"}
    response.json = AsyncMock(return_value=[{"port": 1}])

    assert await UFMClient._parse_response(response) == [{"port": 1}]
    response.json.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"status": "ok"}', {"status": "ok"}),
        ("plain text", {}),
    ],
)
async def test_parse_response_handles_json_like_and_non_json_text(
    body: str,
    expected: dict[str, str],
) -> None:
    response = MagicMock()
    response.status = 200
    response.headers = {"Content-Type": "text/plain"}
    response.text = AsyncMock(return_value=body)

    assert await UFMClient._parse_response(response) == expected


@pytest.mark.asyncio
async def test_get_ports_preserves_mapping_and_unhealthy_filter() -> None:
    client = _client()
    ports_response = [
        {
            "number": "1",
            "label": "Port 1",
            "physical_state": "Link Up",
            "logical_state": "Active",
            "system_name": "System1",
            "node_description": "Node 1",
            "peer_node_name": "Peer1",
            "peer_port_dname": "2",
            "peer_node_description": "Peer Node 1",
            "guid": "0x1",
        },
        {
            "number": "2",
            "label": "Port 2",
            "physical_state": "Link Down",
            "logical_state": "Inactive",
            "system_name": "System1",
            "guid": "0x2",
        },
    ]
    client.request = AsyncMock(return_value=ports_response)

    all_ports = await client.get_ports()
    unhealthy_ports = await client.get_ports(unhealthy_only=True)

    assert all_ports == [
        {
            "system_name": "System1",
            "port": "1",
            "label": "Port 1",
            "description": "Node 1",
            "physical_state": "Link Up",
            "logical_state": "Active",
            "peer_node_name": "Peer1",
            "peer_port": "2",
            "peer_node_description": "Peer Node 1",
            "guid": "0x1",
        },
        {
            "system_name": "System1",
            "port": "2",
            "label": "Port 2",
            "description": "",
            "physical_state": "Link Down",
            "logical_state": "Inactive",
            "peer_node_name": "",
            "peer_port": "",
            "peer_node_description": "",
            "guid": "0x2",
        },
    ]
    assert unhealthy_ports == [all_ports[1]]
    assert client.request.await_args_list == [
        call("GET", "/resources/ports"),
        call("GET", "/resources/ports"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, []])
async def test_get_ports_handles_empty_or_non_list_response(response: object) -> None:
    client = _client()
    client.request = AsyncMock(return_value=response)

    assert await client.get_ports() == []


@pytest.mark.asyncio
async def test_authentication_logs_exclude_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = UFMClient(
        base_url=UFM_BASE_URL,
        username="credential-user",
        passwords=["credential-new", "credential-old"],
    )
    unauthorized_session, _ = _session_response(status=401)
    forbidden_session, _ = _session_response(status=403)
    create_session = AsyncMock(side_effect=(unauthorized_session, forbidden_session))

    with (
        caplog.at_level(logging.DEBUG),
        patch.object(
            client,
            "_create_session",
            new=create_session,
        ),
    ):
        with pytest.raises(UFMAuthError):
            await client.request("GET", "/test")

    assert "credential-user" not in caplog.text
    assert "credential-new" not in caplog.text
    assert "credential-old" not in caplog.text
