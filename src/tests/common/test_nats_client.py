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
"""Tests for JetStream API prefix handling in the base NATS clients."""

from configparser import ConfigParser
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from nats.js.api import AckPolicy, DeliverPolicy
from nats.js.errors import NotFoundError

from nv_config_manager.common.client import DEFAULT_NATS_API_PREFIX, NatsClient, NatsConsumer
from nv_config_manager.common.config import nats_connection

TEST_SERVER = "nats://nats.example.local:4222"


def _config(**overrides: str) -> ConfigParser:
    config = ConfigParser()
    config["nats"] = {
        "server": TEST_SERVER,
        "queue": "nv-config-manager",
        "config_manager_stream": "nv-config-manager",
        **overrides,
    }
    return config


def test_client_defaults_to_standard_api_prefix():
    """A client with no configured prefix uses the JetStream default."""
    client = NatsClient(server=TEST_SERVER)
    assert client.api_prefix == DEFAULT_NATS_API_PREFIX == "$JS.API"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["password", "JWT"])
@pytest.mark.parametrize("scheme,local", [("tls", False), ("wss", False), ("nats", True)])
async def test_render_connection_tls_policy(auth_method: str, scheme: str, local: bool) -> None:
    """Render uses TLS-first only for native TLS endpoints."""
    server = f"{scheme}://nats.example.test:4222"
    config = _config(
        server=server,
        local=str(local),
        auth_method=auth_method,
        user="test-user",
        password="test-password",
        credentials="/test/user.creds",
    )
    conn = MagicMock()
    conn.jetstream.return_value.stream_info = AsyncMock()
    reconnected = AsyncMock()
    with (
        patch("nv_config_manager.common.config.loader.load_config", return_value=config),
        patch(
            "nv_config_manager.common.config.nats.connect", new=AsyncMock(return_value=conn)
        ) as connect,
    ):
        assert await nats_connection(reconnected_cb=reconnected) is conn

    assert connect.await_args.args == (server,)
    options = connect.await_args.kwargs
    if scheme == "tls":
        assert options["tls_handshake_first"] is True
    else:
        assert "tls_handshake_first" not in options
    assert options["tls"].check_hostname
    assert options["reconnected_cb"] is reconnected
    assert options["allow_reconnect"] is True
    if auth_method == "JWT":
        assert options["user_credentials"] == "/test/user.creds"
    else:
        assert options["user"] == "test-user"
        assert options["password"] == "test-password"
    assert conn.jetstream.return_value.stream_info.await_count == (2 if local else 0)


def test_from_config_reads_config_manager_api_prefix():
    """from_config picks up the config-manager account prefix."""
    client = NatsClient.from_config(_config(config_manager_api_prefix="$JS.CUSTOM.API"))
    assert client.api_prefix == "$JS.CUSTOM.API"


def test_from_config_without_prefix_falls_back_to_default():
    """An absent prefix key leaves the client on the default prefix."""
    assert NatsClient.from_config(_config()).api_prefix == DEFAULT_NATS_API_PREFIX


@pytest.mark.asyncio
async def test_external_connect_does_not_require_stream_info():
    """Externally managed streams can be used without stream administration permission."""
    client = NatsClient(server=TEST_SERVER, local=False)
    conn = MagicMock(connected_url=TEST_SERVER)

    with (
        patch(
            "nv_config_manager_infrastructure.nats.client.nats.connect",
            new=AsyncMock(return_value=conn),
        ),
        patch.object(client, "_ensure_stream", new_callable=AsyncMock) as ensure_stream,
    ):
        await client.connect()

    ensure_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_connect_keeps_stream_setup():
    """Bundled/local clients retain automatic stream setup."""
    client = NatsClient(server=TEST_SERVER, local=True)
    conn = MagicMock(connected_url=TEST_SERVER)

    with (
        patch(
            "nv_config_manager_infrastructure.nats.client.nats.connect",
            new=AsyncMock(return_value=conn),
        ),
        patch.object(client, "_ensure_stream", new_callable=AsyncMock) as ensure_stream,
    ):
        await client.connect()

    ensure_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_stream_uses_configured_api_prefix():
    """Stream lookups go through the account's rewritten API prefix."""
    client = NatsClient(server=TEST_SERVER, api_prefix="$JS.CUSTOM.API")
    client.conn = MagicMock()
    client.conn.jetstream.return_value.stream_info = AsyncMock()

    await client._ensure_stream()

    client.conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")


