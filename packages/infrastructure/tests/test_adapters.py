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
"""Reusable infrastructure behavior without application configuration."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import LockNotOwnedError

from nv_config_manager_infrastructure.lock import acquire_lock, release_lock, renew_lock
from nv_config_manager_infrastructure.nats import NatsClient
from nv_config_manager_infrastructure.nats.consumer import NatsConsumer
from nv_config_manager_infrastructure.nats.producer import NatsProducer
from nv_config_manager_infrastructure.redis import RedisClient


async def test_redis_preserves_bytes_json_and_pickle_migration() -> None:
    backend = AsyncMock()
    with patch("nv_config_manager_infrastructure.redis.redis_asyncio.Redis", return_value=backend):
        client = RedisClient("localhost", db=2)
    await client.set("config", {"a": 1}, ttl=timedelta(seconds=60))
    backend.set.assert_awaited_once_with("config", b'{"a": 1}', ex=timedelta(seconds=60))
    backend.get.return_value = b"\x80\x04legacy-pickle"
    assert await client.get("config") is None
    backend.get.return_value = b"raw"
    assert await client.get("bundle", deserialize=False) == b"raw"
    await client.close()
    backend.aclose.assert_awaited_once()


async def test_token_locks_keep_ownership_semantics() -> None:
    lock = AsyncMock()
    lock.acquire.return_value = False
    lock.reacquire.side_effect = LockNotOwnedError
    assert not await acquire_lock(lock, "owner", blocking=False)
    lock.reacquire.side_effect = None
    assert await acquire_lock(lock, "owner", blocking=False)
    assert lock.local.token == b"owner"
    assert await renew_lock(lock, "owner")
    assert await release_lock(lock, "owner")
    lock.release.side_effect = LockNotOwnedError
    assert not await release_lock(lock, "other-owner")
    assert await acquire_lock(None, "owner")


def test_clients_have_no_ini_factory() -> None:
    assert not hasattr(RedisClient, "from_config")
    assert not hasattr(NatsClient, "from_config")
    client = NatsClient("nats://localhost:4222", local=True, api_prefix="$JS.custom.API")
    assert client.api_prefix == "$JS.custom.API"
    assert issubclass(NatsProducer, NatsClient)
    assert issubclass(NatsConsumer, NatsClient)


async def test_password_connection_negotiates_tls_before_credentials() -> None:
    """Password authentication starts TLS before the NATS protocol handshake."""
    conn = MagicMock(connected_url="tls://nats.example.test:4222")
    with patch(
        "nv_config_manager_infrastructure.nats.client.nats.connect",
        new=AsyncMock(return_value=conn),
    ) as connect:
        await NatsClient("tls://nats.example.test:4222", user="user", password="secret").connect()

    assert connect.await_args.kwargs["tls_handshake_first"] is True
    assert connect.await_args.kwargs["tls"] is not None


async def test_external_password_connection_rejects_plaintext_endpoint() -> None:
    """External credentials cannot be sent to an endpoint without TLS-first semantics."""
    client = NatsClient("nats://nats.example.test:4222", user="user", password="secret")
    with pytest.raises(ValueError, match="tls://"):
        await client.connect()


async def test_bundled_password_connection_keeps_server_negotiated_tls() -> None:
    """The explicitly local bundled server retains its existing INFO-first protocol."""
    conn = MagicMock(connected_url="nats://localhost:4222")
    with patch(
        "nv_config_manager_infrastructure.nats.client.nats.connect",
        new=AsyncMock(return_value=conn),
    ) as connect:
        client = NatsClient(
            "nats://localhost:4222",
            local=True,
            user="user",
            password="secret",
        )
        with patch.object(client, "_ensure_stream", new=AsyncMock()):
            await client.connect()

    assert "tls_handshake_first" not in connect.await_args.kwargs


async def test_stream_setup_failure_closes_connection() -> None:
    """A failed local stream setup does not leak the established connection."""
    conn = MagicMock(connected_url="nats://localhost:4222", is_closed=False)
    conn.close = AsyncMock()
    client = NatsClient("nats://localhost:4222", local=True)
    with (
        patch(
            "nv_config_manager_infrastructure.nats.client.nats.connect",
            new=AsyncMock(return_value=conn),
        ),
        patch.object(client, "_ensure_stream", new=AsyncMock(side_effect=RuntimeError("failed"))),
        pytest.raises(RuntimeError, match="failed"),
    ):
        await client.connect()

    conn.close.assert_awaited_once()
