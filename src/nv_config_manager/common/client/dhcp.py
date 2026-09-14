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
"""DHCP Service API client."""

from __future__ import annotations

import json
import ssl
import types
from collections.abc import Callable
from configparser import ConfigParser
from typing import Any, cast

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

from nv_config_manager.common.http_config import (
    get_internal_auth_headers,
    get_mtls_cert_paths,
    parse_verify_param,
)


class DHCPClientException(Exception):
    """Exception raised for errors in the DHCP client."""


class DHCPClient:
    """Async client for interacting with the DHCP API service."""

    def __init__(
        self,
        base_url: str,
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize the DHCP API client."""
        self.base_url = base_url.rstrip("/")
        self._verify = verify
        self._client_certificate = client_certificate
        self._headers = headers
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "dhcp",
    ) -> DHCPClient:
        """Create a DHCP client from INI configuration."""
        dhcp_config = config[section]
        use_internal = dhcp_config.getboolean("use_internal_endpoint", fallback=False)
        if use_internal:
            return cls(
                base_url=dhcp_config["api_service"],
                verify=False,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        return cls(
            base_url=dhcp_config["api_url"],
            verify=parse_verify_param(dhcp_config),
            client_certificate=get_mtls_cert_paths(config),
        )

    @classmethod
    def for_mcp(
        cls,
        base_url: str,
        headers: dict[str, str] | Callable[[], dict[str, str]],
        verify: bool | str = True,
    ) -> DHCPClient:
        """Create a DHCP client for MCP with explicit caller-scoped headers."""
        return cls(base_url=base_url, verify=verify, client_certificate=None, headers=headers)

    async def __aenter__(self) -> DHCPClient:
        """Async context manager entry."""
        self._session = aiohttp.ClientSession(
            connector=_connector(self._verify, self._client_certificate),
            timeout=ClientTimeout(total=30, connect=10),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        if self._session:
            await self._session.close()

    async def get_config(self, ip_version: int = 4) -> Any:
        """Get the sanitized running DHCP configuration."""
        return await self._request("GET", "/config", params={"ip_version": ip_version})

    def _resolve_headers(self) -> dict[str, str] | None:
        """Return headers for the current request/session."""
        headers = self._headers
        if isinstance(headers, dict) or headers is None:
            return cast(dict[str, str] | None, headers)
        return headers()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self._session:
            raise RuntimeError("DHCPClient must be used as async context manager")

        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                headers=self._resolve_headers(),
            ) as rsp:
                payload = await _response_payload(rsp)
                if not rsp.ok:
                    raise DHCPClientException(
                        f"{method} {path} failed with HTTP {rsp.status}: {payload}"
                    )
                return payload
        except aiohttp.ClientError as exc:
            raise DHCPClientException(f"{method} {path} failed: {exc}") from exc


async def _response_payload(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json()
    except (aiohttp.ContentTypeError, json.JSONDecodeError):
        return await response.text()


def _connector(
    verify: bool | str = True,
    client_certificate: tuple[str, str] | None = None,
) -> TCPConnector:
    ssl_context = ssl.create_default_context()
    if isinstance(verify, str):
        ssl_context.load_verify_locations(verify)
    elif verify is False:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    if client_certificate:
        ssl_context.load_cert_chain(client_certificate[0], client_certificate[1])

    return TCPConnector(ssl=ssl_context)
