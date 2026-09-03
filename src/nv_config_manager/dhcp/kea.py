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
"""Local KEA Client for managing DHCP Configuration testing and reloads."""

from __future__ import annotations

import os
from configparser import ConfigParser
from enum import IntEnum
from typing import Any, Literal

import aiohttp

from nv_config_manager.common.config import load_config

# Per-container overrides for the Kea control endpoint. These let a container
# that runs in a *different* pod than Kea (e.g. the config-refresh deployment)
# target a specific Kea Service without changing the shared INI used by every
# other consumer. In particular the refresh process must reach Kea on pods that
# are not yet Ready (bootstrap/config-test), so it points these at the internal
# Kea bootstrap/validation Service (publishNotReadyAddresses) instead of the
# Ready-only internal Service.
KEA_SERVER_ENV = "NV_CONFIG_MANAGER_KEA_SERVER"
KEA_PORT_ENV = "NV_CONFIG_MANAGER_KEA_PORT"


class IpVersion(IntEnum):
    """Supported DHCP address families."""

    V4 = 4
    V6 = 6

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> dict[str, Any]:
        """Expose stable enum names to generated API clients."""
        schema: dict[str, Any] = handler(core_schema)
        schema["x-enum-varnames"] = [member.name for member in cls]
        return schema


class KeaException(Exception):
    """KEA Exception Class."""


