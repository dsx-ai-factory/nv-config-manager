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
import math
import signal
import ssl
import threading
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

LIVENESS_CHECK_INTERVAL_SECONDS = 15.0


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
        liveness_timeout: float | None = None,
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

        When liveness_timeout is set, main() polls the durable and liveness()
        fails once it has made no progress for that long.
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
        if liveness_timeout is not None:
            if not math.isfinite(liveness_timeout):
                raise ValueError(
                    f"Consumer liveness timeout {liveness_timeout:g} must be a finite number "
                    "of seconds, or a stalled consumer would never be restarted"
                )
            if liveness_timeout <= CONSUMER_ACK_WAIT_SECONDS:
                raise ValueError(
                    f"Consumer liveness timeout {liveness_timeout:g} must exceed the consumer "
                    f"ack wait of {CONSUMER_ACK_WAIT_SECONDS}s, or a slow handler would "
                    "restart the pod before its message is redelivered"
                )
        self.liveness_timeout = liveness_timeout
        self._last_progress = time.monotonic()
        self._last_sequences: tuple[int, int] | None = None
        self._subscribed = False
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
            self._subscribed = True

            monitor = None
            if self.liveness_timeout is not None:
                self._last_progress = time.monotonic()
                monitor = asyncio.create_task(self._monitor_liveness(jetstream))
            try:
                while not self.conn.is_closed and not self._stop.is_set():
                    await asyncio.sleep(1)
            finally:
                if monitor is not None:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
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

    def liveness(self) -> tuple[bool, str]:
        """Report whether the durable has made progress recently enough.

        A push consumer whose deliveries stop raises nothing, so the process would
        otherwise stay Running while archiving nothing. Startup reports live until
        the subscription is bound, since a startup failure already exits main().
        """
        if not self._subscribed:
            return True, f"durable {self.full_queue_name} is not subscribed yet"
        if self.conn is None or self.conn.is_closed:
            return False, "NATS connection is not open"
        stalled_for = time.monotonic() - self._last_progress
        if self.liveness_timeout is not None and stalled_for > self.liveness_timeout:
            return False, (
                f"durable {self.full_queue_name} made no progress for {stalled_for:.0f}s "
                f"(limit {self.liveness_timeout:.0f}s)"
            )
        return True, f"durable {self.full_queue_name} last made progress {stalled_for:.0f}s ago"

    def _record_consumer_info(self, consumer_info: ConsumerInfo) -> None:
        """Count the durable as progressing when it is caught up or its sequences advance.

        Replicas share the durable, so this reads its cluster-wide sequences rather
        than counting this pod's messages, which would fail an idle replica while
        the others do the work. Redeliveries do not advance the stream sequences.
        """
        delivered = consumer_info.delivered.stream_seq if consumer_info.delivered else 0
        acked = consumer_info.ack_floor.stream_seq if consumer_info.ack_floor else 0
        outstanding = (consumer_info.num_pending or 0) + (consumer_info.num_ack_pending or 0)
        advanced = (delivered, acked) != self._last_sequences
        self._last_sequences = (delivered, acked)
        if outstanding == 0 or advanced:
            self._last_progress = time.monotonic()

    async def _monitor_liveness(self, jetstream: JetStreamContext) -> None:
        """Poll the durable so liveness reflects whether it is still progressing."""
        while self.conn is not None and not self.conn.is_closed:
            try:
                consumer_info = await jetstream.consumer_info(self.stream, self.full_queue_name)
            except Exception as e:
                logger.warning(
                    "Consumer info for %s on stream %s failed: %s",
                    self.full_queue_name,
                    self.stream,
                    e,
                )
            else:
                if self.conn.is_connected:
                    self._record_consumer_info(consumer_info)
            await asyncio.sleep(LIVENESS_CHECK_INTERVAL_SECONDS)

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


def start_liveness_server(consumer: NatsConsumer, port: int) -> ThreadingHTTPServer:
    """Serve the consumer's liveness on /healthz from a daemon thread."""

    class LivenessHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in ("/healthz", ""):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            live, detail = consumer.liveness()
            body = detail.encode()
            self.send_response(HTTPStatus.OK if live else HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Drop per-request logging so probes do not flood the log."""

    server = ThreadingHTTPServer(("", port), LivenessHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
