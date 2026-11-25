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
"""Tests for provider-registered render event dispatching."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from nv_config_manager.common.client.config_store import (
    ConfigStoreException,
    ConfigStoreUnavailableError,
)
from nv_config_manager.dcim import (
    DCIMChangeEvent,
    DCIMConnectivityError,
    DCIMInvalidDataError,
    RenderEventRequest,
)
from nv_config_manager.render.dispatch import (
    END_TO_END_HISTOGRAM_BUCKETS,
    EVENT_FAILED_COUNTER,
    HISTOGRAM_BUCKETS,
    NAUTOBOT_CHANGE_FAILED_COUNTER,
    EventDispatcher,
    _event_retry_key,
)
from nv_config_manager.render.exceptions import RenderException, RenderScopeMismatch


class SyntheticRenderEventProvider:
    """Small provider that proves the dispatcher owns no object-type logic."""

    def register_render_event_handlers(self, registry) -> None:
        """Register the one synthetic event type."""
        registry.register_render_event_handler("synthetic.device", self.device)

    async def device(self, event, client) -> tuple[RenderEventRequest, ...]:
        """Identify the device entirely in provider code."""
        assert client is not None
        return (RenderEventRequest(device_id=event.object_id, commit_message="synthetic change"),)


@pytest.mark.asyncio
async def test_dispatch_uses_provider_registered_handler():
    """A selected provider defines event types and affected devices."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    dcim_client = AsyncMock()

    @asynccontextmanager
    async def session():
        yield dcim_client

    event = DCIMChangeEvent(
        provider="synthetic",
        operation="update",
        object_type="synthetic.device",
        object_id="device-1",
        timestamp="2026-07-20T00:00:00Z",
        actor="user",
        record={"id": "device-1"},
    )

    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            return_value=(1, []),
        ) as queue_render_batch,
    ):
        await dispatcher.dcim_event_dispatch(event)

    queue_render_batch.assert_awaited_once_with(
        ["device-1"],
        "synthetic change",
        "user",
        "2026-07-20T00:00:00Z",
        dcim_client=dcim_client,
        raise_transient_errors=True,
        retry_key=_event_retry_key(event),
    )


def _synthetic_event() -> DCIMChangeEvent:
    return DCIMChangeEvent(
        provider="synthetic",
        operation="update",
        object_type="synthetic.device",
        object_id="device-1",
        timestamp="2026-07-20T00:00:00Z",
        actor="user",
        record={"id": "device-1"},
    )


@asynccontextmanager
async def _dcim_session() -> AsyncIterator[AsyncMock]:
    yield AsyncMock()


@pytest.mark.asyncio
async def test_dcim_event_dispatch_reraises_transient_dcim_failure() -> None:
    """A DCIM outage leaves the event for redelivery instead of dropping it."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            side_effect=DCIMConnectivityError("DCIM unreachable"),
        ),
        patch.object(dispatcher, "logger") as logger,
    ):
        with pytest.raises(DCIMConnectivityError):
            await dispatcher.dcim_event_dispatch(_synthetic_event())
    logger.exception.assert_not_called()


@pytest.mark.asyncio
async def test_dcim_event_dispatch_counts_failure_outside_dcim_errors() -> None:
    """A raw timeout is retried by the consumer and still counted as a failure."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    failed = EVENT_FAILED_COUNTER.labels(
        "synthetic.device", dispatcher.instance, "TimeoutError", dispatcher.namespace
    )
    before = failed._value.get()
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            side_effect=TimeoutError(),
        ),
    ):
        with pytest.raises(TimeoutError):
            await dispatcher.dcim_event_dispatch(_synthetic_event())
    assert failed._value.get() == before + 1


class TwoMessageRenderEventProvider:
    """Provider whose handler asks for renders under two commit messages."""

    def register_render_event_handlers(self, registry: EventDispatcher) -> None:
        registry.register_render_event_handler("synthetic.device", self.handle_device)

    async def handle_device(
        self, event: DCIMChangeEvent, dcim_client: AsyncMock
    ) -> tuple[RenderEventRequest, ...]:
        return (
            RenderEventRequest(device_id="device-a", commit_message="first"),
            RenderEventRequest(device_id="device-b", commit_message="second"),
        )