@pytest.mark.asyncio
async def test_consumer_binds_existing_durable_with_configured_api_prefix():
    """An existing push consumer is inspected and bound through its account prefix."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        api_prefix="$JS.CUSTOM.API",
        durable_name="nv-config-manager-archive",
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    conn.is_closed = True
    expected = consumer._expected_consumer_config()
    conn.jetstream.return_value.consumer_info = AsyncMock(return_value=MagicMock(config=expected))
    conn.jetstream.return_value.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")
    conn.jetstream.return_value.consumer_info.assert_awaited_once_with(
        "nautobot", "nv-config-manager-archive"
    )
    kwargs = conn.jetstream.return_value.subscribe_bind.await_args.kwargs
    assert kwargs["stream"] == "nautobot"
    assert kwargs["consumer"] == "nv-config-manager-archive"
    assert kwargs["cb"] is consumer.handler
    config = kwargs["config"]
    assert config.durable_name == config.deliver_group == "nv-config-manager-archive"
    assert config.deliver_subject == "nv-config-manager.archive.delivery"
    assert config.deliver_policy == DeliverPolicy.NEW
    assert config.ack_policy == AckPolicy.EXPLICIT
    assert config.ack_wait == 360
    assert config.max_deliver == -1


@pytest.mark.asyncio
async def test_consumer_creates_missing_durable_then_binds_it():
    """The runtime creates only its exact durable when it is absent."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        durable_name="nv-config-manager-archive",
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    created_info = MagicMock(config=consumer._expected_consumer_config())
    jetstream.consumer_info = AsyncMock(side_effect=NotFoundError)
    jetstream.add_consumer = AsyncMock(return_value=created_info)
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    jetstream.add_consumer.assert_awaited_once()
    create_kwargs = jetstream.add_consumer.await_args.kwargs
    assert create_kwargs["stream"] == "nv-config-manager"
    assert create_kwargs["config"].filter_subject == "nv-config-manager.workflow.result"
    jetstream.subscribe_bind.assert_awaited_once_with(
        stream="nv-config-manager",
        consumer="nv-config-manager-archive",
        config=created_info.config,
        cb=consumer.handler,
    )


@pytest.mark.asyncio
async def test_consumer_creation_race_binds_confirmed_durable():
    """A second replica winning creation does not prevent this replica from binding."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    existing_info = MagicMock(config=consumer._expected_consumer_config())
    jetstream.consumer_info = AsyncMock(side_effect=[NotFoundError, existing_info])
    jetstream.add_consumer = AsyncMock(side_effect=RuntimeError("consumer already exists"))
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    assert jetstream.consumer_info.await_count == 2
    jetstream.subscribe_bind.assert_awaited_once()


def test_consumer_mismatch_does_not_enforce_delivery_policy():
    """Migration delivery policy is accepted while operational drift is reported."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    config = consumer._expected_consumer_config()
    config.deliver_policy = DeliverPolicy.ALL
    assert consumer._consumer_configuration_mismatches(MagicMock(config=config)) == []

    config.ack_policy = AckPolicy.NONE
    assert "ack_policy" in consumer._consumer_configuration_mismatches(MagicMock(config=config))[0]


@pytest.mark.asyncio
async def test_consumer_stays_alive_after_subscribing():
    """The consumer loop remains active until the NATS connection closes."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    type(conn).is_closed = PropertyMock(side_effect=[False, True])
    conn.jetstream.return_value.consumer_info = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    conn.jetstream.return_value.subscribe_bind = AsyncMock()

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
    ):
        await consumer.main()

    sleep.assert_awaited_once_with(1)
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["ensure", "subscribe"])
async def test_consumer_closes_connection_when_startup_fails(failure_point):
    """A connection established before a startup failure is always closed."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    consumer_info = MagicMock(config=consumer._expected_consumer_config())
    startup_error = RuntimeError(f"{failure_point} failed")
    ensure_side_effect = startup_error if failure_point == "ensure" else None
    subscribe_side_effect = startup_error if failure_point == "subscribe" else None

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch.object(
            consumer,
            "_ensure_consumer",
            new_callable=AsyncMock,
            return_value=consumer_info,
            side_effect=ensure_side_effect,
        ),
    ):
        jetstream.subscribe_bind = AsyncMock(side_effect=subscribe_side_effect)
        with pytest.raises(RuntimeError, match=f"{failure_point} failed"):
            await consumer.main()

    conn.close.assert_awaited_once()


def test_consumer_run_closes_event_loop_when_main_fails():
    """The event loop is closed even when the consumer coroutine raises."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    loop = MagicMock()
    loop.run_until_complete.side_effect = RuntimeError("startup failed")

    with (
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.get_running_loop",
            side_effect=RuntimeError,
        ),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.new_event_loop",
            return_value=loop,
        ),
        patch.object(consumer, "main", new=MagicMock(return_value=MagicMock())),
        pytest.raises(RuntimeError, match="startup failed"),
    ):
        consumer.run()

    loop.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_consumer_run_rejects_running_event_loop():
    """The synchronous runner does not take ownership of a caller's event loop."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )

    with (
        patch.object(consumer, "main", new=MagicMock()) as main,
        pytest.raises(RuntimeError, match="cannot be called from a running event loop"),
    ):
        consumer.run()

    main.assert_not_called()
    assert consumer._loop is None
