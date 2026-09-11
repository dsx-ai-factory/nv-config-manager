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
"""Shared mixins for reusable NVIDIA Config Manager HTTP clients.

Lives in its own module (rather than ``__init__.py``) so the per-client
modules can import from here without circular-import gymnastics — the
classes here only depend on ``aiohttp`` / ``aiohttp_retry``, never on
the nv-config-manager client classes themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict, cast

from aiohttp import ClientTimeout, TCPConnector
from aiohttp_retry import ExponentialRetry, RetryClient


class WhoamiResult(TypedDict):
    """Body returned by ``GET /whoami`` on nv-config-manager FastAPI services.

    Mirrors :class:`nv_config_manager.common.auth.WhoamiResponse` (the server-side Pydantic
    model) but kept as a separate :class:`typing.TypedDict` here so the client
    package doesn't pull FastAPI / Pydantic into its dependency surface just
    to type a two-field response.
    """

    user: str
    roles: list[str]


type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class _WhoamiViaRetryClientMixin:
    """Adds an async ``whoami()`` method using the per-call ``RetryClient`` pattern.

    Used by the Config Store, Render, and service-owned ZTP clients so their
    identity-probe paths stay in lock-step. :class:`TemporalClient` uses a
    persistent :class:`aiohttp.ClientSession` and does not inherit from this
    mixin — its session shape needs to keep matching every other method on
    the temporal client.

    Host classes must expose ``base_url``, ``connector``, ``timeout``,
    ``retry_options``, and ``_headers`` (a dict, callable returning a dict,
    or ``None``). ``base_url`` is always the service root. ``_headers`` is
    resolved per call so a callable header source is invoked fresh — important
    for SPIFFE JWT-SVIDs which rotate on disk.
    """

    def __init__(
        self,
        *,
        base_url: str,
        connector: TCPConnector,
        timeout: ClientTimeout,
        retry_options: ExponentialRetry,
        headers: HeaderProvider,
    ) -> None:
        """Initialize request resources supplied by the concrete client."""
        self.base_url: str = base_url
        self.connector: TCPConnector = connector
        self.timeout: ClientTimeout = timeout
        self.retry_options: ExponentialRetry = retry_options
        self._headers: HeaderProvider = headers

    def _resolve_headers(self) -> dict[str, str] | None:
        """Return headers for the current request."""
        if callable(self._headers):
            return self._headers()  # type: ignore[ty:call-top-callable]  # ty cannot narrow this union
        return self._headers

    def _new_session(self) -> RetryClient:
        """Create a RetryClient for one request without owning the shared connector."""
        return RetryClient(
            connector=self.connector,
            connector_owner=False,
            timeout=self.timeout,
            retry_options=self.retry_options,
            headers=self._resolve_headers(),
        )

    async def whoami(self) -> WhoamiResult:
        """Probe identity as the receiving service sees this client.

        Exercises the same session + header resolution path as every other
        method on the host client, so integration tests calling this method
        will catch regressions in SPIFFE JWT injection or retry wrapping.

        Returns:
            The JSON body of ``GET /whoami`` (``user`` and ``roles``).
        """
        async with self._new_session() as session:
            async with session.get(f"{self.base_url}/whoami") as response:
                response.raise_for_status()
                return cast("WhoamiResult", await response.json())
