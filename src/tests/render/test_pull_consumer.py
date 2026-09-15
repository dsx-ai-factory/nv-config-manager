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
"""Tests for the NATS pull consumer module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import nats.errors
import pytest
from nats.aio.msg import Msg
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from redis.asyncio.lock import Lock as AsyncRedisLock

from nv_config_manager.render.events.util import DeviceNotEnabledError
from nv_config_manager.render.pull_consumer import (
    CONSUMER_ACK_PENDING,
    CONSUMER_PENDING,
    CONSUMER_REDELIVERED,
    CONSUMER_WAITING,
    PullConsumer,
    PullDCIMConsumer,
    PullDeviceChangeConsumer,
    PullNautobotConsumer,
)

# Test NATS configuration - mock credentials for testing only
TEST_NATS_CONFIG = """
[nats]
server=nats://ruser:T0pS3cr3t@nats.nv-config-manager-render-service.svc.cluster.local:4222
queue=nv-config-manager-render-service-local
password=T0pS3cr3t  # NOSONAR - mock password for testing
auth_method=password
local=false
config_manager_stream=nv-config-manager
config_manager_api_prefix=$JS.API
config_manager_consumer_name=nv-config-manager-device
config_manager_subjects=nv-config-manager.nautobotchange,nv-config-manager.devicechange,nv-config-manager.workflow.result
render_change_stream=nv-config-manager
render_change_subject=nv-config-manager.nautobotchange
device_change_stream=nv-config-manager
device_change_subject=nv-config-manager.devicechange
nautobot_stream=nautobot
nautobot_api_prefix=$JS.CUSTOM.API
nautobot_consumer_name=nv-config-manager-nautobot
nautobot_subjects=nautobot
nautobot_subject=nautobot
"""


@pytest.fixture
def mock_dispatcher():
    """Mock event dispatcher."""
    with patch("nv_config_manager.render.pull_consumer.EventDispatcher") as mock:
        mock_instance = MagicMock()
        mock_instance.dcim_event_dispatch = AsyncMock()
        mock_instance.nautobot_event_dispatch = AsyncMock()
        mock_instance.nautobot_change_dispatch = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_lock():
    """Mock async lock."""
    with patch("nv_config_manager.render.pull_consumer.create_lock") as mock:
        mock_lock_instance = MagicMock(spec=AsyncRedisLock)
        mock_lock_instance.acquire = AsyncMock(return_value=True)
        mock_lock_instance.release = AsyncMock(return_value=True)
        # create_lock is now async, so we need an AsyncMock
        mock.return_value = mock_lock_instance
        yield mock_lock_instance


@pytest.fixture
def mock_jetstream():
    """Mock JetStream context."""
    mock_js = MagicMock()
    mock_js.pull_subscribe = AsyncMock()
    mock_js.pull_subscribe_bind = AsyncMock()
    mock_js.consumer_info = AsyncMock()
    mock_js.add_consumer = AsyncMock()
    mock_js.delete_consumer = AsyncMock()
    return mock_js


@pytest.fixture
def mock_pull_subscription():
    """Mock pull subscription."""
    mock_sub = MagicMock()
    mock_sub.fetch = AsyncMock()
    mock_sub.unsubscribe = AsyncMock()
    return mock_sub


@pytest.mark.asyncio
async def test_pull_consumer_initialization(custom_ini):
    """Test pull consumer initialization."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    assert consumer.stream == "test_stream"
    assert consumer.subject == "test_subject"
    assert "test_queue" in consumer.queue
    assert consumer.running is False
    assert consumer.heartbeat_interval == 1.0
    assert consumer.idle_wait == 1.0
    assert consumer.error_backoff == 2.0


@pytest.mark.asyncio
async def test_pull_consumer_can_process_message(custom_ini):
    """Test default can_process_message behavior."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    # Default implementation should always return True
    assert await consumer.can_process_message() is True


def test_render_consumer_metrics_are_labeled_by_fixed_consumer(custom_ini, monkeypatch):
    """Render exports consumer state without relying on a NATS monitoring exporter."""
    custom_ini(TEST_NATS_CONFIG)
    monkeypatch.setenv("NV_CONFIG_MANAGER_K8S_NAMESPACE", "site-a")
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    info = MagicMock(
        num_pending=12,
        num_ack_pending=3,
        num_redelivered=2,
        num_waiting=4,
    )

    consumer._record_consumer_metrics(info)

    labels = (consumer.queue, consumer.stream, "site-a")
    assert CONSUMER_PENDING.labels(*labels)._value.get() == 12
    assert CONSUMER_ACK_PENDING.labels(*labels)._value.get() == 3
    assert CONSUMER_REDELIVERED.labels(*labels)._value.get() == 2
    assert CONSUMER_WAITING.labels(*labels)._value.get() == 4

    consumer._remove_consumer_metrics()

    for metric in (
        CONSUMER_PENDING,
        CONSUMER_ACK_PENDING,
        CONSUMER_REDELIVERED,
        CONSUMER_WAITING,
    ):
        assert labels not in metric._metrics


def test_consumer_metrics_can_be_removed_before_first_sample(custom_ini):
    """A failed initial lookup must not terminate the metrics task."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="never-recorded-stream",
        subject="never-recorded-subject",
        queue_suffix="never-recorded-consumer",
    )

    consumer._remove_consumer_metrics()


