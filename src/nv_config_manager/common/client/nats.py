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
"""NATS Client.

Shared NATS client for all NVIDIA Config Manager services.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import ssl
from collections.abc import Awaitable, Callable
from configparser import ConfigParser
from typing import Any, cast

import certifi
import nats
import nats.js.errors
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, ConsumerInfo, DeliverPolicy, StreamInfo
from nats.js.errors import NotFoundError

from nv_config_manager.common.http_config import (
    DEFAULT_NATS_API_PREFIX,
    config_manager_api_prefix,
)
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.common.nats_admin import (
    CONSUMER_ACK_WAIT_SECONDS,
    CONSUMER_MAX_DELIVER,
    is_nats_permissions_error,
)

logger = get_logger(__name__, category=LogCategory.NATS)


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

    @classmethod
    def from_config(cls, config: ConfigParser) -> NatsClient:
        """Create NatsClient from configuration.

        Args:
            config: ConfigParser with 'nats' section

        Returns:
            Configured NatsClient instance
        """
        nats_config = config["nats"]

        stream_subjects = [
            subject.strip()
            for subject in nats_config.get("config_manager_subjects", "nv-config-manager.>").split(
                ","
            )
            if subject.strip()
        ]
        return cls(
            server=nats_config["server"],
            queue=nats_config.get("queue", "nv-config-manager"),
            local=nats_config.getboolean("local", fallback=False),
            auth_method=nats_config.get("auth_method", "password"),
            user=nats_config.get("user"),
            password=nats_config.get("password"),
            creds_path=nats_config.get("creds_path"),
            default_stream_name=nats_config.get("config_manager_stream", "nv-config-manager"),
            default_stream_subjects=stream_subjects,
            api_prefix=config_manager_api_prefix(nats_config),
        )

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
        # Use TLS and auth the same way as nats_connection() (used by Render).
        # When local=True we used to skip TLS, but the server may still require TLS
        # for JetStream; skipping it caused "Connected" then JetStream timeouts.
        if self.auth_method == "JWT":
            options["user_credentials"] = self.creds_path
        else:
            options["user"] = self.user
            options["password"] = self.password
        options["tls"] = self.ssl_context

        try:
            self.conn = await nats.connect(self.server, **options)
            # Stream lifecycle belongs to the bundled deployment or an external
            # administrator. Avoid requiring STREAM.INFO merely to publish to or
            # consume from an explicitly configured externally managed stream.
            if self.local:
                await self._ensure_stream()
        except Exception as err:
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


class NatsProducer(NatsClient):
    """NATS JetStream producer for publishing messages."""

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Publish a message to NATS JetStream.

        Args:
            subject: Subject to publish to
            message: Message payload
            stream: Stream name. Defaults to the configured client stream.
        """
        stream_name = stream or self.default_stream_name
        async with await self.connect() as conn:
            await conn.jetstream(prefix=self.api_prefix).publish(
                subject=subject, payload=message.encode("utf-8"), stream=stream_name
            )
            logger.debug("Published NATS message on stream %s subject %s", stream_name, subject)


