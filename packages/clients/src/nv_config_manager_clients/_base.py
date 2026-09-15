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
"""Connection policy and result adaptation around generated async operations."""

from __future__ import annotations

import json
import ssl
from collections.abc import Awaitable, Callable
from types import SimpleNamespace, TracebackType
from typing import Any, Self, cast

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient

from nv_config_manager_clients._types import WhoamiResult

type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class ServiceClient:
    """Own generated-client resources without implementing service endpoints."""

    api_client_type: Any
    configuration_type: Any
    default_api_type: Any

    def __init__(
        self,
        base_url: str,
        *,
        verify: bool | str = True,
        client_certificate: tuple[str, str] | None = None,
        headers: HeaderProvider = None,
        attempts: int = 1,
        retry_statuses: set[int] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Configure TLS, headers, retries, and the generated connection pool."""
        self.base_url = base_url.rstrip("/")
        self._headers = headers
        self._verify = verify
        self._client_certificate = client_certificate
        self.timeout = timeout
        configuration = self.configuration_type(host=self.base_url)
        configuration.verify_ssl = verify is not False
        configuration.safe_chars_for_path_param = ""
        if isinstance(verify, str):
            configuration.ssl_ca_cert = verify
        if client_certificate:
            configuration.cert_file, configuration.key_file = client_certificate
        self.api_client = self.api_client_type(configuration)
        if client_certificate:
            # Preserve the original mTLS policy shared by Render, Temporal, and ZTP.
            self.api_client.rest_client.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.retry_options = ExponentialRetry(
            attempts=attempts,
            statuses=retry_statuses or set(),
            retry_all_server_errors=False,
            start_timeout=1.0,
            max_timeout=10.0,
        )
        self._closed = False

    def _resolve_headers(self) -> dict[str, str]:
        headers = self._headers
        if headers is None:
            return {}
        if isinstance(headers, dict):
            return cast(dict[str, str], headers).copy()
        return headers()

    async def _refresh_headers(
        self, session: aiohttp.ClientSession, context: SimpleNamespace, params: Any
    ) -> None:
        """Refresh credentials for every attempt, including retries."""
        params.headers.update(self._resolve_headers())

    def _ensure_transport(self) -> None:
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")
        rest = self.api_client.rest_client
        if rest.pool_manager is None:
            trace = aiohttp.TraceConfig()
            trace.on_request_start.append(self._refresh_headers)
            session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=rest.ssl_context),
                trace_configs=[trace],
            )
            # Generated REST code still owns serialization and issuing requests.
            # Supplying a retrying pool also preserves existing POST retry policy.
            rest.pool_manager = RetryClient(
                client_session=session, retry_options=self.retry_options
            )

    async def _call(self, operation: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
        self._ensure_transport()
        response = await operation(_request_timeout=self.timeout, **kwargs)
        try:
            data = await response.read()
            response.raise_for_status()
            if not data:
                return None
            try:
                return json.loads(data)
            except (ValueError, UnicodeDecodeError):
                return data.decode("utf-8", errors="replace")
        finally:
            response.release()

    async def whoami(self) -> WhoamiResult:
        """Return the authenticated identity using the generated service API."""
        api = self.default_api_type(self.api_client)
        return cast(WhoamiResult, await self._call(api.whoami_whoami_get_without_preload_content))

    async def close(self) -> None:
        """Close the generated client's connection pool."""
        await self.api_client.close()
        self._closed = True

    async def __aenter__(self) -> Self:
        """Enter an explicitly managed client lifetime."""
        self._ensure_transport()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release connections even when a request fails."""
        await self.close()
