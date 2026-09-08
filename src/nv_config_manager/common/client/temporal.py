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
"""Temporal Workflow Service Client."""

from __future__ import annotations

import json
import ssl
import types
from collections.abc import Callable
from configparser import ConfigParser
from typing import Any, cast

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

from nv_config_manager_workflows.clients._http import WhoamiResult
from nv_config_manager.common.log import LogCategory, get_logger


class TemporalClientException(Exception):
    """Exception raised for errors in the Temporal client."""


class TemporalClient:
    """Async client for interacting with the Temporal workflow service."""

    logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)

    def __init__(
        self,
        base_url: str,
        user_domain: str,
        client_certificate: tuple[str, str] | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize the Temporal client.

        Args:
            base_url: Base URL of the temporal service
            user_domain: User domain for email construction
            client_certificate: Tuple of (cert_file, key_file) for mTLS, or None for internal endpoints
            headers: Static dict or callable returning fresh headers per-request
        """
        self.base_url = base_url.rstrip("/")
        self.user_domain = user_domain
        self._client_certificate = client_certificate
        self._headers = headers
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "temporal",
        user_domain_section: str | None = None,
        user_domain_key: str = "user_domain",
    ) -> TemporalClient:
        """Create TemporalClient from INI configuration.

        Args:
            config: ConfigParser with temporal section
            section: Config section name for temporal settings
            user_domain_section: Section to get user_domain from (defaults to same as section)
            user_domain_key: Key for user_domain value

        Returns:
            Configured TemporalClient instance
        """
        from nv_config_manager.common.config import get_internal_auth_headers, get_mtls_cert_paths

        temporal_config = config[section]
        use_internal = temporal_config.getboolean("use_internal_endpoint", fallback=False)

        # Get user_domain from specified section or temporal section
        domain_section = user_domain_section or section
        if domain_section in config:
            user_domain = config[domain_section].get(user_domain_key, "nvidia.com")
        else:
            user_domain = "nvidia.com"

        if use_internal:
            return cls(
                base_url=temporal_config["api_service"],
                user_domain=user_domain,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            return cls(
                base_url=temporal_config["api_url"],
                user_domain=user_domain,
                client_certificate=get_mtls_cert_paths(config),
            )

    @classmethod
    def for_mcp(
        cls,
        base_url: str,
        headers: dict[str, str] | Callable[[], dict[str, str]],
        user_domain: str = "nvidia.com",
    ) -> TemporalClient:
        """Create a Temporal API client for MCP with explicit caller-scoped headers."""
        return cls(
            base_url=base_url,
            user_domain=user_domain,
            client_certificate=None,
            headers=headers,
        )

    async def __aenter__(self) -> TemporalClient:
        """Async context manager entry."""
        if self._client_certificate:
            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(self._client_certificate[0], self._client_certificate[1])
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
            connector = TCPConnector(ssl=ssl_context)
        else:
            connector = TCPConnector()

        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=30),
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

    async def whoami(self) -> WhoamiResult:
        """Probe identity as temporal-api sees this client.

        Exercises the same session + header resolution path as real
        workflow calls, so integration tests using this method can
        detect regressions in SPIFFE JWT injection.

        Returns:
            The JSON body of ``GET /whoami`` (``user`` and ``roles``).

        Raises:
            RuntimeError: If called outside an ``async with`` block.
            TemporalClientException: On transport or HTTP errors.
        """
        if not self._session:
            raise RuntimeError("TemporalClient must be used as async context manager")
        try:
            async with self._session.get(
                f"{self.base_url}/whoami",
                headers=self._resolve_headers(),
            ) as rsp:
                rsp.raise_for_status()
                data: WhoamiResult = await rsp.json()
                return data
        except aiohttp.ClientError as exc:
            raise TemporalClientException(f"Failed to fetch whoami: {exc}") from exc

    async def list_workflows(self, params: dict[str, Any] | None = None) -> Any:
        """List workflow executions from the Workflow API."""
        return await self._request("GET", "/v1/workflow", params=params)

    async def get_workflow(self, workflow_id: str) -> Any:
        """Get a workflow execution from the Workflow API."""
        return await self._request("GET", f"/v1/workflow/{workflow_id}")

    async def start_workflow(self, endpoint: str, payload: dict[str, Any]) -> Any:
        """Start a workflow through a dynamic Workflow API endpoint."""
        return await self._request("POST", f"/v1/workflow{endpoint}", json_body=payload)

    async def invoke_backup_workflow(self, device_id: str, user: str = "nv-config-manager") -> str:
        """Invoke the backup workflow for a device.

        Args:
            device_id: The device ID to backup
            user: The user triggering the workflow

        Returns:
            The workflow ID

        Raises:
            TemporalClientException: If the workflow invocation fails
        """
        if not self._session:
            raise RuntimeError("TemporalClient must be used as async context manager")

        try:
            payload = {
                "device_id": device_id,
                "trigger": "API",
                "user": user,
                "user_domain": self.user_domain,
                "workflow_id": None,
                "intended_config_commit_id": None,
            }
            async with self._session.post(
                f"{self.base_url}/v1/workflow/ngc/backup",
                json=payload,
                headers=self._resolve_headers(),
            ) as rsp:
                rsp.raise_for_status()
                result = await rsp.json()
                workflow_id: str = result["id"]
                self.logger.info(
                    "Backup workflow invoked for device %s: %s", device_id, workflow_id
                )
                return workflow_id
        except aiohttp.ClientError as exc:
            self.logger.exception(
                "Failed to invoke backup workflow for device %s: %s",
                device_id,
                str(exc),
            )
            raise TemporalClientException(f"Failed to invoke backup workflow: {exc}") from exc

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
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self._session:
            raise RuntimeError("TemporalClient must be used as async context manager")

        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=self._resolve_headers(),
            ) as rsp:
                payload = await _response_payload(rsp)
                if not rsp.ok:
                    raise TemporalClientException(
                        f"{method} {path} failed with HTTP {rsp.status}: {payload}"
                    )
                return payload
        except aiohttp.ClientError as exc:
            raise TemporalClientException(f"{method} {path} failed: {exc}") from exc


async def _response_payload(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json()
    except (aiohttp.ContentTypeError, json.JSONDecodeError):
        return await response.text()
