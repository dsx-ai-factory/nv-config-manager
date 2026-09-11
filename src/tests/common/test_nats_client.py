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

from nv_config_manager.common.client import (
    DEFAULT_NATS_API_PREFIX,
    NatsClient,
    NatsConsumer,
    NatsProducer,
)

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
            "nv_config_manager.common.client.nats.nats.connect", new=AsyncMock(return_value=conn)
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
            "nv_config_manager.common.client.nats.nats.connect", new=AsyncMock(return_value=conn)
        ),
        patch.object(client, "_ensure_stream", new_callable=AsyncMock) as ensure_stream,
    ):
        await client.connect()

    ensure_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_preserves_password_auth_and_tls_contract():
    """Password-authenticated connections pass credentials and the configured TLS context."""
    client = NatsClient(
        server=TEST_SERVER,
        auth_method="password",
        user="nats-user",
        password="nats-password",
    )
    conn = MagicMock(connected_url=TEST_SERVER)

    with patch(
        "nv_config_manager.common.client.nats.nats.connect",
        new=AsyncMock(return_value=conn),
    ) as connect:
        await client.connect()

    connect.assert_awaited_once()
    (server,) = connect.await_args.args
    options = connect.await_args.kwargs
    assert server == TEST_SERVER
    assert options["user"] == "nats-user"
    assert options["password"] == "nats-password"
    assert options["tls"] is client.ssl_context
    assert "user_credentials" not in options


@pytest.mark.asyncio
async def test_connect_preserves_jwt_auth_contract():
    """JWT connections pass the credentials file instead of password fields."""
    client = NatsClient(
        server=TEST_SERVER,
        auth_method="JWT",
        user="ignored-user",
        password="ignored-password",
        creds_path="/secrets/nats.creds",
    )
    conn = MagicMock(connected_url=TEST_SERVER)

    with patch(
        "nv_config_manager.common.client.nats.nats.connect",
        new=AsyncMock(return_value=conn),
    ) as connect:
        await client.connect()

    options = connect.await_args.kwargs
    assert options["user_credentials"] == "/secrets/nats.creds"
    assert "user" not in options
    assert "password" not in options
    assert options["tls"] is client.ssl_context


@pytest.mark.asyncio
async def test_ensure_stream_uses_configured_api_prefix():
    """Stream lookups go through the account's rewritten API prefix."""
    client = NatsClient(server=TEST_SERVER, api_prefix="$JS.CUSTOM.API")
    client.conn = MagicMock()
    client.conn.jetstream.return_value.stream_info = AsyncMock()

    await client._ensure_stream()

    client.conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")


@pytest.mark.asyncio
async def test_local_client_creates_missing_default_stream():
    """Local deployments retain automatic creation of the configured stream."""
    client = NatsClient(
        server=TEST_SERVER,
        local=True,
        default_stream_name="workflow-events",
        default_stream_subjects=["workflow.>"],
    )
    client.conn = MagicMock()
    jetstream = client.conn.jetstream.return_value
    stream_info = MagicMock()
    jetstream.stream_info = AsyncMock(side_effect=[NotFoundError, stream_info])
    jetstream.add_stream = AsyncMock()

    await client._ensure_stream()

    jetstream.add_stream.assert_awaited_once_with(
        name="workflow-events",
        subjects=["workflow.>"],
    )
    assert jetstream.stream_info.await_count == 2
    assert client.stream_info is stream_info


@pytest.mark.asyncio
async def test_producer_publishes_utf8_to_default_stream_and_api_prefix():
    """Producer publishing freezes payload encoding, stream selection, and API prefix."""
    producer = NatsProducer(
        server=TEST_SERVER,
        default_stream_name="workflow-events",
        api_prefix="$JS.CUSTOM.API",
    )
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    jetstream = conn.jetstream.return_value
    jetstream.publish = AsyncMock()

    with patch.object(producer, "connect", new=AsyncMock(return_value=conn)):
        await producer.publish("workflow.result", "café")

    conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")
    jetstream.publish.assert_awaited_once_with(
        subject="workflow.result",
        payload="café".encode(),
        stream="workflow-events",
    )


@pytest.mark.asyncio
async def test_producer_allows_per_publish_stream_override():
    """An explicit publish stream continues to override the configured default."""
    producer = NatsProducer(server=TEST_SERVER, default_stream_name="default-stream")
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    jetstream = conn.jetstream.return_value
    jetstream.publish = AsyncMock()

    with patch.object(producer, "connect", new=AsyncMock(return_value=conn)):
        await producer.publish("workflow.result", "payload", stream="override-stream")

    jetstream.publish.assert_awaited_once_with(
        subject="workflow.result",
        payload=b"payload",
        stream="override-stream",
    )


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
    type(conn).is_closed = PropertyMock(side_effect=[False, True])
    conn.jetstream.return_value.consumer_info = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    conn.jetstream.return_value.subscribe_bind = AsyncMock()

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch(
            "nv_config_manager.common.client.nats.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        await consumer.main()

    sleep.assert_awaited_once_with(1)
