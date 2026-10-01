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
"""Behavioral tests for the reusable UFM client."""

import logging
from ssl import PROTOCOL_TLS_CLIENT, SSLContext
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from nv_config_manager_workflows.clients.ufm import (
    UFMAuthError,
    UFMClient,
    UFMClientError,
)

BASE_URL = "https://ufm.example.com/ufmRest"
PORTS_URL = f"{BASE_URL}/resources/ports"


def _client(
    passwords: list[str] | None = None,
    *,
    ssl: bool | SSLContext | aiohttp.Fingerprint = True,
    timeout_seconds: int = 30,
) -> UFMClient:
    return UFMClient(
        base_url=BASE_URL,
        username="ufm-user",
        passwords=["candidate-password"] if passwords is None else passwords,
        ssl=ssl,
        timeout_seconds=timeout_seconds,
    )


def test_client_constructs_from_explicit_settings_without_mutating_passwords() -> None:
    passwords = ["new-password", "old-password"]

    client = UFMClient(
        base_url=f"{BASE_URL}/",
        username="ufm-user",
        passwords=passwords,
        timeout_seconds=17,
    )
    passwords.reverse()

    assert client._base_url == BASE_URL
    assert client._host == "ufm.example.com"
    assert client._username == "ufm-user"
    assert client._passwords == ["new-password", "old-password"]
    assert client._timeout_seconds == 17
    assert client._ssl is True


def test_exception_names_inheritance_and_status_code_are_preserved() -> None:
    error = UFMClientError("failed", status_code=503)

    assert UFMAuthError.__name__ == "UFMAuthError"
    assert UFMClientError.__name__ == "UFMClientError"
    assert UFMAuthError.__bases__ == (Exception,)
    assert UFMClientError.__bases__ == (Exception,)
    assert error.status_code == 503


def test_cached_working_password_is_tried_first_without_duplication() -> None:
    client = _client(["new-password", "old-password"])
    client._working_password = "old-password"

    assert client._get_passwords_to_try() == ["old-password", "new-password"]


@pytest.mark.asyncio
async def test_session_uses_explicit_username_password_and_timeout() -> None:
    client = _client(timeout_seconds=19)
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


@pytest.mark.asyncio
async def test_successful_request_uses_base_path_tls_policy_and_caches_password() -> None:
    ssl_context = SSLContext(PROTOCOL_TLS_CLIENT)
    client = _client(ssl=ssl_context)

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload=[{"port": 1}])

        result = await client.request("GET", "/resources/ports")

    assert result == [{"port": 1}]
    assert client._working_password == "candidate-password"
    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["ssl"] is ssl_context


@pytest.mark.asyncio
async def test_request_tls_override_takes_precedence_over_client_policy() -> None:
    ssl_context = SSLContext(PROTOCOL_TLS_CLIENT)
    client = _client(ssl=ssl_context)

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload=[])

        await client.request("GET", "/resources/ports", ssl=False)

    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["ssl"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_request_tries_next_password_after_authentication_failure(status: int) -> None:
    client = _client(["new-password", "old-password"])

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, status=status)
        mocked.get(PORTS_URL, payload=[{"port": 1}])

        result = await client.request("GET", "/resources/ports")

    assert result == [{"port": 1}]
    assert client._working_password == "old-password"


@pytest.mark.asyncio
async def test_failed_cached_password_is_invalidated_before_retry() -> None:
    client = _client(["new-password", "old-password"])
    client._working_password = "old-password"

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, status=401)
        mocked.get(PORTS_URL, payload=[])

        await client.request("GET", "/resources/ports")

    assert client._working_password == "new-password"


@pytest.mark.asyncio
async def test_all_authentication_failures_raise_final_auth_error() -> None:
    client = _client(["new-password", "old-password"])

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, status=401)
        mocked.get(PORTS_URL, status=403)

        with pytest.raises(UFMAuthError, match="All 2 password attempts failed") as exc_info:
            await client.request("GET", "/resources/ports")

    assert isinstance(exc_info.value.__cause__, UFMAuthError)


@pytest.mark.asyncio
async def test_empty_password_list_raises_auth_error_without_request() -> None:
    client = _client([])

    with pytest.raises(UFMAuthError, match="No UFM passwords configured"):
        await client.request("GET", "/resources/ports")


