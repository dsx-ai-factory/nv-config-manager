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

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
from nv_config_manager.render.dispatch import EventDispatcher
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
async def _dcim_session():
    yield AsyncMock()


@pytest.mark.asyncio
async def test_dcim_event_dispatch_reraises_transient_dcim_failure():
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
async def test_dcim_event_dispatch_consumes_dcim_data_error():
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
    [DCIMConnectivityError("DCIM unreachable"), ConfigStoreUnavailableError("unavailable")],
)
@patch("nv_config_manager.render.dispatch.execute_render", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_nautobot_change_dispatch_reraises_transient_failure(mock_render, failure):
    """A dependency outage is left for the consumer to retry with backoff."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    mock_render.side_effect = failure
    with patch.object(dispatcher, "logger") as logger:
        with pytest.raises(type(failure)):
            await dispatcher.nautobot_change_dispatch(dict(_RENDER_MESSAGE))
    logger.exception.assert_not_called()


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
async def test_nautobot_change_dispatch_consumes_data_errors(mock_render, failure):
    """Errors a retry cannot fix are logged and the message is consumed."""
    dispatcher = EventDispatcher(SyntheticRenderEventProvider())
    mock_render.side_effect = failure
    with patch.object(dispatcher, "logger") as logger:
        await dispatcher.nautobot_change_dispatch(dict(_RENDER_MESSAGE))
    logger.exception.assert_called_once()