@pytest.mark.asyncio
async def test_pull_consumer_message_handler_not_implemented(custom_ini):
    """Test that message_handler raises NotImplementedError in base class."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)

    with pytest.raises(NotImplementedError):
        await consumer.message_handler(mock_msg)


@pytest.mark.asyncio
async def test_pull_nautobot_consumer_initialization(custom_ini):
    """Test PullNautobotConsumer initialization."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullNautobotConsumer()
    assert consumer.stream == "nautobot"
    assert consumer.subject == "nautobot"
    assert consumer.api_prefix == "$JS.CUSTOM.API"
    assert "nautobot" in consumer.queue


@pytest.mark.asyncio
async def test_pull_consumer_uses_configured_durable_name(custom_ini):
    """Each stream can provide its exact authorized durable name."""
    configured = TEST_NATS_CONFIG.replace(
        "nautobot_consumer_name=nv-config-manager-nautobot",
        "nautobot_consumer_name=externally-owned-nautobot",
    )
    custom_ini(configured)

    consumer = PullNautobotConsumer()

    assert consumer.queue == "externally-owned-nautobot"


@pytest.mark.asyncio
async def test_pull_dcim_consumer_uses_generic_event_configuration(custom_ini):
    """The generic consumer retains legacy stream and durable settings during migration."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDCIMConsumer()

    assert consumer.stream == "nautobot"
    assert consumer.subject == "nautobot"
    assert consumer.queue == "nv-config-manager-nautobot"
    assert consumer.api_prefix == "$JS.CUSTOM.API"


@pytest.mark.asyncio
async def test_pull_dcim_consumer_normalizes_before_dispatch(mock_dispatcher, custom_ini):
    """Render routing only receives the provider-neutral event representation."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDCIMConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"provider": "synthetic"}).encode()
    mock_msg.ack = AsyncMock()
    normalized_event = MagicMock()

    with patch(
        "nv_config_manager.render.pull_consumer.normalize_dcim_event",
        return_value=normalized_event,
    ) as normalize:
        await consumer.message_handler(mock_msg)

    normalize.assert_called_once_with({"provider": "synthetic"})
    mock_dispatcher.dcim_event_dispatch.assert_awaited_once_with(normalized_event)
    mock_msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_pull_nautobot_consumer_message_handler_success(mock_dispatcher, custom_ini):
    """Test PullNautobotConsumer successful message handling."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullNautobotConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"test": "data"}).encode()
    mock_msg.ack = AsyncMock()

    normalized_event = MagicMock()
    with patch(
        "nv_config_manager.render.pull_consumer.normalize_dcim_event",
        return_value=normalized_event,
    ):
        await consumer.message_handler(mock_msg)

    mock_dispatcher.dcim_event_dispatch.assert_awaited_once_with(normalized_event)
    mock_msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_pull_nautobot_consumer_device_not_enabled_error(mock_dispatcher, custom_ini):
    """Test PullNautobotConsumer handles DeviceNotEnabledError."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullNautobotConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"test": "data"}).encode()
    mock_msg.ack = AsyncMock()

    # Make dispatcher raise DeviceNotEnabledError
    mock_dispatcher.dcim_event_dispatch.side_effect = DeviceNotEnabledError("Device not enabled")

    normalized_event = MagicMock()
    with patch(
        "nv_config_manager.render.pull_consumer.normalize_dcim_event",
        return_value=normalized_event,
    ):
        await consumer.message_handler(mock_msg)

    mock_dispatcher.dcim_event_dispatch.assert_awaited_once_with(normalized_event)
    mock_msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_pull_nautobot_consumer_exception_handling(mock_dispatcher, custom_ini):
    """Test PullNautobotConsumer handles general exceptions."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullNautobotConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"test": "data"}).encode()
    mock_msg.nak = AsyncMock()

    # Make dispatcher raise a general exception
    mock_dispatcher.dcim_event_dispatch.side_effect = Exception("Processing error")

    normalized_event = MagicMock()
    with patch(
        "nv_config_manager.render.pull_consumer.normalize_dcim_event",
        return_value=normalized_event,
    ):
        await consumer.message_handler(mock_msg)

    mock_dispatcher.dcim_event_dispatch.assert_awaited_once_with(normalized_event)
    mock_msg.nak.assert_called_once()


@pytest.mark.asyncio
async def test_pull_device_change_consumer_initialization(custom_ini):
    """Test PullDeviceChangeConsumer initialization."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDeviceChangeConsumer()
    assert consumer.stream == "nv-config-manager"
    assert consumer.subject == "nv-config-manager.nautobotchange"
    assert consumer.api_prefix == "$JS.API"
    assert "device" in consumer.queue


