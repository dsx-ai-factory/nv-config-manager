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
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
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
# Bound on each JetStream call the liveness monitor makes. The client's own
# request timeout covers only the wait for a reply, not a publish held up
# behind a full outbound buffer, and a monitor stuck there never checks for a stall.
LIVENESS_API_TIMEOUT_SECONDS = 30.0

# JetStream error code for "consumer not found".
CONSUMER_NOT_FOUND_ERR_CODE = 10014


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
        liveness_timeout: float | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the stream, subject, durable, and message handler.

        Each delivery runs as its own task, so up to the durable's
        ``max_ack_pending`` messages are handled concurrently and none waits in
        the client while its ack wait runs. ``max_concurrent_handlers`` caps that
        locally for a durable provisioned with a higher limit. With
        ``manual_ack`` the handler must ack, nak, or term every message itself;
        otherwise each message is acked once the handler returns.

        When liveness_timeout is set, main() polls the durable and raises once
        neither the durable nor this process has made progress for that long, so
        the process exits and is restarted.
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
        # Wall-clock copies for metrics, which report Unix time.
        self.last_progress_time = time.time()
        self._handler_starts: dict[asyncio.Task[None], float] = {}
        self._last_sequences: tuple[int, int] | None = None
        # What a durable deleted under this process is re-created from: the
        # configuration it was bound with, and the last acknowledged position.
        self._bound_config: ConsumerConfig | None = None
        self._ack_floor_seq = 0
        self._bound_at = datetime.now(UTC)
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
            self._adopt_durable(consumer_info)

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

            monitor = None
            if self.liveness_timeout is not None:
                self._record_progress()
                self._bound_at = datetime.now(UTC)
                monitor = asyncio.create_task(self._monitor_liveness(jetstream))
            try:
                while not self.conn.is_closed and not self._stop.is_set():
                    if monitor is not None and monitor.done():
                        # Re-raise a stall so the process exits and is restarted.
                        monitor.result()
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
        self._handler_starts[task] = time.time()
        task.add_done_callback(self._handler_finished)

    def _handler_finished(self, task: asyncio.Task[None]) -> None:
        self._handler_tasks.discard(task)
        if self._handler_slots is not None:
            self._handler_slots.release()
        self._handler_starts.pop(task, None)
        if not task.cancelled():
            self._record_progress()

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
        """Close the connection without reporting a TLS teardown race.

        A connection that is already dead can fail to close. That must not
        replace the error main() is exiting with, such as a stalled durable.
        """
        asyncio_logger = logging.getLogger("asyncio")
        old_loglevel = asyncio_logger.level
        asyncio_logger.setLevel(logging.ERROR)
        try:
            await conn.close()
        except ssl.SSLError:
            pass
        except Exception as e:
            logger.warning("Could not close the NATS connection cleanly: %r", e)
        finally:
            asyncio_logger.setLevel(old_loglevel)

    @property
    def oldest_handler_start(self) -> float:
        """Unix time the longest-running in-flight handler started, or 0 when idle."""
        return min(list(self._handler_starts.values()), default=0.0)

    def _record_progress(self) -> None:
        self._last_progress = time.monotonic()
        self.last_progress_time = time.time()

    def liveness(self) -> tuple[bool, str]:
        """Report whether the durable or this process has made progress recently enough.

        A push consumer whose deliveries stop raises nothing, so the process would
        otherwise stay Running while archiving nothing.

        A delivery this process finished handling counts as progress even when the
        handler failed, because a restart cannot fix a failing backend and would
        otherwise repeat for as long as the durable is stalled behind it.
        """
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
        the others do the work. Redeliveries do not advance the stream sequences,
        and one of an older message can move the delivered sequence backward, so
        only a sequence above the highest seen counts.
        """
        delivered = consumer_info.delivered.stream_seq if consumer_info.delivered else 0
        acked = consumer_info.ack_floor.stream_seq if consumer_info.ack_floor else 0
        outstanding = (consumer_info.num_pending or 0) + (consumer_info.num_ack_pending or 0)
        seen_delivered, seen_acked = self._last_sequences or (-1, -1)
        advanced = delivered > seen_delivered or acked > seen_acked
        self._last_sequences = (max(delivered, seen_delivered), max(acked, seen_acked))
        if outstanding == 0 or advanced:
            self._record_progress()

    async def _monitor_liveness(self, jetstream: JetStreamContext) -> None:
        """Poll the durable, and raise once it and this process have stopped progressing.

        A blocked event loop cannot run this check; the progress metrics cover that.
        """
        while self.conn is not None and not self.conn.is_closed:
            try:
                consumer_info = await asyncio.wait_for(
                    jetstream.consumer_info(self.stream, self.full_queue_name),
                    LIVENESS_API_TIMEOUT_SECONDS,
                )
            except NotFoundError as e:
                if e.err_code == CONSUMER_NOT_FOUND_ERR_CODE:
                    await self._recreate_durable(jetstream)
                else:
                    logger.warning(
                        "Consumer info for %s on stream %s failed: %s",
                        self.full_queue_name,
                        self.stream,
                        e,
                    )
            except Exception as e:
                logger.warning(
                    "Consumer info for %s on stream %s failed: %s",
                    self.full_queue_name,
                    self.stream,
                    e,
                )
            else:
                self._adopt_durable(consumer_info)
                if self.conn.is_connected:
                    self._record_consumer_info(consumer_info)
            live, detail = self.liveness()
            if not live:
                raise RuntimeError(
                    f"Consumer {self.full_queue_name} is exiting to be restarted: {detail}"
                )
            await asyncio.sleep(LIVENESS_CHECK_INTERVAL_SECONDS)

    def _adopt_durable(self, consumer_info: ConsumerInfo) -> None:
        """Follow the durable's delivery limit, and remember how to re-create it."""
        config = consumer_info.config
        max_deliver = config.max_deliver
        if max_deliver is None:
            self.max_deliver = -1
        elif isinstance(max_deliver, int):
            self.max_deliver = max_deliver
        if isinstance(config, ConsumerConfig):
            self._bound_config = config
        acked = consumer_info.ack_floor.stream_seq if consumer_info.ack_floor else 0
        if isinstance(acked, int):
            self._ack_floor_seq = max(self._ack_floor_seq, acked)

    async def _recreate_durable(self, jetstream: JetStreamContext) -> None:
        """Re-create a durable deleted under this process, after its last acknowledged message.

        Exiting instead would let startup create it at new messages only, and the
        events published since the deletion would never be archived.

        The replacement copies the configuration this process is bound to, not the
        configured defaults, so the existing subscription keeps receiving and the
        delivery limit does not change. It starts after the acknowledgement floor,
        so messages that were delivered but not yet archived are delivered again.
        A durable that never acknowledged anything starts from when this process
        bound to it instead of replaying the stream from its beginning.

        The server-returned ``name`` is cleared so the request goes to the
        ``CONSUMER.DURABLE.CREATE`` subject that startup uses, rather than
        ``CONSUMER.CREATE``, which an account's permissions may not allow.
        """
        base = self._bound_config or self._expected_consumer_config()
        if self._ack_floor_seq > 0:
            start = f"stream sequence {self._ack_floor_seq + 1}"
            config = replace(
                base,
                name=None,
                deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=self._ack_floor_seq + 1,
                opt_start_time=None,
            )
        else:
            start = self._bound_at.isoformat()
            config = replace(
                base,
                name=None,
                deliver_policy=DeliverPolicy.BY_START_TIME,
                opt_start_seq=None,
                opt_start_time=self._bound_at,
            )
        try:
            consumer_info = await asyncio.wait_for(
                jetstream.add_consumer(stream=self.stream, config=config),
                LIVENESS_API_TIMEOUT_SECONDS,
            )
        except Exception as e:
            # Another replica may have re-created it first, or creation is left
            # to an administrator. The next poll shows which.
            logger.warning(
                "Consumer %s on stream %s is missing and could not be re-created: %s",
                self.full_queue_name,
                self.stream,
                e,
            )
            return
        max_deliver = consumer_info.config.max_deliver
        if isinstance(max_deliver, int):
            self.max_deliver = max_deliver
        logger.warning(
            "Consumer %s on stream %s was missing and has been re-created, starting from %s",
            self.full_queue_name,
            self.stream,
            start,
        )

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
