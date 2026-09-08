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
"""Tests for provider-backed render event utility functions."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager.dcim import RenderDeviceStatus
from nv_config_manager.render.events.util import (
    DeviceNotEnabledError,
    clear_queued,
    is_queued,
    mark_queued,
    queue_render,
    should_run,
)


@pytest.fixture
def mock_dcim_client():
    """Provide the selected DCIM client through its public lifecycle seam."""
    client = AsyncMock()
    client.get_render_device_status.return_value = None

    @asynccontextmanager
    async def session():
        yield client

    with patch("nv_config_manager.render.events.util.dcim_client_session", session):
        yield client


@pytest.fixture
def mock_nats_connection():
    """Mock a newly-created NATS connection and JetStream publisher."""
    with patch("nv_config_manager.render.events.util.nats_connection") as mock:
        connection = AsyncMock()
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()
        connection.jetstream = MagicMock(return_value=jetstream)
        connection.is_closed = False
        mock.return_value = connection
        yield connection


@pytest.mark.asyncio
async def test_should_run_checks_provider_render_status(mock_dcim_client, custom_ini):
    """Render eligibility is provider-neutral and respects environment scope."""
    custom_ini("""
[aggregate]
is_aggregate_environment=false
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )

    assert await should_run("test-device", mock_dcim_client) is True
    mock_dcim_client.get_render_device_status.assert_awaited_once_with("test-device")


@pytest.mark.asyncio
async def test_should_run_rejects_disabled_or_missing_device(mock_dcim_client):
    """Missing and disabled managed-device states do not queue a render."""
    assert await should_run("test-device", mock_dcim_client) is False
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=False, is_aggregate_managed=False
    )
    assert await should_run("test-device", mock_dcim_client) is False


@pytest.mark.asyncio
async def test_should_run_rejects_aggregate_scope_mismatch(mock_dcim_client, custom_ini):
    """Aggregate scoping remains a consumer policy over normalized status."""
    custom_ini("""
[aggregate]
is_aggregate_environment=true
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )

    assert await should_run("test-device", mock_dcim_client) is False


@pytest.mark.asyncio
async def test_queue_render_uses_provider_status_before_publishing(
    mock_dcim_client, mock_nats_connection, custom_ini
):
    """A normalized enabled status permits a JetStream render event."""
    custom_ini("""
[aggregate]
is_aggregate_environment=false

[nats]
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )
    with patch("nv_config_manager.render.events.util._get_queue_redis_client") as redis_getter:
        redis = MagicMock()
        redis.exists = AsyncMock(return_value=False)
        redis.setex = AsyncMock()
        redis_getter.return_value = redis

        await queue_render("test-device", "message", "user", "2024-01-16T21:46:05Z")

    mock_nats_connection.jetstream.return_value.publish.assert_awaited_once()
    mock_dcim_client.get_render_device_status.assert_awaited_once_with("test-device")


@pytest.mark.asyncio
async def test_queue_render_reports_disabled_device(mock_dcim_client, mock_nats_connection):
    """A disabled provider status does not publish a render event."""
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=False, is_aggregate_managed=False
    )
    with patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None):
        with pytest.raises(DeviceNotEnabledError, match="not enabled for configuration renders"):
            await queue_render("test-device", "message", "user", "2024-01-16T21:46:05Z")

    mock_nats_connection.jetstream.return_value.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_render_clears_queued_flag_when_publish_fails(
    mock_dcim_client, mock_nats_connection, custom_ini
):
    """A failed publish clears the temporary queue marker."""
    custom_ini("""
[aggregate]
is_aggregate_environment=false

[nats]
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )
    mock_nats_connection.jetstream.return_value.publish.side_effect = RuntimeError("NATS error")
    with patch("nv_config_manager.render.events.util._get_queue_redis_client") as redis_getter:
        redis = MagicMock()
        redis.exists = AsyncMock(return_value=False)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()
        redis_getter.return_value = redis

        with pytest.raises(Exception, match="NATS error"):
            await queue_render("test-device", "message", "user", "2024-01-16T21:46:05Z")

    redis.delete.assert_awaited_once_with("test-device_queued")


@pytest.mark.asyncio
async def test_redis_deduplication_functions():
    """Queue-marker helpers retain their Redis behavior."""
    with patch("nv_config_manager.render.events.util._get_queue_redis_client") as redis_getter:
        redis = MagicMock()
        redis.setex = AsyncMock()
        redis.exists = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        redis_getter.return_value = redis

        await mark_queued("device")
        assert await is_queued("device") is True
        await clear_queued("device")

    redis.setex.assert_awaited_once_with("device_queued", 60, 1, serialize=False)
    redis.delete.assert_awaited_once_with("device_queued")