class KeaClient:
    """Async KEA REST Client."""

    @staticmethod
    def from_config(config: ConfigParser | None = None, attached: bool = False) -> KeaClient:
        """Create a KEA client from the configured server and port.

        When ``attached`` is set, the client always targets the co-located Kea
        over localhost (same pod / shared network namespace).

        Otherwise the endpoint comes from ``[dhcp.kea]`` in the INI, but may be
        overridden per-container via the ``NV_CONFIG_MANAGER_KEA_SERVER`` /
        ``NV_CONFIG_MANAGER_KEA_PORT`` env vars. The config-refresh container
        uses this override to reach Kea through the internal bootstrap/validation
        Service so it can run ``config-test`` and seed Redis before pods are
        Ready, without weakening readiness for anyone else.
        """
        if config is None:
            config = load_config()
        if attached:
            host = "localhost"
            port = int(config["dhcp.kea"]["port"])
        else:
            host = os.environ.get(KEA_SERVER_ENV) or config["dhcp.kea"]["server"]
            port = int(os.environ.get(KEA_PORT_ENV) or config["dhcp.kea"]["port"])
        return KeaClient(host=host, port=port)

    def __init__(self, host: str | None = None, port: int = 8000) -> None:
        """Initialize a KEA REST Client."""
        self.url = f"http://{host}:{port}/"
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            connector = aiohttp.TCPConnector()
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> KeaClient:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object
    ) -> None:
        """Async context manager exit."""
        await self.close()

    async def status(self) -> Any:
        """Return the status of the KEA server."""
        data = {"command": "status-get"}
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                rsp.raise_for_status()
                return await rsp.json()
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def test_config(
        self, configuration: dict[str, Any], version: int = 4
    ) -> tuple[bool, str | None]:
        """Test if a proposed configuration is valid."""
        data = {
            "command": "config-test",
            "service": [f"dhcp{version}"],
            "arguments": configuration,
        }
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                result = await rsp.json()
                if result[0]["result"] != 0:
                    return False, result[0]["text"]
                return True, None
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def set_config(self, configuration: dict[str, Any], version: int = 4) -> str | None:
        """Set the KEA DHCP Configuration.

        Returns the SHA-256 hash of the effective configuration reported by KEA
        (Kea 2.4+ returns this digest from ``config-set``). ``None`` is returned
        when the running KEA server predates hash support and omits it.
        """
        session = await self._get_session()
        try:
            # Set configuration in memory
            data = {
                "command": "config-set",
                "service": [f"dhcp{version}"],
                "arguments": configuration,
            }
            async with session.post(self.url, json=data) as rsp:
                result = await rsp.json()
                if result[0]["result"] != 0:
                    raise KeaException(f"Failed to set configuration: {result[0]['text']}")
                config_hash: str | None = result[0].get("arguments", {}).get("hash")

            # Persist configuration to disk
            data = {
                "command": "config-write",
                "service": [f"dhcp{version}"],
                "arguments": {"filename": "/etc/kea/kea-dhcp4.conf"},
            }
            async with session.post(self.url, json=data) as rsp:
                result = await rsp.json()
                if result[0]["result"] != 0:
                    raise KeaException(
                        f"Failed to persist updated configuration to disk: {result[0]['text']}"
                    )

            return config_hash

        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def get_config_hash(self, version: int = 4) -> str | None:
        """Return the SHA-256 hash of the running KEA DHCP Configuration.

        Uses KEA's ``config-hash-get`` command (Kea 2.4+) to detect configuration
        drift cheaply, without comparing full ``config-get`` output (which
        contains KEA-generated defaults). ``None`` is returned when the running
        KEA server predates hash support and omits the digest.
        """
        data = {
            "command": "config-hash-get",
            "service": [f"dhcp{version}"],
        }
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                result = await rsp.json()
                if result[0]["result"] != 0:
                    raise KeaException(f"Failed to get configuration hash: {result[0]['text']}")
                arguments: dict[str, Any] = result[0].get("arguments", {})
                config_hash: str | None = arguments.get("hash")
                return config_hash
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def get_config(self, version: int = 4) -> list[dict[str, Any]]:
        """Return the running KEA DHCP Configuration."""
        session = await self._get_session()
        try:
            data = {
                "command": "config-get",
                "service": [f"dhcp{version}"],
            }
            async with session.post(self.url, json=data) as rsp:
                result: list[dict[str, Any]] = await rsp.json()
                return result

        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def _lease_command(
        self,
        operation: Literal["get", "del"],
        ip_address: str,
        version: IpVersion,
    ) -> list[dict[str, Any]]:
        """Run a lease command against the selected KEA service."""
        arguments: dict[str, Any] = {"ip-address": ip_address}
        if version == IpVersion.V6 and operation == "get":
            arguments["type"] = "IA_NA"
        data = {
            "command": f"lease{version}-{operation}",
            "service": [f"dhcp{version}"],
            "arguments": arguments,
        }
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                rsp.raise_for_status()
                result: list[dict[str, Any]] = await rsp.json()
                return result
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def get_lease(
        self,
        ip_address: str,
        version: IpVersion = IpVersion.V4,
    ) -> list[dict[str, Any]]:
        """Return one lease from the selected KEA service."""
        return await self._lease_command("get", ip_address, version)

    async def delete_lease(
        self,
        ip_address: str,
        version: IpVersion = IpVersion.V4,
    ) -> list[dict[str, Any]]:
        """Delete one lease from the selected KEA service."""
        return await self._lease_command("del", ip_address, version)

    async def get_lease_page(
        self,
        limit: int = 100,
        version: IpVersion = IpVersion.V4,
        from_address: str = "start",
    ) -> list[dict[str, Any]]:
        """Return a page of leases from the selected KEA service."""
        data = {
            "command": f"lease{version}-get-page",
            "service": [f"dhcp{version}"],
            "arguments": {"from": from_address, "limit": limit},
        }
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                rsp.raise_for_status()
                result: list[dict[str, Any]] = await rsp.json()
                return result
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc

    async def get_statistics(self, version: int = 4) -> list[dict[str, Any]]:
        """Return all statistics recorded by the KEA DHCP service."""
        data = {
            "command": "statistic-get-all",
            "service": [f"dhcp{version}"],
            "arguments": {},
        }
        session = await self._get_session()
        try:
            async with session.post(self.url, json=data) as rsp:
                rsp.raise_for_status()
                result: list[dict[str, Any]] = await rsp.json()
                return result
        except TimeoutError as exc:
            raise TimeoutError(
                "KEA Request timed out, are you running within a KEA Docker Container?"
            ) from exc