@pytest.mark.asyncio
async def test_non_authentication_http_error_preserves_status_and_body() -> None:
    client = _client()

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, status=500, body="Internal Server Error")

        with pytest.raises(UFMClientError, match="HTTP 500 - Internal Server Error") as exc_info:
            await client.request("GET", "/resources/ports")

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_transport_error_is_classified_as_client_error() -> None:
    client = _client()
    transport_error = aiohttp.ClientConnectionError("connection refused")

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, exception=transport_error)

        with pytest.raises(UFMClientError, match="connection refused") as exc_info:
            await client.request("GET", "/resources/ports")

    assert exc_info.value.__cause__ is transport_error
    assert exc_info.value.status_code is None


@pytest.mark.asyncio
async def test_json_body_is_parsed_when_content_type_is_not_json() -> None:
    client = _client()

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, body='{"value": 1}', content_type="text/plain")

        result = await client.request("GET", "/resources/ports")

    assert result == {"value": 1}


@pytest.mark.asyncio
async def test_non_json_success_returns_empty_mapping_without_logging_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response_body = "sensitive-response-body"
    client = _client()

    with (
        caplog.at_level(logging.DEBUG, logger="nv_config_manager_workflows.clients.ufm"),
        aioresponses() as mocked,
    ):
        mocked.get(PORTS_URL, body=response_body, content_type="text/plain")

        result = await client.request("GET", "/resources/ports")

    assert result == {}
    assert response_body not in caplog.text


@pytest.mark.asyncio
async def test_get_ports_returns_healthy_and_unhealthy_ports_by_default() -> None:
    client = _client()
    ports_response = [
        {
            "number": "1",
            "physical_state": "Link Up",
            "logical_state": "Active",
            "guid": "0x1",
        },
        {
            "number": "2",
            "physical_state": "Link Down",
            "logical_state": "Inactive",
            "guid": "0x2",
        },
    ]

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload=ports_response)

        result = await client.get_ports()

    assert [port["port"] for port in result] == ["1", "2"]
    assert [port["guid"] for port in result] == ["0x1", "0x2"]


@pytest.mark.asyncio
async def test_get_ports_normalizes_fields_and_filters_unhealthy_ports() -> None:
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
            "physical_state": "Link Down",
            "logical_state": "Inactive",
            "system_name": "System1",
        },
    ]

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload=ports_response)

        result = await client.get_ports(unhealthy_only=True)

    assert result == [
        {
            "system_name": "System1",
            "port": "2",
            "label": "",
            "description": "",
            "physical_state": "Link Down",
            "logical_state": "Inactive",
            "peer_node_name": "",
            "peer_port": "",
            "peer_node_description": "",
            "guid": "",
        }
    ]


@pytest.mark.asyncio
async def test_get_ports_returns_empty_list_for_empty_response() -> None:
    client = _client()

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload=[])

        result = await client.get_ports()

    assert result == []


@pytest.mark.asyncio
async def test_get_ports_returns_empty_list_for_non_list_response() -> None:
    client = _client()

    with aioresponses() as mocked:
        mocked.get(PORTS_URL, payload={"error": "unexpected"})

        result = await client.get_ports()

    assert result == []


@pytest.mark.asyncio
async def test_context_manager_closes_retained_session() -> None:
    client = _client()
    session = MagicMock()
    session.close = AsyncMock()
    client._session = session

    async with client as entered:
        assert entered is client

    session.close.assert_awaited_once_with()
    assert client._session is None


@pytest.mark.asyncio
async def test_request_logs_do_not_contain_username_or_passwords(
    caplog: pytest.LogCaptureFixture,
) -> None:
    username = "credential-user-never-log"
    passwords = ["credential-new-never-log", "credential-old-never-log"]
    client = UFMClient(BASE_URL, username, passwords)

    with (
        caplog.at_level(logging.DEBUG, logger="nv_config_manager_workflows.clients.ufm"),
        aioresponses() as mocked,
    ):
        mocked.get(PORTS_URL, status=401)
        mocked.get(PORTS_URL, payload=[])

        await client.request("GET", "/resources/ports")

    assert username not in caplog.text
    assert all(password not in caplog.text for password in passwords)
