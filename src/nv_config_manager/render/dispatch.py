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
"""Dynamic Event Dispatcher."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Histogram

from nv_config_manager.common.client import ConfigStoreException
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import (
    DCIMChangeEvent,
    DCIMClient,
    DCIMError,
    DCIMRenderEventHandler,
    DCIMRenderEventProvider,
    DCIMRenderEventRegistry,
    RenderEventRequest,
    dcim_client_session,
    get_dcim_provider,
)
from nv_config_manager.render.events.util import queue_render_batch
from nv_config_manager.render.exceptions import RenderException
from nv_config_manager.render.render import execute_render

# Define custom bucket values for histograms (in seconds)
HISTOGRAM_BUCKETS = [
    0.1,
    0.5,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    float("inf"),
]

EVENT_PROCESSING_TIME = Histogram(
    "nv_config_manager_event_processing_time",
    "Time spent processing an event",
    ["model", "instance", "namespace"],
    buckets=HISTOGRAM_BUCKETS,
)
EVENT_RECEIVED_COUNTER = Counter(
    "nv_config_manager_events_received",
    "Events received via NATS",
    ["model", "instance", "namespace"],
)
EVENT_PROCESSED_COUNTER = Counter(
    "nv_config_manager_events_processed",
    "Events processed by the dispatcher",
    ["model", "instance", "namespace"],
)
EVENT_SKIPPED_COUNTER = Counter(
    "nv_config_manager_events_skipped",
    "Events skipped by the dispatcher",
    ["model", "instance", "namespace"],
)
EVENT_FAILED_COUNTER = Counter(
    "nv_config_manager_events_failed",
    "Events failed to process",
    ["model", "instance", "exception_class", "namespace"],
)

NAUTOBOT_CHANGE_PROCESSING_TIME = Histogram(
    "nv_config_manager_nautobot_change_message_processing_time",
    "Time spent processing a render for a nautobot change",
    ["instance", "namespace"],
    buckets=HISTOGRAM_BUCKETS,
)
NAUTOBOT_CHANGE_END_TO_END_TIME = Histogram(
    "nv_config_manager_nautobot_change_message_end_to_end_time",
    "End-to-end time from Nautobot message publish to config store persistence in seconds",
    ["instance", "namespace"],
    buckets=HISTOGRAM_BUCKETS,
)
NAUTOBOT_CHANGE_RECEIVED_COUNTER = Counter(
    "nv_config_manager_nautobot_change_messages_received",
    "Nautobot change messages received via NATS",
    ["instance", "namespace"],
)
NAUTOBOT_CHANGE_PROCESSED_COUNTER = Counter(
    "nv_config_manager_nautobot_change_messages_processed",
    "Nautobot change messages processed by the dispatcher",
    ["instance", "namespace"],
)
NAUTOBOT_CHANGE_FAILED_COUNTER = Counter(
    "nv_config_manager_nautobot_change_messages_failed",
    "Nautobot change messages that failed to process",
    ["instance", "exception_class", "namespace"],
)


class EventDispatcher(DCIMRenderEventRegistry):  # pylint: disable=too-few-public-methods
    """Provider-neutral dispatcher for render-triggering DCIM events."""

    logger = get_logger(__name__, category=LogCategory.RENDER_EVENT)

    def __init__(self, provider: DCIMRenderEventProvider | None = None) -> None:
        """Initialize the dispatcher with the selected provider's handlers."""
        self.dispatch_table: dict[str, DCIMRenderEventHandler] = {}
        self.instance = os.getenv("HOSTNAME", "unknown")
        self.namespace = os.getenv("NV_CONFIG_MANAGER_K8S_NAMESPACE", "unknown")

        selected_provider = provider or get_dcim_provider()
        if isinstance(selected_provider, DCIMRenderEventProvider):
            selected_provider.register_render_event_handlers(self)
        else:
            self.logger.warning(
                "DCIM provider %s does not define render event handlers",
                selected_provider.metadata.name,
            )

    def register_render_event_handler(
        self,
        object_type: str,
        handler: DCIMRenderEventHandler,
    ) -> None:
        """Register one provider-owned handler for a DCIM object type."""
        if object_type in self.dispatch_table:
            raise ValueError(f"Duplicate render event handler for {object_type}")
        self.dispatch_table[object_type] = handler

    async def _queue_requests(
        self,
        requests: tuple[RenderEventRequest, ...],
        event: DCIMChangeEvent,
        dcim_client: DCIMClient,
    ) -> None:
        """Queue provider-identified render requests in commit-message batches."""
        requests_by_message: dict[str, list[str]] = defaultdict(list)
        for request in requests:
            requests_by_message[request.commit_message].append(request.device_id)

        for commit_message, device_ids in requests_by_message.items():
            _, failures = await queue_render_batch(
                device_ids,
                commit_message,
                event.actor,
                event.timestamp,
                dcim_client=dcim_client,
            )
            for failure in failures:
                self.logger.info(
                    "Render request for %s was not queued: %s",
                    failure["device_uuid"],
                    failure["error"],
                )

    async def dcim_event_dispatch(self, event: DCIMChangeEvent) -> None:
        """Invoke the selected provider's handler for one normalized event."""
        model = event.object_type

        EVENT_RECEIVED_COUNTER.labels(model, self.instance, self.namespace).inc()
        if event.record is None:
            # Have seen instances of this occurring, could be an object that was deleted
            # after an update and therefore the change producer has no record to include
            # in the message.
            self.logger.info(
                "Event %s for object %s has no record, ignoring message.",
                model,
                event.object_id,
            )
            EVENT_SKIPPED_COUNTER.labels(model, self.instance, self.namespace).inc()
            return

        try:
            handler = self.dispatch_table[model]
        except KeyError:
            self.logger.info("No event handler implemented for %s, ignoring message.", model)
            EVENT_SKIPPED_COUNTER.labels(model, self.instance, self.namespace).inc()
            return

        try:
            with EVENT_PROCESSING_TIME.labels(model, self.instance, self.namespace).time():
                async with dcim_client_session() as dcim_client:
                    requests = tuple(await handler(event, dcim_client))
                    await self._queue_requests(requests, event, dcim_client)
                EVENT_PROCESSED_COUNTER.labels(model, self.instance, self.namespace).inc()
        except DCIMError as exc:
            self.logger.exception("Error processing event: %s", event)
            EVENT_FAILED_COUNTER.labels(
                model, self.instance, exc.__class__.__name__, self.namespace
            ).inc()

    async def nautobot_change_dispatch(self, data: dict[str, Any]) -> None:
        """Invoke a device render for a nautobot change."""
        NAUTOBOT_CHANGE_RECEIVED_COUNTER.labels(self.instance, self.namespace).inc()
        try:
            with NAUTOBOT_CHANGE_PROCESSING_TIME.labels(self.instance, self.namespace).time():
                await execute_render(
                    data["device_id"],
                    data["commit_message"],
                    data["user"],
                )
                e2e_time = (
                    datetime.now(UTC) - datetime.fromisoformat(data["@timestamp"])
                ).total_seconds()
                NAUTOBOT_CHANGE_END_TO_END_TIME.labels(
                    self.instance,
                    self.namespace,
                ).observe(e2e_time)
                NAUTOBOT_CHANGE_PROCESSED_COUNTER.labels(self.instance, self.namespace).inc()

        except (RenderException, ConfigStoreException, DCIMError) as exc:
            self.logger.exception("Error processing nautobot device change event: %s", data)
            NAUTOBOT_CHANGE_FAILED_COUNTER.labels(
                self.instance, exc.__class__.__name__, self.namespace
            ).inc()
