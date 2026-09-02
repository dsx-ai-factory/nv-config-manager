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
"""Token-based distributed locks backed by Redis."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from redis.asyncio.lock import Lock as AsyncRedisLock
from redis.exceptions import LockError, LockNotOwnedError

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)


class LockBackendNotConfiguredError(RuntimeError):
    """Raised when a lock is used before the host application configured a backend."""


class _Unset:
    """Distinguishes "never configured" from "configured as a local no-op"."""


_UNSET: Final = _Unset()

# Set once at application startup; see configure_lock_backend().
_lock_redis: Redis | None | _Unset = _UNSET


def configure_lock_backend(redis: Redis | None) -> None:
    """Point the lock helpers at ``redis``, or at no-op locks when ``None``.

    Args:
        redis: An already-connected async Redis client, or None for local
            single-process runs where no shared Redis exists.
    """
    global _lock_redis  # noqa: PLW0603
    _lock_redis = redis


def _token_bytes(token: str) -> bytes:
    """Encode a caller-supplied lock token the way redis-py stores it."""
    return token.encode()


def _redis_lock(name: str, timeout: int) -> AsyncRedisLock | None:
    """Build a Redis-backed lock, or None when configured for local no-op locks."""
    if isinstance(_lock_redis, _Unset):
        raise LockBackendNotConfiguredError(
            "Workflow lock backend is not configured. Call "
            "nv_config_manager_workflows.lock.configure_lock_backend(redis) at "
            "application startup, passing None for local no-op locks."
        )
    if _lock_redis is None:
        return None
    return AsyncRedisLock(_lock_redis, name, timeout=timeout)


async def acquire_lock(
    name: str,
    token: str,
    timeout: int,
    blocking_timeout: float | None = None,
    blocking: bool = True,
) -> bool:
    """Acquire a distributed lock on ``name`` for ``token``.

    Returns True once held, or False if it could not be acquired immediately
    when ``blocking`` is False, otherwise within ``blocking_timeout``.

    """
    lock = _redis_lock(name, timeout)
    if lock is None:
        return True

    token_bytes = _token_bytes(token)

    if await lock.acquire(token=token_bytes, blocking=False):
        return True
    if await _refresh_if_owned(lock, token_bytes):
        return True

    if not blocking:
        return False
    return bool(
        await lock.acquire(token=token_bytes, blocking=True, blocking_timeout=blocking_timeout)
    )


async def _refresh_if_owned(lock: AsyncRedisLock, token_bytes: bytes) -> bool:
    """Extend ``lock``'s TTL when ``token_bytes`` already holds it, else False."""
    lock.local.token = token_bytes
    try:
        await lock.reacquire()
    except LockNotOwnedError:
        return False
    return True


async def renew_lock(name: str, token: str, timeout: int) -> bool:
    """Extend the TTL of a lock this ``token`` holds back out to ``timeout``."""
    lock = _redis_lock(name, timeout)
    if lock is None:
        return True

    lock.local.token = _token_bytes(token)
    try:
        await lock.reacquire()
        return True
    except LockNotOwnedError:
        return False


async def release_lock(name: str, token: str) -> bool:
    """Release a lock held by ``token``."""
    lock = _redis_lock(name, timeout=1)
    if lock is None:
        return True

    lock.local.token = _token_bytes(token)
    try:
        await lock.release()
        return True
    except (LockNotOwnedError, LockError):
        log.warning("Lock %s was not owned at release time.", name)
        return False


__all__ = [
    "LockBackendNotConfiguredError",
    "acquire_lock",
    "configure_lock_backend",
    "release_lock",
    "renew_lock",
]