@pytest.mark.asyncio
async def test_pull_consumer_uses_configured_api_prefix(custom_ini):
    """Test that pull consumers create JetStream context with the configured API prefix."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
        api_prefix="$JS.CUSTOM.API",
    )
    mock_conn = MagicMock()
    mock_conn.is_closed = True
    mock_conn.jetstream.return_value = MagicMock()

    with (
        patch(
            "nv_config_manager.render.pull_consumer.nats_connection", new_callable=AsyncMock
        ) as mock_nats_connection,
        patch.object(consumer, "_run_pull_consumer", new_callable=AsyncMock),
    ):
        mock_nats_connection.return_value = mock_conn

        await consumer.main()

    mock_conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")


@pytest.mark.asyncio
async def test_pull_device_change_consumer_success(mock_dispatcher, mock_lock, custom_ini):
    """Test PullDeviceChangeConsumer successful message handling."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDeviceChangeConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"device_id": "test-device"}).encode()
    mock_msg.ack = AsyncMock()
    mock_lock.acquire.return_value = True

    with patch(
        "nv_config_manager.render.pull_consumer.clear_queued", new_callable=AsyncMock
    ) as mock_clear_queued:
        # Mock the async dispatch method
        mock_dispatcher.nautobot_change_dispatch = AsyncMock()
        await consumer.message_handler(mock_msg)

    mock_clear_queued.assert_awaited_once_with("test-device")
    mock_msg.ack.assert_called_once()
    mock_dispatcher.nautobot_change_dispatch.assert_called_once_with({"device_id": "test-device"})
    mock_lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_pull_device_change_consumer_lock_failure(mock_dispatcher, mock_lock, custom_ini):
    """Test PullDeviceChangeConsumer when lock cannot be acquired."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDeviceChangeConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"device_id": "test-device"}).encode()
    mock_msg.nak = AsyncMock()
    mock_lock.acquire.return_value = False

    with patch(
        "nv_config_manager.render.pull_consumer.clear_queued", new_callable=AsyncMock
    ) as mock_clear_queued:
        await consumer.message_handler(mock_msg)

    # clear_queued should NOT be called when lock acquisition fails
    mock_clear_queued.assert_not_awaited()
    mock_msg.nak.assert_called_once_with(delay=5)
    # Lock release should NOT be called since acquire failed
    mock_lock.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_pull_device_change_consumer_render_exception(mock_dispatcher, mock_lock, custom_ini):
    """Test PullDeviceChangeConsumer handles RenderException."""
    custom_ini(TEST_NATS_CONFIG)
    from nv_config_manager.render.exceptions import RenderException

    consumer = PullDeviceChangeConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"device_id": "test-device"}).encode()
    mock_msg.ack = AsyncMock()
    mock_lock.acquire.return_value = True

    with patch("nv_config_manager.render.pull_consumer.clear_queued", new_callable=AsyncMock):
        # Mock the async dispatch method to raise exception
        mock_dispatcher.nautobot_change_dispatch = AsyncMock(
            side_effect=RenderException("Render failed")
        )
        await consumer.message_handler(mock_msg)

    # Should still ack the message for RenderException
    mock_msg.ack.assert_called_once()
    mock_lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_pull_device_change_consumer_general_exception(
    mock_dispatcher, mock_lock, custom_ini
):
    """Test PullDeviceChangeConsumer handles general exceptions."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullDeviceChangeConsumer()
    mock_msg = AsyncMock(spec=Msg)
    mock_msg.data = json.dumps({"device_id": "test-device"}).encode()
    mock_msg.nak = AsyncMock()
    mock_lock.acquire.return_value = True

    with patch("nv_config_manager.render.pull_consumer.clear_queued", new_callable=AsyncMock):
        # Mock the async dispatch method to raise exception
        mock_dispatcher.nautobot_change_dispatch = AsyncMock(
            side_effect=Exception("Processing error")
        )
        await consumer.message_handler(mock_msg)

    # Should nak the message for general exception
    mock_msg.nak.assert_called_once()
    mock_lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_resilient_message_handler_success(custom_ini):
    """Test _resilient_message_handler with successful processing."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)

    # Mock the message_handler method to succeed
    consumer.message_handler = AsyncMock()

    await consumer._resilient_message_handler(mock_msg)

    consumer.message_handler.assert_called_once_with(mock_msg)


@pytest.mark.asyncio
async def test_resilient_message_handler_consumer_error(custom_ini):
    """Test _resilient_message_handler naks message on any error."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)

    # Mock the message_handler method to raise any error
    consumer.message_handler = AsyncMock(side_effect=Exception("processing error"))
    consumer.nak = AsyncMock()

    # Should not raise, should nak the message instead
    await consumer._resilient_message_handler(mock_msg)

    consumer.nak.assert_called_once_with(mock_msg)