@pytest.mark.asyncio
async def test_dcim_event_dispatch_queues_every_group_before_retrying() -> None:
    """An outage in one commit-message group does not hold back the next group."""
    dispatcher = EventDispatcher(TwoMessageRenderEventProvider())
    outage = DCIMConnectivityError("DCIM unreachable")
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            side_effect=[outage, (1, [])],
        ) as queue_render_batch,
    ):
        with pytest.raises(DCIMConnectivityError) as caught:
            await dispatcher.dcim_event_dispatch(_synthetic_event())

    assert caught.value is outage
    assert [call.args[0] for call in queue_render_batch.await_args_list] == [
        ["device-a"],
        ["device-b"],
    ]


@pytest.mark.asyncio
async def test_dcim_event_dispatch_stops_on_unexpected_queue_error() -> None:
    """A failure a retry is not known to fix is raised as soon as it happens."""
    dispatcher = EventDispatcher(TwoMessageRenderEventProvider())
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            side_effect=ValueError("unexpected"),
        ) as queue_render_batch,
    ):
        with pytest.raises(ValueError, match="unexpected"):
            await dispatcher.dcim_event_dispatch(_synthetic_event())

    queue_render_batch.assert_awaited_once()


class FailingRenderEventProvider:
    """Provider whose handler fails before it identifies any device."""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def register_render_event_handlers(self, registry: EventDispatcher) -> None:
        registry.register_render_event_handler("synthetic.device", self.handle_device)

    async def handle_device(
        self, event: DCIMChangeEvent, dcim_client: AsyncMock
    ) -> tuple[RenderEventRequest, ...]:
        raise self.failure


@pytest.mark.asyncio
async def test_dcim_event_dispatch_renews_retry_record_when_handler_is_retried() -> None:
    """A delivery that fails before enqueue keeps the record of devices already queued."""
    dispatcher = EventDispatcher(FailingRenderEventProvider(DCIMConnectivityError("down")))
    event = _synthetic_event()
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.renew_published", new_callable=AsyncMock
        ) as renew_published,
    ):
        with pytest.raises(DCIMConnectivityError):
            await dispatcher.dcim_event_dispatch(event)

    renew_published.assert_awaited_once_with(_event_retry_key(event))


@pytest.mark.asyncio
async def test_dcim_event_dispatch_does_not_renew_retry_record_for_consumed_event() -> None:
    """An event that is not retried has no record to keep."""
    dispatcher = EventDispatcher(FailingRenderEventProvider(DCIMInvalidDataError("bad record")))
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.renew_published", new_callable=AsyncMock
        ) as renew_published,
    ):
        await dispatcher.dcim_event_dispatch(_synthetic_event())

    renew_published.assert_not_awaited()


def test_event_retry_key_is_stable_per_event() -> None:
    """Redeliveries of one event share a key, and another event gets its own."""
    event = _synthetic_event()
    assert _event_retry_key(event) == _event_retry_key(_synthetic_event())
    assert _event_retry_key(event) != _event_retry_key(
        event.model_copy(update={"timestamp": "2026-07-20T00:00:01Z"})
    )


@pytest.mark.asyncio
async def test_dcim_event_dispatch_consumes_dcim_data_error() -> None:
    """Bad DCIM data will not render on retry, so the event is consumed."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    with (
        patch("nv_config_manager.render.dispatch.dcim_client_session", _dcim_session),
        patch(
            "nv_config_manager.render.dispatch.queue_render_batch",
            new_callable=AsyncMock,
            side_effect=DCIMInvalidDataError("no primary IP"),
        ),
        patch.object(dispatcher, "logger") as logger,
    ):
        await dispatcher.dcim_event_dispatch(_synthetic_event())
    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_ignores_unregistered_provider_event():
    """Unknown event types are safely ignored without opening a provider client."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    dispatcher.logger.info = MagicMock()
    event = DCIMChangeEvent(
        provider="synthetic",
        operation="update",
        object_type="synthetic.other",
        object_id="other-1",
        timestamp="2026-07-20T00:00:00Z",
        record={"id": "other-1"},
    )

    await dispatcher.dcim_event_dispatch(event)

    dispatcher.logger.info.assert_called_once_with(
        "No event handler implemented for %s, ignoring message.", "synthetic.other"
    )


