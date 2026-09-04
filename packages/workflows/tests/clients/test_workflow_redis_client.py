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
"""Tests for the configuration-independent Redis client."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from nv_config_manager_workflows.clients import RedisClient


def _client_with_backend() -> tuple[RedisClient, MagicMock]:
    backend = MagicMock()
    with patch(
        "nv_config_manager_workflows.clients.redis.redis_asyncio.Redis",
        return_value=backend,
    ):
        client = RedisClient(host="redis.example.com")
    return client, backend


def test_constructor_uses_explicit_connection_settings() -> None:
    with patch("nv_config_manager_workflows.clients.redis.redis_asyncio.Redis") as constructor:
        RedisClient(
            host="redis.example.com",
            port=6380,
            db=3,
            ssl=True,
            password="secret",
            socket_timeout=7,
            socket_connect_timeout=9,
        )

    constructor.assert_called_once_with(
        host="redis.example.com",
        port=6380,
        db=3,
        ssl=True,
        password="secret",
        socket_timeout=7,
        socket_connect_timeout=9,
        decode_responses=False,
    )


async def test_set_serializes_json_and_applies_default_ttl() -> None:
    client, backend = _client_with_backend()
    backend.set = AsyncMock()

    await client.set("key", {"value": 1})

    backend.set.assert_awaited_once_with(
        "key",
        b'{"value": 1}',
        ex=RedisClient.DEFAULT_TTL,
    )


async def test_set_preserves_raw_bytes_and_explicit_ttl() -> None:
    client, backend = _client_with_backend()
    backend.set = AsyncMock()
    ttl = timedelta(hours=1)

    await client.set("key", b"raw", ttl=ttl, serialize=False)

    backend.set.assert_awaited_once_with("key", b"raw", ex=ttl)


async def test_get_deserializes_json() -> None:
    client, backend = _client_with_backend()
    backend.get = AsyncMock(return_value=b'{"value": 1}')

    assert await client.get("key") == {"value": 1}


async def test_get_returns_raw_bytes_when_requested() -> None:
    client, backend = _client_with_backend()
    backend.get = AsyncMock(return_value=b"raw")

    assert await client.get("key", deserialize=False) == b"raw"


async def test_get_treats_legacy_pickle_as_cache_miss() -> None:
    client, backend = _client_with_backend()
    backend.get = AsyncMock(return_value=b"\x80\x04legacy")

    assert await client.get("key") is None


async def test_async_context_manager_closes_connection() -> None:
    backend = MagicMock()
    backend.aclose = AsyncMock()
    with patch(
        "nv_config_manager_workflows.clients.redis.redis_asyncio.Redis",
        return_value=backend,
    ):
        async with RedisClient(host="redis.example.com"):
            pass

    backend.aclose.assert_awaited_once_with()