@pytest.mark.asyncio
async def test_resilient_message_handler_general_error(custom_ini):
    """Test _resilient_message_handler with general error."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.nak = AsyncMock()

    # Mock the message_handler method to raise a general error
    consumer.message_handler = AsyncMock(side_effect=Exception("general error"))

    await consumer._resilient_message_handler(mock_msg)

    # Should attempt to nak the message
    mock_msg.nak.assert_called_once()


@pytest.mark.asyncio
async def test_missing_consumer_is_created(custom_ini, mock_jetstream):
    """Runtime creates the exact durable authorized by the NATS owner."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    consumer.jetstream = mock_jetstream
    mock_jetstream.consumer_info.side_effect = NotFoundError

    await consumer._ensure_consumer_exists()

    mock_jetstream.consumer_info.assert_called_once_with("test_stream", consumer.queue)
    mock_jetstream.add_consumer.assert_awaited_once()
    stream = mock_jetstream.add_consumer.await_args.kwargs["stream"]
    config = mock_jetstream.add_consumer.await_args.kwargs["config"]
    assert stream == "test_stream"
    assert config.durable_name == consumer.queue
    assert config.filter_subject == "test_subject"
    assert config.deliver_policy == DeliverPolicy.NEW
    mock_jetstream.delete_consumer.assert_not_called()


@pytest.mark.asyncio
async def test_existing_consumer_is_not_modified(custom_ini, mock_jetstream):
    """An existing durable retains its established delivery position and policy."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    consumer.jetstream = mock_jetstream

    await consumer._ensure_consumer_exists()

    mock_jetstream.add_consumer.assert_not_called()
    mock_jetstream.delete_consumer.assert_not_called()


@pytest.mark.asyncio
async def test_existing_consumer_mismatch_logs_admin_update_request(custom_ini, mock_jetstream):
    """An externally managed consumer mismatch produces an actionable request."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    consumer.jetstream = mock_jetstream
    consumer.logger = MagicMock()
    existing = MagicMock()
    existing.config = ConsumerConfig(
        durable_name=consumer.queue,
        filter_subject="wrong.subject",
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.NONE,
        ack_wait=30,
        max_deliver=5,
    )
    mock_jetstream.consumer_info.return_value = existing

    await consumer._ensure_consumer_exists()

    warning = consumer.logger.warning.call_args.args
    assert "filter_subject" in warning[2]
    assert "ack_policy" in warning[2]
    assert "ack_wait" in warning[2]
    assert "max_deliver" in warning[2]
    assert "Ask the NATS administrator" in warning[3]
    assert "Delivery policy may retain" in warning[3]


