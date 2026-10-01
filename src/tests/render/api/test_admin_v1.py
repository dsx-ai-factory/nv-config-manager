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
"""Tests for the admin v1 NATS consumer endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import nats.errors
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from nats.js.errors import NotFoundError

from nv_config_manager.render.api.admin_v1 import (
    ConsumerType,
    get_consumer_configs,
    get_consumer_info,
    reset_all_consumers,
    reset_consumer,
)
from nv_config_manager.render.api.main import app


class MockConsumerInfo:
    """Minimal consumer information returned by the mocked JetStream client."""

    def __init__(self, num_pending=0, num_ack_pending=0, consumer_seq=100):
        self.num_pending = num_pending
        self.num_ack_pending = num_ack_pending
        self.delivered = MagicMock(consumer_seq=consumer_seq)


def create_test_mocks():
    """Create config and NATS mocks shared by endpoint tests."""
    mock_config = MagicMock()
    mock_config.__getitem__.return_value = {
        "queue": "site-specific-queue",
        "config_manager_stream": "nv-config-manager",
        "config_manager_api_prefix": "$JS.API",
        "config_manager_consumer_name": "nv-config-manager-device",
        "render_change_stream": "nv-config-manager",
        "render_change_subject": "nv-config-manager.nautobotchange",
        "nautobot_stream": "nautobot",
        "nautobot_api_prefix": "$JS.EXTERNAL.API",
        "nautobot_consumer_name": "nv-config-manager-nautobot",
        "nautobot_subject": "nautobot",
    }

    connection = MagicMock()
    jetstream = MagicMock()
    connection.jetstream.return_value = jetstream
    connection.close = AsyncMock()

    async def get_connection(**_kwargs):
        return connection

    return mock_config, get_connection, connection, jetstream


@patch("nv_config_manager.render.api.admin_v1.load_config")
def test_consumer_configs_use_fixed_names_and_per_stream_prefixes(mock_load_config):
    """Consumer identity does not depend on the site's queue name."""
    mock_config, _, _, _ = create_test_mocks()
    mock_load_config.return_value = mock_config

    configs = get_consumer_configs()

    assert configs["device"] == {
        "durable_name": "nv-config-manager-device",
        "stream": "nv-config-manager",
        "subject": "nv-config-manager.nautobotchange",
        "api_prefix": "$JS.API",
    }
    assert configs["dcim"] == {
        "durable_name": "nv-config-manager-nautobot",
        "stream": "nautobot",
        "subject": "nautobot",
        "api_prefix": "$JS.EXTERNAL.API",
    }


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_list_consumers_uses_each_streams_prefix(mock_nats_connection, mock_load_config):
    """Consumer inspection follows the account import for each stream."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=10))

    response = TestClient(app).get("/v1/admin/consumers")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["consumers"]] == [
        "nv-config-manager-nautobot",
        "nv-config-manager-device",
    ]
    connection.jetstream.assert_any_call(prefix="$JS.EXTERNAL.API")
    connection.jetstream.assert_any_call(prefix="$JS.API")
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_get_consumer_info_not_found(mock_nats_connection, mock_load_config):
    """A missing fixed durable is reported as not found."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    jetstream.consumer_info = AsyncMock(side_effect=NotFoundError)

    response = TestClient(app).get("/v1/admin/consumers/device")

    assert response.status_code == 404
    assert "nv-config-manager-device" in response.json()["detail"]


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_reset_consumer_deletes_exact_durable(mock_nats_connection, mock_load_config):
    """Reset remains compatible with NATS 2.10 by deleting the authorized durable."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=50))
    jetstream.delete_consumer = AsyncMock()

    response = TestClient(app).delete("/v1/admin/consumers/dcim/reset")

    assert response.status_code == 200
    assert response.json()["consumer_name"] == "nv-config-manager-nautobot"
    assert "50 pending" in response.json()["message"]
    assert "if create permission is not granted" in response.json()["message"]
    jetstream.delete_consumer.assert_awaited_once_with(
        stream="nautobot", consumer="nv-config-manager-nautobot"
    )
    connection.jetstream.assert_called_once_with(prefix="$JS.EXTERNAL.API")
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config", side_effect=RuntimeError("bad config"))
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_list_consumers_closes_connection_on_failure(mock_nats_connection, _mock_load_config):
    """Unexpected failures after connect do not leak the NATS connection."""
    _, get_connection, connection, _ = create_test_mocks()
    mock_nats_connection.side_effect = get_connection

    response = TestClient(app).get("/v1/admin/consumers")

    assert response.status_code == 500
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_reset_missing_consumer_is_idempotent(mock_nats_connection, mock_load_config):
    """Repeated cleanup succeeds when the durable has already disappeared."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    jetstream.consumer_info = AsyncMock(side_effect=NotFoundError)
    jetstream.delete_consumer = AsyncMock(side_effect=NotFoundError)

    response = TestClient(app).delete("/v1/admin/consumers/device/reset")

    assert response.status_code == 200
    assert "already deleted" in response.json()["message"]
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
@pytest.mark.asyncio
async def test_reset_permission_error_returns_admin_instructions(
    mock_nats_connection, mock_load_config
):
    """A restricted account receives an actionable manual reset request."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    permission_callback = None

    async def get_denied_connection(**kwargs):
        nonlocal permission_callback
        permission_callback = kwargs["error_cb"]
        return await get_connection(**kwargs)

    mock_nats_connection.side_effect = get_denied_connection
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=50))

    async def denied_delete(*_args, **_kwargs):
        assert permission_callback is not None
        await permission_callback(nats.errors.Error("nats: permissions violation for publish"))
        raise nats.errors.TimeoutError

    jetstream.delete_consumer = AsyncMock(side_effect=denied_delete)

    with pytest.raises(HTTPException) as exc_info:
        await reset_consumer(ConsumerType.dcim, MagicMock())

    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert "$JS.EXTERNAL.API.CONSUMER.DELETE.nautobot.nv-config-manager-nautobot" in detail
    assert "delete consumer 'nv-config-manager-nautobot'" in detail
    assert "deliver_policy='new'" in detail
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_reset_all_deletes_both_fixed_durables(mock_nats_connection, mock_load_config):
    """Reset-all uses each stream prefix and deletes both exact names."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=5))
    jetstream.delete_consumer = AsyncMock()

    response = TestClient(app).delete("/v1/admin/consumers/reset-all")

    assert response.status_code == 200
    assert {result["consumer_name"] for result in response.json()} == {
        "nv-config-manager-device",
        "nv-config-manager-nautobot",
    }
    assert jetstream.delete_consumer.await_count == 2
    connection.jetstream.assert_any_call(prefix="$JS.EXTERNAL.API")
    connection.jetstream.assert_any_call(prefix="$JS.API")
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
@pytest.mark.asyncio
async def test_get_consumer_info_permission_error_returns_admin_instructions(
    mock_nats_connection, mock_load_config
):
    """A denied consumer lookup identifies the exact INFO permission needed."""
    mock_config, get_connection, connection, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    permission_callback = None

    async def get_denied_connection(**kwargs):
        nonlocal permission_callback
        permission_callback = kwargs["error_cb"]
        return await get_connection(**kwargs)

    async def denied_info(*_args, **_kwargs):
        assert permission_callback is not None
        await permission_callback(nats.errors.Error("nats: permissions violation for publish"))
        raise nats.errors.TimeoutError

    mock_nats_connection.side_effect = get_denied_connection
    jetstream.consumer_info = AsyncMock(side_effect=denied_info)

    with pytest.raises(HTTPException) as exc_info:
        await get_consumer_info(ConsumerType.device, MagicMock())

    assert exc_info.value.status_code == 403
    assert (
        "$JS.API.CONSUMER.INFO.nv-config-manager.nv-config-manager-device" in exc_info.value.detail
    )
    connection.close.assert_awaited_once()


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_reset_unknown_backlog_does_not_report_negative_count(
    mock_nats_connection, mock_load_config
):
    """Permission-limited inspection is reported as unavailable, not minus one."""
    mock_config, get_connection, _, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    permission_callback = None

    async def get_denied_connection(**kwargs):
        nonlocal permission_callback
        permission_callback = kwargs["error_cb"]
        return await get_connection(**kwargs)

    async def denied_info(*_args, **_kwargs):
        assert permission_callback is not None
        await permission_callback(nats.errors.Error("nats: permissions violation for publish"))
        raise nats.errors.TimeoutError

    mock_nats_connection.side_effect = get_denied_connection
    jetstream.consumer_info = AsyncMock(side_effect=denied_info)
    jetstream.delete_consumer = AsyncMock()

    response = TestClient(app).delete("/v1/admin/consumers/device/reset")

    assert response.status_code == 200
    assert "pending-message count was unavailable" in response.json()["message"]
    assert "-1" not in response.json()["message"]


