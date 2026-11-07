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

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, ConsumerInfo, DeliverPolicy
from nats.js.errors import NotFoundError
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_infrastructure.nats.admin import (
    ARCHIVE_CONSUMER_MAX_ACK_PENDING,
    ARCHIVE_CONSUMER_MAX_DELIVER,
    CONSUMER_ACK_WAIT_SECONDS,
    is_nats_permissions_error,
)
from nv_config_manager_infrastructure.nats.client import NatsClient

logger = get_logger(__name__, category=LogCategory.NATS)

# Below the default pod termination grace period, so the connection still closes
# cleanly; any handler still running is redelivered after its ack wait.
HANDLER_DRAIN_SECONDS = 20


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
        manual_ack: bool = False,
        max_concurrent_handlers: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the stream, subject, durable, and message handler.

        Each delivery runs as its own task, so up to the durable's
        ``max_ack_pending`` messages are handled concurrently and none waits in
        the client while its ack wait runs. ``max_concurrent_handlers`` caps that
        locally for a durable provisioned with a higher limit. With
        ``manual_ack`` the handler must ack, nak, or term every message itself;
        otherwise each message is acked once the handler returns.
        """
        super().__init__(**kwargs)
        self.stream = stream
        self.subject = subject
        self.queue_suffix = queue_suffix
        self.durable_name = durable_name
        self.deliver_subject = deliver_subject
        self.manual_ack = manual_ack
        self.handler = handler
        # The bound durable's limit, which is what JetStream enforces.
        self.max_deliver = ARCHIVE_CONSUMER_MAX_DELIVER
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._subscription: Any = None
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._handler_slots = (
            asyncio.Semaphore(max_concurrent_handlers) if max_concurrent_handlers else None
        )

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
            max_deliver = consumer_info.config.max_deliver
            if max_deliver is None:
                self.max_deliver = -1
            elif isinstance(max_deliver, int):
                self.max_deliver = max_deliver

            # The client's auto-ack would ack as soon as the dispatch returns.
            self._subscription = await jetstream.subscribe_bind(
                stream=self.stream,
                consumer=durable,
                config=consumer_info.config,
                cb=self._dispatch,
                manual_ack=True,
            )
            logger.info(
                "Subscribed to subject %s on stream %s with queue %s",
                self.subject,
                self.stream,
                self.full_queue_name,
            )

            while not self.conn.is_closed and not self._stop.is_set():
                await asyncio.sleep(1)
        finally:
            await self._drain_handlers(self.conn)
            await self._close_connection(self.conn)

    async def _dispatch(self, msg: Msg) -> None:
        """Start handling a delivery without holding up the next one, up to the cap."""
        if self._handler_slots is not None:
            await self._handler_slots.acquire()
        task = asyncio.create_task(self._handle(msg))
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_finished)

    def _handler_finished(self, task: asyncio.Task[None]) -> None:
        self._handler_tasks.discard(task)
        if self._handler_slots is not None:
            self._handler_slots.release()

    async def _handle(self, msg: Msg) -> None:
        """Run the handler, acking afterwards unless it settles messages itself."""
        try:
            await self.handler(msg)
            if not self.manual_ack:
                await msg.ack()
        except Exception:
            logger.exception("Handler failed for message on subject %s", msg.subject)

    async def _drain_handlers(self, conn: Client) -> None:
        """Stop deliveries, then let in-flight handlers settle their messages."""
        if self._subscription is not None and not conn.is_closed:
            try:
                await self._subscription.unsubscribe()
            except Exception:
                logger.warning("Could not unsubscribe before draining handlers", exc_info=True)
        if not self._handler_tasks:
            return
        _, pending = await asyncio.wait(set(self._handler_tasks), timeout=HANDLER_DRAIN_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning(
                "Cancelled %d handlers still running after %ds; JetStream will redeliver them",
                len(pending),
                HANDLER_DRAIN_SECONDS,
            )
            await asyncio.gather(*pending, return_exceptions=True)

    async def _close_connection(self, conn: Client) -> None:
        """Close the connection without reporting a TLS teardown race."""
        asyncio_logger = logging.getLogger("asyncio")
        old_loglevel = asyncio_logger.level
        asyncio_logger.setLevel(logging.ERROR)
        try:
            await conn.close()
        except ssl.SSLError:
            pass
        finally:
            asyncio_logger.setLevel(old_loglevel)

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
            max_deliver=ARCHIVE_CONSUMER_MAX_DELIVER,
            max_ack_pending=ARCHIVE_CONSUMER_MAX_ACK_PENDING,
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
            "max_ack_pending",
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
                "ack_wait=%ss, max_deliver=%s, max_ack_pending=%s, deliver_subject=%r, and "
                "deliver_group=%r.",
                durable,
                self.stream,
                f"{self.api_prefix}.CONSUMER.INFO.{self.stream}.{durable}",
                f"{self.api_prefix}.CONSUMER.DURABLE.CREATE.{self.stream}.{durable}",
                self.subject,
                CONSUMER_ACK_WAIT_SECONDS,
                ARCHIVE_CONSUMER_MAX_DELIVER,
                ARCHIVE_CONSUMER_MAX_ACK_PENDING,
                self.deliver_subject,
                durable,
            )
            return
        await super()._error_cb(error)

    def _clean_exit(self) -> None:
        """Ask the consumer loop to drain its handlers and close the connection."""
        self._stop.set()