@pytest.mark.asyncio
async def test_missing_consumer_permission_error_logs_provision_request(custom_ini, mock_jetstream):
    """Restricted accounts tell operators exactly how to provision the durable."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
        api_prefix="$JS.IMPORTED.API",
    )
    consumer.jetstream = mock_jetstream
    consumer.logger = MagicMock()
    mock_jetstream.consumer_info.side_effect = NotFoundError

    async def denied_creation(*_args, **_kwargs):
        consumer._last_permission_error = nats.errors.Error(
            "nats: permissions violation for publish"
        )
        raise nats.errors.TimeoutError

    mock_jetstream.add_consumer.side_effect = denied_creation

    with pytest.raises(nats.errors.TimeoutError):
        await consumer._ensure_consumer_exists()

    message = consumer.logger.error.call_args.args[2]
    assert "$JS.IMPORTED.API.CONSUMER.DURABLE.CREATE.test_stream" in message
    assert "deliver_policy='new'" in message
    assert "filter_subject='test_subject'" in message


@pytest.mark.asyncio
async def test_consumer_lookup_propagates_transient_errors(custom_ini, mock_jetstream):
    """A transient lookup failure retries without attempting creation."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )
    consumer.jetstream = mock_jetstream

    mock_jetstream.consumer_info.side_effect = TimeoutError("transient")

    with pytest.raises(TimeoutError):
        await consumer._ensure_consumer_exists()

    mock_jetstream.add_consumer.assert_not_called()
    mock_jetstream.delete_consumer.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_cycle_binds_stream_explicitly(
    custom_ini, mock_jetstream, mock_pull_subscription
):
    """Naming the stream avoids a $JS.API.STREAM.NAMES lookup, which is not exported
    across NATS account boundaries."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="nautobot",
        api_prefix="$JS.EXTERNAL.API",
    )
    consumer.jetstream = mock_jetstream
    mock_jetstream.pull_subscribe_bind.return_value = mock_pull_subscription
    consumer.nats_conn = MagicMock()
    consumer.nats_conn.is_closed = False
    # Skip the message loop; this asserts only how the subscription is bound.
    consumer.running = False

    await consumer._run_consumer_cycle()

    mock_jetstream.pull_subscribe_bind.assert_awaited_once_with(
        consumer=consumer.queue, stream="nautobot"
    )
    mock_jetstream.pull_subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_resilient_ack_success(custom_ini):
    """Test resilient_ack with successful acknowledgment."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.ack = AsyncMock()

    await consumer.ack(mock_msg)

    mock_msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_resilient_ack_no_responders_error(custom_ini):
    """Test resilient_ack handles NoRespondersError gracefully."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.ack = AsyncMock(side_effect=nats.errors.NoRespondersError("no responders"))

    # Should not raise an exception
    await consumer.ack(mock_msg)

    mock_msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_resilient_ack_other_error(custom_ini):
    """Test resilient_ack re-raises other errors."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.ack = AsyncMock(side_effect=Exception("other error"))

    with pytest.raises(Exception, match="other error"):
        await consumer.ack(mock_msg)


@pytest.mark.asyncio
async def test_resilient_nak_success(custom_ini):
    """Test resilient_nak with successful negative acknowledgment."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.nak = AsyncMock()

    await consumer.nak(mock_msg)

    mock_msg.nak.assert_called_once()


@pytest.mark.asyncio
async def test_resilient_nak_with_delay(custom_ini):
    """Test resilient_nak with delay parameter."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.nak = AsyncMock()

    await consumer.nak(mock_msg, delay=10)

    mock_msg.nak.assert_called_once_with(delay=10)


@pytest.mark.asyncio
async def test_resilient_nak_no_responders_error(custom_ini):
    """Test resilient_nak handles NoRespondersError gracefully."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.nak = AsyncMock(side_effect=nats.errors.NoRespondersError("no responders"))

    # Should not raise an exception
    await consumer.nak(mock_msg)

    mock_msg.nak.assert_called_once()


@pytest.mark.asyncio
async def test_resilient_nak_other_error(custom_ini):
    """Test resilient_nak does re-raise other errors."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    mock_msg = AsyncMock(spec=Msg)
    mock_msg.nak = AsyncMock(side_effect=Exception("other error"))

    with pytest.raises(Exception, match="other error"):
        await consumer.nak(mock_msg)


@pytest.mark.asyncio
async def test_heartbeat_configuration(custom_ini):
    """Test that consumer is configured with heartbeat interval."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    # Check that heartbeat interval is configured
    assert consumer.heartbeat_interval == 1.0


@pytest.mark.asyncio
async def test_simplified_fetch_with_heartbeat_no_timeout(custom_ini):
    """Test that fetch uses heartbeat without timeout for simplified error handling."""
    custom_ini(TEST_NATS_CONFIG)
    consumer = PullConsumer(
        stream="test_stream",
        subject="test_subject",
        queue_suffix="test_queue",
    )

    # Verify heartbeat interval is configured
    assert consumer.heartbeat_interval == 1.0
    # Verify timeout was removed
    assert not hasattr(consumer, "pull_timeout")
