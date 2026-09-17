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
"""Shared NATS connection and JetStream lifecycle client."""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import urlparse

import certifi
import nats
import nats.js.errors
from nats.aio.client import Client
from nats.js.api import StreamInfo
from nv_config_manager_logging import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.NATS)

# Defined here rather than in common.config because that module imports this
# package; common.config re-exports it as the public name.
DEFAULT_NATS_API_PREFIX = "$JS.API"


class NatsClient:
    """Base client for NATS JetStream."""

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
        """Initialize the NATS client.

        Args:
            server: NATS server URL
            queue: Queue prefix for consumer groups
            local: Whether this is a local development environment
            auth_method: Authentication method ('password' or 'JWT')
            user: Username for password auth
            password: Password for password auth
            creds_path: Path to credentials file for JWT auth
            api_prefix: JetStream API prefix. Use the exporting account's rewritten
                prefix when the stream is imported from another NATS account.
        """
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
        """Connect to the NATS server.

        Returns:
            Connected NATS client
        """
        logger.debug(
            "Connecting to NATS server=%s local=%s auth_method=%s",
            self.server,
            self.local,
            self.auth_method,
        )
        options: dict[str, Any] = {
            "connect_timeout": 30,
            "reconnected_cb": self._reconnected_cb,
            "disconnected_cb": self._disconnected_cb,
            "closed_cb": self._closed_cb,
            "error_cb": self._error_cb,
        }
        # External tls:// authentication uses TLS-first; wss:// establishes TLS
        # through the WebSocket transport before any NATS messages. Local
        # connections retain server-advertised TLS negotiation.
        scheme = urlparse(self.server).scheme
        if self.auth_method == "JWT":
            if not self.local and scheme not in ("tls", "wss"):
                raise ValueError(
                    "External JWT authentication requires a tls:// or wss:// NATS endpoint"
                )
            options["user_credentials"] = self.creds_path
            if not self.local and scheme == "tls":
                # Do not trust unauthenticated INFO to require encryption
                # before sending JWT/NKey authentication material.
                options["tls_handshake_first"] = True
        else:
            if (self.user or self.password) and not self.local and scheme not in ("tls", "wss"):
                raise ValueError(
                    "External password authentication requires a tls:// or wss:// NATS endpoint"
                )
            options["user"] = self.user
            options["password"] = self.password
            if scheme == "tls":
                # Encrypt before server INFO and client CONNECT so a peer
                # cannot suppress TLS negotiation and capture credentials.
                options["tls_handshake_first"] = True
        options["tls"] = self.ssl_context

        try:
            self.conn = await nats.connect(self.server, **options)
            # Stream lifecycle belongs to the bundled deployment or an external
            # administrator. Avoid requiring STREAM.INFO merely to publish to or
            # consume from an explicitly configured externally managed stream.
            if self.local:
                await self._ensure_stream()
        except Exception as err:
            if self.conn is not None and not self.conn.is_closed:
                await self.conn.close()
            logger.error(
                "NATS connection failed: server=%s error=%s",
                self.server,
                err,
                exc_info=True,
            )
            raise

        logger.info("Connected to NATS %s", self.conn.connected_url)
        return self.conn

    async def _ensure_stream(self) -> None:
        """Ensure the configured stream exists."""
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
        """Close the NATS connection."""
        if self.conn and not self.conn.is_closed:
            await self.conn.close()