@pytest.mark.asyncio
async def test_dispatch_logs_event_without_record() -> None:
    """Record-less events are observable when the dispatcher skips them."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    dispatcher.logger.info = MagicMock()
    event = DCIMChangeEvent(
        provider="synthetic",
        operation="delete",
        object_type="synthetic.device",
        object_id="device-1",
        timestamp="2026-07-20T00:00:00Z",
        record=None,
    )

    await dispatcher.dcim_event_dispatch(event)

    dispatcher.logger.info.assert_called_once_with(
        "Event %s for object %s has no record, ignoring message.",
        "synthetic.device",
        "device-1",
    )


@patch("nv_config_manager.render.dispatch.execute_render")
@pytest.mark.asyncio
async def test_nautobot_change_dispatch(mock_render):
    """The existing render-work queue continues to execute a device render."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    message = {
        "device_id": "device-1",
        "commit_message": "test commit message",
        "user": "test",
        "@timestamp": "2025-08-13T20:00:30Z",
    }

    await dispatcher.nautobot_change_dispatch(message)

    mock_render.assert_called_once_with("device-1", "test commit message", "test")


def test_end_to_end_buckets_extend_past_processing_buckets():
    """Queue wait beyond ten minutes lands in a finite bucket."""
    assert END_TO_END_HISTOGRAM_BUCKETS[: len(HISTOGRAM_BUCKETS) - 1] == HISTOGRAM_BUCKETS[:-1]
    assert {1800.0, 3600.0} <= set(END_TO_END_HISTOGRAM_BUCKETS)
    assert END_TO_END_HISTOGRAM_BUCKETS == sorted(END_TO_END_HISTOGRAM_BUCKETS)


@patch("nv_config_manager.render.dispatch.execute_render", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_queued_device_outside_aggregate_scope_is_skipped(mock_render):
    """Old queue entries for another cell are consumed without render failures."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    mock_render.side_effect = RenderScopeMismatch("Device is not aggregate-managed")
    message = {
        "device_id": "device-1",
        "commit_message": "message",
        "user": "test",
        "@timestamp": "2025-08-13T20:00:30Z",
    }
    with patch.object(dispatcher, "logger") as logger:
        await dispatcher.nautobot_change_dispatch(message)
    logger.info.assert_called_once()
    logger.exception.assert_not_called()


_RENDER_MESSAGE = {
    "device_id": "device-1",
    "commit_message": "message",
    "user": "test",
    "@timestamp": "2025-08-13T20:00:30Z",
}


@pytest.mark.parametrize(
    "failure",
    [
        DCIMConnectivityError("DCIM unreachable"),
        ConfigStoreUnavailableError("unavailable"),
        TimeoutError(),
        aiohttp.ClientConnectionError("reset"),
    ],
)
@patch("nv_config_manager.render.dispatch.execute_render", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_nautobot_change_dispatch_reraises_transient_failure(
    mock_render: AsyncMock, failure: Exception
) -> None:
    """A dependency outage is counted and left for the consumer to retry with backoff."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    mock_render.side_effect = failure
    failed = NAUTOBOT_CHANGE_FAILED_COUNTER.labels(
        dispatcher.instance, type(failure).__name__, dispatcher.namespace
    )
    before = failed._value.get()
    with patch.object(dispatcher, "logger") as logger:
        with pytest.raises(type(failure)):
            await dispatcher.nautobot_change_dispatch(dict(_RENDER_MESSAGE))
    logger.exception.assert_not_called()
    assert failed._value.get() == before + 1


@pytest.mark.parametrize(
    "failure",
    [
        DCIMInvalidDataError("no primary IP"),
        ConfigStoreException("bad request"),
        RenderException("template failed"),
    ],
)
@patch("nv_config_manager.render.dispatch.execute_render", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_nautobot_change_dispatch_consumes_data_errors(
    mock_render: AsyncMock, failure: Exception
) -> None:
    """Errors a retry cannot fix are logged and the message is consumed."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    mock_render.side_effect = failure
    with patch.object(dispatcher, "logger") as logger:
        await dispatcher.nautobot_change_dispatch(dict(_RENDER_MESSAGE))
    logger.exception.assert_called_once()
