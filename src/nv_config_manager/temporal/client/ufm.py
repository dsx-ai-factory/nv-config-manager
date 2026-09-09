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
"""UFM (Unified Fabric Manager) client for InfiniBand management.

Provides a client class for interacting with the UFM REST API with support for:
- Site-specific credential lookup from secrets config
- Password rotation with automatic retry on 401/403
- Configurable retry logic for transient failures
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.common.secrets import (
    get_rotation_passwords,
    resolve_credential_source,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


class UFMAuthError(Exception):
    """Raised when all UFM authentication attempts fail."""


class UFMClientError(Exception):
    """Raised for UFM client errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UFMClient:
    """Async client for UFM REST API with password rotation support.

    This client handles:
    - Site-specific credential lookup from secrets config
    - Password rotation with automatic retry on 401/403
    - Configurable retry logic for transient failures

    Credential lookup order:
    1. Secrets config: [site.{site_slug}] section (if site provided)
    2. Main config: [ufm] section (global fallback)

    Example usage:
        async with UFMClient(host="ufm.example.com", site="Site A") as client:
            ports = await client.get_ports()
            systems = await client.get("/systems")
    """

    def __init__(
        self,
        host: str,
        site: str | None = None,
        timeout_seconds: int = 30,
        max_passwords: int = 2,
    ) -> None:
        """Initialize UFM client.

        Args:
            host: UFM hostname or IP address
            site: Site name for site-specific credential lookup
            timeout_seconds: Request timeout in seconds
            max_passwords: Maximum number of rotation passwords to try
        """
        self._host = host
        self._site = site
        self._timeout_seconds = timeout_seconds
        self._max_passwords = max_passwords

        self._base_url = f"https://{host}/ufmRest"
        self._username: str = ""
        self._passwords: list[str] = []
        self._working_password: str | None = None
        self._session: aiohttp.ClientSession | None = None

        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load credentials from config with site-specific fallback.

        Lookup order (handled by resolve_credential_source):
        1. Secrets config: [site.{site_slug}] section (if site provided)
        2. Main config: [ufm] section (global fallback)
        """
        main_config = load_config()

        # Resolve config section - checks secrets config first, then main config
        config, section = resolve_credential_source(main_config, "ufm", self._site)

        # Get username from resolved section
        if config.has_section(section):
            self._username = config[section].get("ufm_api_user", "")

        # Get rotation passwords (up to max_passwords most recent)
        self._passwords = get_rotation_passwords(
            config, section, key_prefix="ufm_api_token_r", max_passwords=self._max_passwords
        )

        logger.debug(
            "Loaded UFM credentials for %s: username=%s, passwords=%d",
            self._host,
            self._username,
            len(self._passwords),
        )

    def _get_passwords_to_try(self) -> list[str]:
        """Get list of passwords to try, with cached working password first."""
        if self._working_password and self._working_password in self._passwords:
            # Put working password first, then the rest (deduplicated)
            return [self._working_password] + [
                p for p in self._passwords if p != self._working_password
            ]
        return list(self._passwords)

    async def _create_session(self, password: str) -> aiohttp.ClientSession:
        """Create a new session with the given password."""
        auth = aiohttp.BasicAuth(self._username, password)
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        return aiohttp.ClientSession(auth=auth, timeout=timeout)

    async def __aenter__(self) -> UFMClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - close session."""
        await self.close()

    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        """Make an authenticated request with password rotation on auth failure.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (e.g., "/resources/ports")
            **kwargs: Additional arguments passed to aiohttp request

        Returns:
            JSON response data

        Raises:
            UFMAuthError: If all password attempts fail with 401/403
            UFMClientError: For other HTTP errors
        """
        if not self._passwords:
            raise UFMAuthError("No UFM passwords configured")

        url = f"{self._base_url}{path}"
        passwords = self._get_passwords_to_try()
        last_error: Exception | None = None

        # Default SSL to False for self-signed certs
        if "ssl" not in kwargs:
            kwargs["ssl"] = False

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
                        # Invalidate cached password if it failed
                        if self._working_password == password:
                            self._working_password = None
                        continue

                    if response.status >= 400:
                        text = await response.text()
                        raise UFMClientError(
                            f"UFM request failed: HTTP {response.status} - {text}",
                            status_code=response.status,
                        )

                    # Success - cache this password
                    self._working_password = password
                    logger.debug("UFM request successful for %s (attempt %d)", self._host, idx)

                    return await self._parse_response(response)
            except aiohttp.ClientError as e:
                raise UFMClientError(f"UFM request failed: {e}") from e
            finally:
                await session.close()

        raise UFMAuthError(
            f"All {len(passwords)} password attempts failed for UFM at {self._host}"
        ) from last_error

    @staticmethod
    async def _parse_response(response: aiohttp.ClientResponse) -> dict[str, Any] | list[Any]:
        """Extract JSON body from a successful UFM response."""
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