@patch("nv_config_manager.render.api.admin_v1.jetstream_for_consumer")
@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
def test_reset_all_isolates_jetstream_context_failure(
    mock_nats_connection, mock_load_config, mock_jetstream_for_consumer
):
    """A context failure for one account does not prevent the other reset."""
    mock_config, get_connection, _, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    mock_nats_connection.side_effect = get_connection
    mock_jetstream_for_consumer.side_effect = [RuntimeError("bad import"), jetstream]
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=2))
    jetstream.delete_consumer = AsyncMock()

    response = TestClient(app).delete("/v1/admin/consumers/reset-all")

    assert response.status_code == 200
    assert [result["status"] for result in response.json()] == ["error", "success"]
    jetstream.delete_consumer.assert_awaited_once_with(
        stream="nv-config-manager", consumer="nv-config-manager-device"
    )


@patch("nv_config_manager.render.api.admin_v1.load_config")
@patch("nv_config_manager.render.api.admin_v1.nats_connection")
@pytest.mark.asyncio
async def test_reset_all_permission_error_is_per_consumer(mock_nats_connection, mock_load_config):
    """A denied deletion returns guidance while subsequent consumers still reset."""
    mock_config, get_connection, _, jetstream = create_test_mocks()
    mock_load_config.return_value = mock_config
    permission_callback = None

    async def get_denied_connection(**kwargs):
        nonlocal permission_callback
        permission_callback = kwargs["error_cb"]
        return await get_connection(**kwargs)

    delete_calls = 0

    async def delete_with_first_denied(*_args, **_kwargs):
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            assert permission_callback is not None
            await permission_callback(nats.errors.Error("nats: permissions violation for publish"))
            raise nats.errors.TimeoutError

    mock_nats_connection.side_effect = get_denied_connection
    jetstream.consumer_info = AsyncMock(return_value=MockConsumerInfo(num_pending=3))
    jetstream.delete_consumer = AsyncMock(side_effect=delete_with_first_denied)

    results = await reset_all_consumers(MagicMock())

    assert [result.status for result in results] == ["error", "success"]
    assert "$JS.EXTERNAL.API.CONSUMER.DELETE.nautobot" in results[0].message
