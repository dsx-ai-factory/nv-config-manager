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

from nv_config_manager.dcim import DCIMConnectivityError, RenderDeviceStatus
from nv_config_manager.render.events.util import (
    DeviceNotEnabledError,
    EnqueueResult,
    _process_single_device_enqueue,
    claim_queued,
    clear_queued,
    is_queued,
    queue_render,
    queue_render_batch,
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
        redis_getter.return_value = _enqueue_redis(claimed=True)

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
        redis = _enqueue_redis(claimed=True)
        redis_getter.return_value = redis

        with pytest.raises(Exception, match="NATS error"):
            await queue_render("test-device", "message", "user", "2024-01-16T21:46:05Z")

    redis.delete.assert_awaited_once_with("test-device_queued")


@pytest.mark.asyncio
async def test_redis_deduplication_functions():
    """The queued flag is checked and set in one Redis command."""
    with patch("nv_config_manager.render.events.util._get_queue_redis_client") as redis_getter:
        redis = MagicMock()
        redis.redis.set = AsyncMock(side_effect=[True, None])
        redis.delete = AsyncMock()
        redis_getter.return_value = redis

        assert await claim_queued("device") is True
        assert await claim_queued("device") is False
        await clear_queued("device")

    redis.redis.set.assert_awaited_with("device_queued", 1, nx=True, ex=60)
    redis.delete.assert_awaited_once_with("device_queued")


@pytest.mark.asyncio
async def test_claim_queued_always_claims_without_redis():
    """Local environments have no Redis, so every request is published."""
    with patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None):
        assert await claim_queued("device") is True


def _enqueue_redis(claimed: bool, queued: bool = False) -> MagicMock:
    redis = MagicMock()
    redis.redis.exists = AsyncMock(return_value=int(queued))
    redis.redis.set = AsyncMock(return_value=True if claimed else None)
    redis.delete = AsyncMock()
    return redis


def _jetstream() -> MagicMock:
    jetstream = MagicMock()
    jetstream.publish = AsyncMock()
    return jetstream


def _enable_device(dcim_client: AsyncMock) -> None:
    dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )


@pytest.mark.asyncio
async def test_enqueue_skips_known_duplicate_before_dcim_lookup(mock_dcim_client, custom_ini):
    """A device that is already queued is skipped without a DCIM status lookup."""
    custom_ini("[nats]\n")
    _enable_device(mock_dcim_client)
    redis = _enqueue_redis(claimed=True, queued=True)
    jetstream = _jetstream()

    result = await _process_single_device_enqueue(
        "device", "msg", "user", "ts", jetstream, MagicMock(), redis, mock_dcim_client
    )

    assert result is EnqueueResult.ALREADY_QUEUED
    redis.redis.exists.assert_awaited_once_with("device_queued")
    mock_dcim_client.get_render_device_status.assert_not_awaited()
    redis.redis.set.assert_not_awaited()
    jetstream.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_queued_is_false_without_redis():
    """Local environments have no Redis, so nothing is ever considered queued."""
    with patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None):
        assert await is_queued("device") is False


