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
"""Convenience access to the generated DHCP API."""

from __future__ import annotations

from typing import Any, Self

import aiohttp

from nv_config_manager_clients._base import HeaderProvider, ServiceClient
from nv_config_manager_clients.generated.dhcp import ApiClient, Configuration
from nv_config_manager_clients.generated.dhcp.api.default_api import DefaultApi


class DHCPClientException(Exception):
    """An unsuccessful DHCP request."""


class DHCPClient(ServiceClient):
    """Async DHCP service client."""

    api_client_type = ApiClient
    configuration_type = Configuration
    default_api_type = DefaultApi

    def __init__(
        self,
        base_url: str,
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
        headers: HeaderProvider = None,
    ) -> None:
        super().__init__(
            base_url, verify=verify, client_certificate=client_certificate, headers=headers
        )
        self._api = DefaultApi(self.api_client)

    @classmethod
    def for_mcp(cls, base_url: str, headers: HeaderProvider, verify: bool | str = True) -> Self:
        """Create a caller-scoped DHCP client."""
        return cls(base_url, verify=verify, headers=headers)

    async def get_config(self, ip_version: int = 4) -> Any:
        """Return the sanitized running DHCP configuration."""
        try:
            return await self._call(
                self._api.get_config_config_get_without_preload_content, ip_version=ip_version
            )
        except aiohttp.ClientError as exc:
            raise DHCPClientException(f"Failed to get DHCP configuration: {exc}") from exc
