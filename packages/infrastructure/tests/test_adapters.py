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
from unittest.mock import AsyncMock, patch

from redis.exceptions import LockNotOwnedError

from nv_config_manager_infrastructure.lock import acquire_lock, release_lock, renew_lock
from nv_config_manager_infrastructure.nats import NatsClient
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
    assert not await acquire_lock(lock, "owner", 30, blocking=False)
    lock.reacquire.side_effect = None
    assert await acquire_lock(lock, "owner", 30, blocking=False)
    assert lock.local.token == b"owner"
    assert await renew_lock(lock, "owner", 30)
    assert await release_lock(lock, "owner")
    lock.release.side_effect = LockNotOwnedError
    assert not await release_lock(lock, "other-owner")
    assert await acquire_lock(None, "owner", 30)


def test_clients_have_no_ini_factory() -> None:
    assert not hasattr(RedisClient, "from_config")
    assert not hasattr(NatsClient, "from_config")
    client = NatsClient("nats://localhost:4222", local=True, api_prefix="$JS.custom.API")
    assert client.api_prefix == "$JS.custom.API"
