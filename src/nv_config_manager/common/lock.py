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
"""Distributed locks backed by Redis.

Shared across services so critical sections are serialized across workers and pods.
Falls back to a no-op lock in local development where no shared Redis is available.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nv_config_manager_infrastructure import lock as lock_primitives
from nv_config_manager_infrastructure.lock import NoopLock as _FakeLock
from redis.asyncio.lock import Lock as AsyncRedisLock

from nv_config_manager.common.config import redis_client
from nv_config_manager.common.config.environment import is_local_environment

if TYPE_CHECKING:
    from nv_config_manager.common.client import RedisClient

log = logging.getLogger(__name__)


# Module-level Redis client for lock operations
_lock_redis_client: RedisClient | None = None


def _get_lock_redis_client() -> RedisClient | None:
    """Get or create a shared async Redis client for locking."""
    global _lock_redis_client

    if is_local_environment():
        return None

    if _lock_redis_client is not None:
        return _lock_redis_client

    _lock_redis_client = redis_client(db_key="lock_db")
    return _lock_redis_client


async def create_lock(
    name: str,
    timeout: int = 180,
    blocking: bool = True,
    blocking_timeout: int | None = None,
) -> AsyncRedisLock | _FakeLock:
    """Initialize an async lock object for this environment.

    Args:
        name: Lock name
        timeout: Lock timeout in seconds
        blocking: Whether acquire() should block waiting for the lock
        blocking_timeout: Max time to wait for lock acquisition

    Returns:
        AsyncRedisLock for distributed locking, or _FakeLock for local development
    """
    client = _get_lock_redis_client()
    if client is None:
        return _FakeLock()

    return AsyncRedisLock(
        client.redis, name, timeout=timeout, blocking=blocking, blocking_timeout=blocking_timeout
    )


# ---------------------------------------------------------------------------
# Token-based locking
# ---------------------------------------------------------------------------


def _redis_lock(name: str, timeout: int) -> AsyncRedisLock | None:
    """Build a Redis-backed lock, or None in local single-process development."""
    client = _get_lock_redis_client()
    if client is None:
        return None
    return AsyncRedisLock(client.redis, name, timeout=timeout)


async def acquire_lock(
    name: str,
    token: str,
    timeout: int,
    blocking_timeout: float | None = None,
    blocking: bool = True,
) -> bool:
    """Acquire a token lock using the service-selected Redis backend."""
    return await lock_primitives.acquire_lock(
        _redis_lock(name, timeout), token, blocking_timeout, blocking
    )


async def renew_lock(name: str, token: str, timeout: int) -> bool:
    """Renew a token lock using the service-selected Redis backend."""
    return await lock_primitives.renew_lock(_redis_lock(name, timeout), token)


async def release_lock(name: str, token: str) -> bool:
    """Release a token lock using the service-selected Redis backend."""
    return await lock_primitives.release_lock(_redis_lock(name, 1), token)
