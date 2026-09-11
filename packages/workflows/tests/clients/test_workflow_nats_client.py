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
"""Contracts for the workflows package NATS JetStream producer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nats.js.errors import NotFoundError

from nv_config_manager_workflows.clients import NatsClient, NatsProducer

TEST_SERVER = "nats://nats.example:4222"


def test_producer_extends_the_shared_nats_client() -> None:
    assert issubclass(NatsProducer, NatsClient)


@pytest.mark.asyncio
async def test_connect_preserves_password_auth_and_tls() -> None:
    producer = NatsProducer(
        server=TEST_SERVER,
        user="nats-user",
        password="nats-password",
    )
    connection = MagicMock(connected_url=TEST_SERVER)

    with patch(
        "nv_config_manager_workflows.clients.nats.base.nats.connect",
        new=AsyncMock(return_value=connection),
    ) as connect:
        await producer.connect()

    connect.assert_awaited_once()
    (server,) = connect.await_args.args
    options = connect.await_args.kwargs
    assert server == TEST_SERVER
    assert options["user"] == "nats-user"
    assert options["password"] == "nats-password"
    assert options["tls"] is producer.ssl_context
    assert "user_credentials" not in options


@pytest.mark.asyncio
async def test_connect_preserves_jwt_auth() -> None:
    producer = NatsProducer(
        server=TEST_SERVER,
        auth_method="JWT",
        user="ignored-user",
        password="ignored-password",
        creds_path="/secrets/nats.creds",
    )
    connection = MagicMock(connected_url=TEST_SERVER)

    with patch(
        "nv_config_manager_workflows.clients.nats.base.nats.connect",
        new=AsyncMock(return_value=connection),
    ) as connect:
        await producer.connect()

    options = connect.await_args.kwargs
    assert options["user_credentials"] == "/secrets/nats.creds"
    assert "user" not in options
    assert "password" not in options


@pytest.mark.asyncio
async def test_local_connect_creates_a_missing_stream() -> None:
    producer = NatsProducer(
        server=TEST_SERVER,
        local=True,
        default_stream_name="workflow-events",
        default_stream_subjects=["workflow.>"],
    )
    connection = MagicMock(connected_url=TEST_SERVER)
    jetstream = connection.jetstream.return_value
    stream_info = MagicMock()
    jetstream.stream_info = AsyncMock(side_effect=[NotFoundError, stream_info])
    jetstream.add_stream = AsyncMock()

    with patch(
        "nv_config_manager_workflows.clients.nats.base.nats.connect",
        new=AsyncMock(return_value=connection),
    ):
        await producer.connect()

    connection.jetstream.assert_called_once_with(prefix="$JS.API")
    jetstream.add_stream.assert_awaited_once_with(
        name="workflow-events",
        subjects=["workflow.>"],
    )
    assert producer.stream_info is stream_info


@pytest.mark.asyncio
async def test_publish_uses_utf8_default_stream_and_api_prefix() -> None:
    producer = NatsProducer(
        server=TEST_SERVER,
        default_stream_name="workflow-events",
        api_prefix="$JS.CUSTOM.API",
    )
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=None)
    connection.jetstream.return_value.publish = AsyncMock()

    with patch.object(producer, "connect", new=AsyncMock(return_value=connection)):
        await producer.publish("workflow.result", "café")

    connection.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")
    connection.jetstream.return_value.publish.assert_awaited_once_with(
        subject="workflow.result",
        payload="café".encode(),
        stream="workflow-events",
    )


@pytest.mark.asyncio
async def test_publish_allows_stream_override() -> None:
    producer = NatsProducer(server=TEST_SERVER, default_stream_name="default-stream")
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=None)
    connection.jetstream.return_value.publish = AsyncMock()

    with patch.object(producer, "connect", new=AsyncMock(return_value=connection)):
        await producer.publish("workflow.result", "payload", stream="override-stream")

    connection.jetstream.return_value.publish.assert_awaited_once_with(
        subject="workflow.result",
        payload=b"payload",
        stream="override-stream",
    )