@pytest.mark.asyncio
async def test_enqueue_skips_device_already_queued(mock_dcim_client, custom_ini):
    """A producer that loses the claim to a concurrent one does not publish again."""
    custom_ini("[nats]\n")
    _enable_device(mock_dcim_client)
    redis = _enqueue_redis(claimed=False)
    jetstream = _jetstream()

    result = await _process_single_device_enqueue(
        "device", "msg", "user", "ts", jetstream, MagicMock(), redis, mock_dcim_client
    )

    assert result is EnqueueResult.ALREADY_QUEUED
    jetstream.publish.assert_not_awaited()
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_does_not_claim_disabled_device(mock_dcim_client, custom_ini):
    """A disabled device never takes the flag, so it cannot suppress a concurrent request."""
    custom_ini("[nats]\n")
    redis = _enqueue_redis(claimed=True)
    jetstream = _jetstream()

    result = await _process_single_device_enqueue(
        "device", "msg", "user", "ts", jetstream, MagicMock(), redis, mock_dcim_client
    )

    assert isinstance(result, dict)
    assert "not enabled" in result["error"]
    jetstream.publish.assert_not_awaited()
    redis.redis.set.assert_not_awaited()
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_another_producers_flag(mock_dcim_client, custom_ini):
    """An error before this producer claimed the device leaves the existing flag alone."""
    custom_ini("[nats]\n")
    _enable_device(mock_dcim_client)
    redis = _enqueue_redis(claimed=True)
    redis.redis.set.side_effect = ConnectionError("redis down")

    result = await _process_single_device_enqueue(
        "device", "msg", "user", "ts", _jetstream(), MagicMock(), redis, mock_dcim_client
    )

    assert isinstance(result, dict)
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_reraises_transient_dcim_failure(mock_dcim_client, custom_ini):
    """A DCIM outage during the status check is raised so the event can be retried."""
    custom_ini("[nats]\n")
    mock_dcim_client.get_render_device_status.side_effect = DCIMConnectivityError("down")
    redis = _enqueue_redis(claimed=True)

    with pytest.raises(DCIMConnectivityError):
        await _process_single_device_enqueue(
            "device", "msg", "user", "ts", _jetstream(), MagicMock(), redis, mock_dcim_client
        )
    redis.redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_releases_claim_before_reraising_transient_failure(
    mock_dcim_client, custom_ini
):
    """A publish that times out releases this producer's claim so a retry can publish."""
    custom_ini("[nats]\n")
    _enable_device(mock_dcim_client)
    redis = _enqueue_redis(claimed=True)
    jetstream = _jetstream()
    jetstream.publish.side_effect = TimeoutError()

    with pytest.raises(TimeoutError):
        await _process_single_device_enqueue(
            "device", "msg", "user", "ts", jetstream, MagicMock(), redis, mock_dcim_client
        )
    redis.delete.assert_awaited_once_with("device_queued")


@pytest.fixture
def batch_nats():
    """Give the batch path a shared NATS connection and no Redis."""
    connection = MagicMock()
    connection.is_closed = False
    connection.jetstream = MagicMock(return_value=_jetstream())
    with (
        patch("nv_config_manager.render.events.util.NATSConnectionManager") as manager,
        patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None),
    ):
        manager.return_value.get_connection.return_value = connection
        yield connection


def _status_or_outage(device_uuid: str) -> RenderDeviceStatus:
    if device_uuid == "device-b":
        raise DCIMConnectivityError("DCIM unreachable")
    return RenderDeviceStatus(render_enabled=True, is_aggregate_managed=False)


@pytest.mark.asyncio
async def test_batch_reports_transient_failure_by_device(batch_nats, mock_dcim_client, custom_ini):
    """Callers that do not retry see the failed device rather than an exception."""
    custom_ini("[nats]\n")
    mock_dcim_client.get_render_device_status.side_effect = _status_or_outage

    queued, failed = await queue_render_batch(
        ["device-a", "device-b"], "msg", "user", "ts", dcim_client=mock_dcim_client
    )

    assert queued == 1
    assert failed == [{"device_uuid": "device-b", "error": "DCIM unreachable"}]


@pytest.mark.asyncio
async def test_batch_raises_transient_failure_when_asked(batch_nats, mock_dcim_client, custom_ini):
    """The event dispatcher has the whole batch retried after a DCIM outage."""
    custom_ini("[nats]\n")
    mock_dcim_client.get_render_device_status.side_effect = _status_or_outage

    with pytest.raises(DCIMConnectivityError):
        await queue_render_batch(
            ["device-a", "device-b"],
            "msg",
            "user",
            "ts",
            dcim_client=mock_dcim_client,
            raise_transient_errors=True,
        )
    batch_nats.jetstream.return_value.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_queue_render_does_not_report_skipped_duplicate_as_published(
    mock_dcim_client, mock_nats_connection, custom_ini
):
    """The publish log line only appears when a message was actually published."""
    custom_ini("[nats]\n")
    _enable_device(mock_dcim_client)
    logger = MagicMock()
    with (
        patch(
            "nv_config_manager.render.events.util._get_queue_redis_client",
            return_value=_enqueue_redis(claimed=False),
        ),
        patch("nv_config_manager.render.events.util.get_logger", return_value=logger),
    ):
        await queue_render("device", "message", "user", "2024-01-16T21:46:05Z")

    mock_nats_connection.jetstream.return_value.publish.assert_not_awaited()
    published = [c for c in logger.info.call_args_list if "Published" in c.args[0]]
    assert published == []
