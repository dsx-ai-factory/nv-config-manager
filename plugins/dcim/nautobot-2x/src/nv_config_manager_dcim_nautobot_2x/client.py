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
"""Nautobot HTTP transport owned by the Nautobot DCIM provider."""

from __future__ import annotations

import logging
import re
import ssl
import types
from collections.abc import Callable
from typing import Any, Self, cast

import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from nv_config_manager_dcim.errors import DCIMError

logger = logging.getLogger(__name__)

_SAFE_REST_PATH = re.compile(r"[A-Za-z0-9._~/-]+")


class NautobotException(DCIMError):
    """Backend-specific compatibility name for a public DCIM failure."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NautobotClient:
    """Async Nautobot HTTP client used only by this provider.

    Provider operations subclass this transport with Nautobot-specific
    rendering, DHCP, and workflow operations.

    Usage:
        async with NautobotClient.from_config(config) as client:
            result = await client.graphql_query(query, variables)
    """

    def __init__(
        self,
        nautobot_url: str,
        token: str = "",
        verify: bool | str = True,
        timeout: int = 30,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize the Nautobot client.

        Args:
            nautobot_url: Base URL for Nautobot instance
            token: API token for authentication
            verify: SSL verification - True (default), False (disable), or str (path to CA cert)
            timeout: Default request timeout in seconds
            headers: Static dict or callable returning fresh headers per-request.
                If set, these headers take precedence over token auth.
        """
        self.nautobot_url = nautobot_url.rstrip("/") + "/"
        self.token = token
        self._verify = verify
        self._timeout = timeout
        self._headers = headers
        self.graphql_endpoint = f"{self.nautobot_url}api/graphql/"
        self.rest_endpoint = f"{self.nautobot_url}api/"
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def for_mcp(
        cls,
        nautobot_url: str,
        headers: dict[str, str] | Callable[[], dict[str, str]],
        verify: bool | str = True,
        timeout: int = 30,
    ) -> Self:
        """Create a Nautobot client for MCP with explicit caller-scoped headers."""
        return cls(
            nautobot_url=nautobot_url,
            verify=verify,
            timeout=timeout,
            headers=headers,
        )

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self._ensure_session()
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
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session exists, creating it lazily if needed."""
        if not self._session:
            ssl_context = ssl.create_default_context()

            if isinstance(self._verify, str):
                # Use custom CA certificate for verification
                ssl_context.load_verify_locations(self._verify)
            elif self._verify is False:
                # Disable SSL verification
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            connector = TCPConnector(ssl=ssl_context)
            timeout = ClientTimeout(total=self._timeout, connect=10)

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
        return self._session

    def _resolve_headers(self) -> dict[str, str] | None:
        """Return auth headers for the current request/session."""
        headers = self._headers
        if isinstance(headers, dict):
            return cast(dict[str, str], headers)
        if headers is not None:
            return headers()
        if self.token:
            return {"Authorization": f"Token {self.token}"}
        return None

    def _rest_url(self, path: str) -> str:
        """Return a URL for a safe path relative to the Nautobot REST API root."""
        path_parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or _SAFE_REST_PATH.fullmatch(path) is None
            or any(part in {".", ".."} for part in path_parts)
            or any(not part for part in path_parts[:-1])
        ):
            raise ValueError("Nautobot API path must be a safe relative path")
        return f"{self.rest_endpoint}{path}"

    async def close(self) -> None:
        """Close the HTTP client session.

        Should be called when the client is no longer needed if not
        using the async context manager.
        """
        if self._session:
            await self._session.close()
            self._session = None

    async def _handle_error_response(
        self, rsp: aiohttp.ClientResponse, method: str, path: str
    ) -> None:
        """Handle error responses by logging and raising with response body.

        Args:
            rsp: The aiohttp ClientResponse object
            method: HTTP method used (GET, POST, PATCH, DELETE)
            path: API path that was requested

        Raises:
            NautobotException: Always raised with status and response body details
        """
        # Try to get the response body for error details
        try:
            error_body = await rsp.json()
        except Exception:
            # If JSON parsing fails, try to get text
            try:
                error_body = await rsp.text()
            except Exception:
                error_body = "<unable to read response body>"

        raise NautobotException(
            f"Nautobot API error: {method} {path} returned {rsp.status}: {error_body}",
            status_code=rsp.status,
        )

    async def graphql_query(
        self, query: str, variables: dict[str, Any] | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string
            variables: Query variables
            timeout: Request timeout in seconds (uses default if None)

        Returns:
            Query response data

        Raises:
            NautobotException: If the query fails or returns errors
        """
        session = await self._ensure_session()
        payload = {"query": query, "variables": variables or {}}
        operation_name = getattr(query, "operation_name", None)
        if operation_name is not None:
            payload["operationName"] = operation_name

        logger.debug("Executing GraphQL query")

        request_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with session.post(
            self.graphql_endpoint,
            json=payload,
            timeout=request_timeout,
            headers=self._resolve_headers(),
        ) as rsp:
            if rsp.status == 400:
                data = await rsp.json()
                raise NautobotException(f"GraphQL error: {data.get('errors', data)}")
            rsp.raise_for_status()
            result = await rsp.json()

            if "errors" in result:
                raise NautobotException(f"GraphQL errors: {result['errors']}")

            return cast(dict[str, Any], result)

    async def get(
        self, path: str, params: dict[str, Any] | None = None, timeout: int | None = None
    ) -> Any:
        """Send an HTTP GET request.

        Args:
            path: API path (relative to /api/)
            params: Query parameters
            timeout: Request timeout in seconds

        Returns:
            Response JSON data
        """
        request_url = self._rest_url(path)
        session = await self._ensure_session()
        request_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with session.get(
            request_url,
            params=params,
            timeout=request_timeout,
            headers=self._resolve_headers(),
        ) as rsp:
            if not rsp.ok:
                await self._handle_error_response(rsp, "GET", path)
            return await rsp.json()

    async def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 250,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return every page of a paginated Nautobot list endpoint."""
        collected: list[dict[str, Any]] = []
        query = dict(params or {})
        query["limit"] = page_size
        offset = 0
        while True:
            query["offset"] = offset
            page = await self.get(path, params=query, timeout=timeout)
            results = page.get("results", [])
            collected.extend(results)
            if not results or not page.get("next"):
                break
            offset += len(results)
        return collected

    async def post(self, path: str, data: Any, timeout: int | None = None) -> Any:
        """Send an HTTP POST request.

        Args:
            path: API path (relative to /api/)
            data: Request body data
            timeout: Request timeout in seconds

        Returns:
            Response JSON data
        """
        request_url = self._rest_url(path)
        session = await self._ensure_session()
        request_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with session.post(
            request_url,
            json=data,
            timeout=request_timeout,
            headers=self._resolve_headers(),
        ) as rsp:
            if not rsp.ok:
                await self._handle_error_response(rsp, "POST", path)
            return await rsp.json()

    async def patch(self, path: str, data: Any, timeout: int | None = None) -> Any:
        """Send an HTTP PATCH request.

        Args:
            path: API path (relative to /api/)
            data: Request body data
            timeout: Request timeout in seconds

        Returns:
            Response JSON data
        """
        request_url = self._rest_url(path)
        session = await self._ensure_session()
        request_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with session.patch(
            request_url,
            json=data,
            timeout=request_timeout,
            headers=self._resolve_headers(),
        ) as rsp:
            if not rsp.ok:
                await self._handle_error_response(rsp, "PATCH", path)
            return await rsp.json()

    async def delete(self, path: str, timeout: int | None = None) -> None:
        """Send an HTTP DELETE request.

        Args:
            path: API path (relative to /api/)
            timeout: Request timeout in seconds
        """
        request_url = self._rest_url(path)
        session = await self._ensure_session()
        request_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with session.delete(
            request_url,
            timeout=request_timeout,
            headers=self._resolve_headers(),
        ) as rsp:
            if not rsp.ok:
                await self._handle_error_response(rsp, "DELETE", path)

    async def installed_plugins(self) -> dict[str, str]:
        """Get plugins installed in the current Nautobot environment."""
        data = await self.get("status/")
        return cast(dict[str, str], data.get("plugins", {}))

    def get_device_ui_url(self, device_id: str) -> str:
        """Get the Device UI URL for a given Device UUID."""
        return f"{self.nautobot_url}dcim/devices/{device_id}"
