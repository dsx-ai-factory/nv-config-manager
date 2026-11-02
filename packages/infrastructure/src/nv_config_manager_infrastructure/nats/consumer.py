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
"""JetStream durable push consumer."""

import asyncio
import logging
import signal
import ssl
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, ConsumerInfo, DeliverPolicy
from nats.js.errors import NotFoundError
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_infrastructure.nats.admin import (
    CONSUMER_ACK_WAIT_SECONDS,
    CONSUMER_MAX_DELIVER,
    is_nats_permissions_error,
)
from nv_config_manager_infrastructure.nats.client import NatsClient

logger = get_logger(__name__, category=LogCategory.NATS)


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
        """Initialize the stream, subject, durable, and message handler."""
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
        """Run the consumer until interrupted."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "NatsConsumer.run() cannot be called from a running event loop; "
                "await consumer.main() instead"
            )

        self._loop = asyncio.new_event_loop()
        try:
            for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                self._loop.add_signal_handler(sig, self._clean_exit)

            logger.info("Starting consumer event loop")
            self._loop.run_until_complete(self.main())
        finally:
            self._loop.close()

    async def main(self) -> None:
        """Connect, bind the durable consumer, and process messages."""
        self.conn = await self.connect()
        try:
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
        finally:
            await self.conn.close()

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
                consumer_info = await jetstream.add_consumer(stream=self.stream, config=expected)
                logger.info("Created NATS consumer %s on stream %s", durable, self.stream)
            except Exception as create_error:
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
        """Schedule connection cleanup on the consumer event loop."""

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
