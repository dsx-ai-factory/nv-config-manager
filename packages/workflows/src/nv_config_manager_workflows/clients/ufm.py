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
"""Reusable UFM client with explicit connection and credential settings."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from ssl import SSLContext
from typing import Any
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger(__name__)

type UFMSSL = bool | SSLContext | aiohttp.Fingerprint


class UFMAuthError(Exception):
    """Raised when all UFM authentication attempts fail."""


class UFMClientError(Exception):
    """Raised for UFM client errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UFMClient:
    """Asynchronous UFM REST client with password-rotation support."""

    def __init__(
        self,
        base_url: str,
        username: str,
        passwords: Sequence[str],
        *,
        ssl: UFMSSL = False,
        timeout_seconds: int = 30,
    ) -> None:
        """Initialize the client from explicit settings.

        Args:
            base_url: Complete UFM REST base URL, such as
                ``https://ufm.example.com/ufmRest``.
            username: Username used for HTTP basic authentication.
            passwords: Ordered candidate passwords to try.
            ssl: aiohttp TLS policy used when a request does not override it.
            timeout_seconds: Total timeout for each password attempt.
        """
        self._base_url = base_url.rstrip("/")
        parsed_url = urlsplit(self._base_url)
        self._host = parsed_url.netloc or self._base_url
        self._username = username
        self._passwords = list(passwords)
        self._ssl = ssl
        self._timeout_seconds = timeout_seconds
        self._working_password: str | None = None
        self._session: aiohttp.ClientSession | None = None

    def _get_passwords_to_try(self) -> list[str]:
        """Return candidate passwords with the cached working password first."""
        if self._working_password and self._working_password in self._passwords:
            return [self._working_password] + [
                password for password in self._passwords if password != self._working_password
            ]
        return list(self._passwords)

    async def _create_session(self, password: str) -> aiohttp.ClientSession:
        """Create a session for one password attempt."""
        auth = aiohttp.BasicAuth(self._username, password)
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        return aiohttp.ClientSession(auth=auth, timeout=timeout)

    async def __aenter__(self) -> UFMClient:
        """Enter the asynchronous context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the asynchronous context manager and close any retained session."""
        await self.close()

    async def close(self) -> None:
        """Close a retained client session, if present."""
        if self._session:
            await self._session.close()
            self._session = None

    async def request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        """Make an authenticated request, rotating passwords after 401 or 403 responses."""
        if not self._passwords:
            raise UFMAuthError("No UFM passwords configured")

        url = f"{self._base_url}{path}"
        passwords = self._get_passwords_to_try()
        last_error: Exception | None = None
        kwargs.setdefault("ssl", self._ssl)

        for idx, password in enumerate(passwords, 1):
            logger.debug("UFM request to %s %s (attempt %d/%d)", method, url, idx, len(passwords))

            session = await self._create_session(password)
            try:
                async with session.request(method, url, **kwargs) as response:
                    if response.status in (401, 403):
                        logger.warning(
                            "UFM authentication failed for %s (attempt %d/%d): HTTP %d",
                            self._host,
                            idx,
                            len(passwords),
                            response.status,
                        )
                        last_error = UFMAuthError(f"Authentication failed: HTTP {response.status}")
                        if self._working_password == password:
                            self._working_password = None
                        continue

                    if response.status >= 400:
                        text = await response.text()
                        raise UFMClientError(
                            f"UFM request failed: HTTP {response.status} - {text}",
                            status_code=response.status,
                        )

                    self._working_password = password
                    logger.debug("UFM request successful for %s (attempt %d)", self._host, idx)
                    return await self._parse_response(response)
            except aiohttp.ClientError as error:
                raise UFMClientError(f"UFM request failed: {error}") from error
            finally:
                await session.close()

        raise UFMAuthError(
            f"All {len(passwords)} password attempts failed for UFM at {self._host}"
        ) from last_error

    @staticmethod
    async def _parse_response(response: aiohttp.ClientResponse) -> dict[str, Any] | list[Any]:
        """Extract a JSON-compatible value from a successful response."""
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            result: dict[str, Any] | list[Any] = await response.json()
            return result

        text = await response.text()
        try:
            parsed: dict[str, Any] | list[Any] = json.loads(text)
            return parsed
        except ValueError:
            logger.debug(
                "UFM returned non-JSON response (status=%d, content_type=%s): %s",
                response.status,
                content_type,
                text[:200],
            )
            return {}

    async def get_ports(self, unhealthy_only: bool = False) -> list[dict[str, Any]]:
        """Get InfiniBand ports from UFM.

        Args:
            unhealthy_only: If True, only return unhealthy ports
                (physical_state != "link up" or logical_state != "active")

        Returns:
            List of port dictionaries. Each dictionary contains:

            - system_name (str): UFM system name hosting the port
            - port (str): Port number on the system
            - label (str): UFM-assigned port label
            - description (str): Node description from UFM
            - physical_state (str): Physical link state (e.g. "link up")
            - logical_state (str): Logical link state (e.g. "active")
            - peer_node_name (str): Connected peer node name
            - peer_port (str): Connected peer port dname
            - peer_node_description (str): Connected peer node description
            - guid (str): Port GUID

            All values are strings; missing fields in the UFM response
            are returned as empty strings.
        """
        data = await self.request("GET", "/resources/ports")

        if not isinstance(data, list):
            return []

        ports = []
        for port in data:
            port_data = {
                "system_name": port.get("system_name", ""),
                "port": port.get("number", ""),
                "label": port.get("label", ""),
                "description": port.get("node_description", ""),
                "physical_state": port.get("physical_state", ""),
                "logical_state": port.get("logical_state", ""),
                "peer_node_name": port.get("peer_node_name", ""),
                "peer_port": port.get("peer_port_dname", ""),
                "peer_node_description": port.get("peer_node_description", ""),
                "guid": port.get("guid", ""),
            }

            if unhealthy_only:
                physical_state = port.get("physical_state", "").lower()
                logical_state = port.get("logical_state", "").lower()
                if physical_state != "link up" or logical_state != "active":
                    ports.append(port_data)
            else:
                ports.append(port_data)

        return ports
