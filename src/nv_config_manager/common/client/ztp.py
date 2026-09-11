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
"""ZTP Service Client."""

from __future__ import annotations

import ssl
from collections.abc import Callable
from configparser import ConfigParser

from aiohttp import ClientTimeout, TCPConnector
from aiohttp_retry import ExponentialRetry

from nv_config_manager.common.http_config import get_internal_auth_headers, get_mtls_cert_paths
from nv_config_manager_workflows.clients._http import _WhoamiViaRetryClientMixin


class ZTPClientException(Exception):
    """Exception raised for errors in the ZTP client."""


class ZTPClient(_WhoamiViaRetryClientMixin):
    """Async client for interacting with the ZTP server."""

    def __init__(
        self,
        base_url: str,
        client_certificate: tuple[str, str] | None = None,
        headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        """Initialize the ZTP client.

        Args:
            base_url: Base URL of the ZTP service
            client_certificate: Tuple of (cert_file, key_file) for mTLS, or None for internal endpoints
            headers: Static dict or callable returning fresh headers per-request
        """
        self.base_url = base_url.rstrip("/")
        self._headers = headers

        if client_certificate:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.load_cert_chain(client_certificate[0], client_certificate[1])
            ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            self.connector = TCPConnector(ssl=ssl_ctx)
        else:
            self.connector = TCPConnector()

        self.timeout = ClientTimeout(total=30)
        self.retry_options = ExponentialRetry(
            attempts=3,
            start_timeout=1.0,
            max_timeout=5.0,
            factor=2.0,
            statuses={500, 502, 503, 504},
        )

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "ztp",
    ) -> ZTPClient:
        """Create ZTPClient from INI configuration.

        Args:
            config: ConfigParser with ztp section
            section: Config section name

        Returns:
            Configured ZTPClient instance
        """
        ztp_config = config[section]
        use_internal = ztp_config.getboolean("use_internal_endpoint", fallback=False)

        if use_internal:
            return cls(
                base_url=ztp_config["api_service"],
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            return cls(
                base_url=ztp_config["api_url"],
                client_certificate=get_mtls_cert_paths(config),
            )

    async def check_file_exists(self, file_path: str) -> bool:
        """Check if a file exists on the ZTP server.

        Args:
            file_path: The file path to check (e.g., "ytl-bundles/1.2.2/firmware.bin")

        Returns:
            True if the file exists, False otherwise
        """
        async with self._new_session() as session:
            try:
                async with session.head(f"{self.base_url}/v1/files/{file_path}") as response:
                    return response.status == 200
            except Exception:
                return False
