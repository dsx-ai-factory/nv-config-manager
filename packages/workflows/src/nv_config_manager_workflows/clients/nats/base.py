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
"""Configuration-independent NATS connection client."""

from __future__ import annotations

import logging
import ssl
from typing import Any, TypedDict

import certifi
import nats
import nats.js.errors
from nats.aio.client import Client
from nats.js.api import StreamInfo

logger = logging.getLogger(__name__)

DEFAULT_NATS_API_PREFIX = "$JS.API"


class NatsClientSettings(TypedDict):
    """Explicit connection settings accepted by NATS clients."""

    api_prefix: str
    server: str
    queue: str
    local: bool
    auth_method: str
    user: str | None
    password: str | None
    creds_path: str | None
    default_stream_name: str
    default_stream_subjects: list[str]


class NatsClient:
    """Base client for NATS JetStream with explicit connection settings."""

    def __init__(
        self,
        server: str,
        queue: str = "nv-config-manager",
        local: bool = False,
        auth_method: str = "password",
        user: str | None = None,
        password: str | None = None,
        creds_path: str | None = None,
        default_stream_name: str = "nv-config-manager",
        default_stream_subjects: list[str] | None = None,
        api_prefix: str = DEFAULT_NATS_API_PREFIX,
    ) -> None:
        """Initialize the client without reading application configuration."""
        self.server = server
        self.queue = queue
        self.local = local
        self.auth_method = auth_method
        self.user = user
        self.password = password
        self.creds_path = creds_path
        self.default_stream_name = default_stream_name
        self.default_stream_subjects = default_stream_subjects or ["nv-config-manager.>"]
        self.api_prefix = api_prefix

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.load_verify_locations(certifi.where())
        self.conn: Client | None = None
        self.stream_info: StreamInfo | None = None

    async def _disconnected_cb(self) -> None:
        logger.info("Disconnected from NATS")

    async def _reconnected_cb(self) -> None:
        logger.info("Reconnected to NATS")

    async def _closed_cb(self) -> None:
        logger.info("NATS connection closed")

    async def _error_cb(self, error: Exception) -> None:
        logger.error("NATS error: %s", error, exc_info=error)

    async def connect(self) -> Client:
        """Connect to NATS and provision the local stream when requested."""
        options: dict[str, Any] = {
            "connect_timeout": 30,
            "reconnected_cb": self._reconnected_cb,
            "disconnected_cb": self._disconnected_cb,
            "closed_cb": self._closed_cb,
            "error_cb": self._error_cb,
            "tls": self.ssl_context,
        }
        if self.auth_method == "JWT":
            options["user_credentials"] = self.creds_path
        else:
            options["user"] = self.user
            options["password"] = self.password

        try:
            self.conn = await nats.connect(self.server, **options)
            if self.local:
                await self._ensure_stream()
        except Exception as error:
            logger.error(
                "NATS connection failed: server=%s error=%s",
                self.server,
                error,
                exc_info=True,
            )
            raise

        logger.info("Connected to NATS %s", self.conn.connected_url)
        return self.conn

    async def _ensure_stream(self) -> None:
        """Ensure the configured stream exists for a local deployment."""
        if not self.conn:
            return

        jetstream = self.conn.jetstream(prefix=self.api_prefix)
        try:
            self.stream_info = await jetstream.stream_info(self.default_stream_name)
        except nats.js.errors.NotFoundError:
            if self.local:
                logger.info("Configured stream %s not found, creating...", self.default_stream_name)
                await jetstream.add_stream(
                    name=self.default_stream_name,
                    subjects=self.default_stream_subjects,
                )
                self.stream_info = await jetstream.stream_info(self.default_stream_name)
            else:
                logger.error(
                    "Configured JetStream stream %s not found (publish will fail): server=%s",
                    self.default_stream_name,
                    self.server,
                )

    async def close(self) -> None:
        """Close the NATS connection when it is open."""
        if self.conn and not self.conn.is_closed:
            await self.conn.close()
