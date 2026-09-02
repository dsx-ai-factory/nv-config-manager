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

The token-based helpers now live in :mod:`nv_config_manager_workflows.lock` so the
workflow package carries no service configuration; they are re-exported here for
existing callers. ``create_lock`` stays in the service because its consumers are
service runtime concerns (the render consumer, producer and HTTP routes).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from redis.asyncio.lock import Lock as AsyncRedisLock

from nv_config_manager.common.config import is_local_environment, redis_client
from nv_config_manager_workflows.lock import (
    acquire_lock,
    configure_lock_backend,
    release_lock,
    renew_lock,
)

if TYPE_CHECKING:
    from types import TracebackType

    from nv_config_manager.common.client import RedisClient

log = logging.getLogger(__name__)


class _FakeLock:
    """No-op lock for local single-process runs where no shared Redis exists."""

    async def acquire(  # pylint: disable=unused-argument
        self,
        blocking: bool | None = None,
        blocking_timeout: int | None = None,
        token: str | bytes | None = None,
    ) -> bool:
        """Acquire the lock."""
        return True

    async def release(self) -> bool:
        """Release the lock."""
        return True

    async def __aenter__(self) -> _FakeLock:
        """Async context manager entry."""
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.release()


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


def configure_workflow_lock_backend() -> None:
    """Point the workflow package's lock helpers at this service's Redis.

    Call once at startup. Derives the backend from the same client as
    ``create_lock`` so the two cannot disagree about whether this is a local
    single-process run.
    """
    client = _get_lock_redis_client()
    configure_lock_backend(client.redis if client is not None else None)


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


__all__ = [
    "acquire_lock",
    "configure_lock_backend",
    "configure_workflow_lock_backend",
    "create_lock",
    "release_lock",
    "renew_lock",
]