class NatsConsumer(NatsClient):
    """NATS JetStream consumer for subscribing to messages."""

    def __init__(
        self,
        stream: str,
        subject: str,
        queue_suffix: str,
        handler: Callable[[Msg], Awaitable[None]],
        durable_name: str | None = None,
        deliver_subject: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the consumer.

        Args:
            stream: Stream name to consume from
            subject: Subject to subscribe to
            queue_suffix: Suffix for the queue group name
            handler: Async callback for handling messages
            durable_name: Exact durable name. Defaults to the legacy queue-based name.
            deliver_subject: Fixed push delivery subject for administrator provisioning.
            **kwargs: Additional arguments passed to NatsClient
        """
        super().__init__(**kwargs)
        self.stream = stream
        self.subject = subject
        self.queue_suffix = queue_suffix
        self.durable_name = durable_name
        self.deliver_subject = deliver_subject
        self.handler = handler
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def full_queue_name(self) -> str:
        """Get the full queue name."""
        return self.durable_name or f"{self.queue}-{self.queue_suffix}"

    def run(self) -> None:
        """Run the consumer (blocking)."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()

        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            self._loop.add_signal_handler(sig, self._clean_exit)

        logger.info("Starting consumer event loop")
        self._loop.run_until_complete(self.main())
        self._loop.close()

    async def main(self) -> None:
        """Main consumer loop."""
        self.conn = await self.connect()
        jetstream = self.conn.jetstream(prefix=self.api_prefix)
        durable = self.full_queue_name
        consumer_info = await self._ensure_consumer(jetstream)

        await jetstream.subscribe_bind(
            stream=self.stream,
            consumer=durable,
            config=consumer_info.config,
            cb=self.handler,
        )
        logger.info(
            "Subscribed to subject %s on stream %s with queue %s",
            self.subject,
            self.stream,
            self.full_queue_name,
        )

        while not self.conn.is_closed:
            await asyncio.sleep(1)

    def _expected_consumer_config(self) -> ConsumerConfig:
        """Build the exact push-consumer configuration owned by this runtime."""
        if not self.deliver_subject:
            raise ValueError("A fixed deliver_subject is required for a durable push consumer")
        durable = self.full_queue_name
        return ConsumerConfig(
            durable_name=durable,
            deliver_policy=DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=CONSUMER_ACK_WAIT_SECONDS,
            max_deliver=CONSUMER_MAX_DELIVER,
            filter_subject=self.subject,
            deliver_subject=self.deliver_subject,
            deliver_group=durable,
        )

    async def _ensure_consumer(self, jetstream: JetStreamContext) -> ConsumerInfo:
        """Return the fixed durable, creating it only when it is absent."""
        durable = self.full_queue_name
        try:
            consumer_info = await jetstream.consumer_info(self.stream, durable)
        except NotFoundError:
            expected = self._expected_consumer_config()
            try:
                consumer_info = await jetstream.add_consumer(
                    stream=self.stream,
                    config=expected,
                )
                logger.info("Created NATS consumer %s on stream %s", durable, self.stream)
            except Exception as create_error:
                # Another replica may have created the durable after our lookup. Bind to
                # it only when a fresh lookup confirms that race; otherwise preserve the
                # original creation failure and its permission guidance.
                try:
                    consumer_info = await jetstream.consumer_info(self.stream, durable)
                except Exception as lookup_error:
                    raise create_error from lookup_error

        mismatches = self._consumer_configuration_mismatches(consumer_info)
        if mismatches:
            logger.warning(
                "Consumer %s on stream %s differs from the archive runtime: %s. Ask the "
                "NATS administrator to update the durable before relying on archival.",
                durable,
                self.stream,
                "; ".join(mismatches),
            )
        return cast(ConsumerInfo, consumer_info)

    def _consumer_configuration_mismatches(self, consumer_info: ConsumerInfo) -> list[str]:
        """Return operational differences without enforcing the migration policy."""
        config = consumer_info.config
        expected = self._expected_consumer_config()
        mismatches = []
        for field in (
            "durable_name",
            "filter_subject",
            "ack_policy",
            "ack_wait",
            "max_deliver",
            "deliver_subject",
            "deliver_group",
        ):
            actual_value = getattr(config, field)
            expected_value = getattr(expected, field)
            if actual_value != expected_value:
                mismatches.append(f"{field}={actual_value!r} expected {expected_value!r}")
        return mismatches

    async def _error_cb(self, error: Exception) -> None:
        """Log exact administrator guidance for restricted consumer operations."""
        if is_nats_permissions_error(error):
            durable = self.full_queue_name
            logger.error(
                "NATS denied an operation for consumer %s on stream %s. Ask the NATS "
                "administrator to grant publish access to %s and %s, or provision that "
                "durable with filter_subject=%r, deliver_policy='new', ack_policy='explicit', "
                "ack_wait=%ss, max_deliver=%s, deliver_subject=%r, and deliver_group=%r.",
                durable,
                self.stream,
                f"{self.api_prefix}.CONSUMER.INFO.{self.stream}.{durable}",
                f"{self.api_prefix}.CONSUMER.DURABLE.CREATE.{self.stream}.{durable}",
                self.subject,
                CONSUMER_ACK_WAIT_SECONDS,
                CONSUMER_MAX_DELIVER,
                self.deliver_subject,
                durable,
            )
            return
        await super()._error_cb(error)

    def _clean_exit(self) -> None:
        """Cancel all running tasks and close the connection."""

        async def close_connection() -> None:
            asyncio_logger = logging.getLogger("asyncio")
            old_loglevel = asyncio_logger.level
            asyncio_logger.setLevel(logging.ERROR)
            try:
                if self.conn and not self.conn.is_closed:
                    await self.conn.close()
            except ssl.SSLError:
                pass
            asyncio_logger.setLevel(old_loglevel)

        if self._loop:
            self._loop.create_task(close_connection())
