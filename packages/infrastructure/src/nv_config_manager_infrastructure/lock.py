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
"""Configuration-independent Redis lock primitives."""

from __future__ import annotations

import logging
from types import TracebackType

from redis.asyncio.lock import Lock as AsyncRedisLock
from redis.exceptions import LockError, LockNotOwnedError

log = logging.getLogger(__name__)


class NoopLock:
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

    async def __aenter__(self) -> NoopLock:
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


def _token_bytes(token: str) -> bytes:
    """Encode a caller-supplied lock token the way redis-py stores it."""
    return token.encode()


async def acquire_lock(
    lock: AsyncRedisLock | None,
    token: str,
    timeout: int,
    blocking_timeout: float | None = None,
    blocking: bool = True,
) -> bool:
    """Acquire a distributed lock on ``name`` for ``token``.

    Returns True once held, or False if it could not be acquired immediately
    when ``blocking`` is False, otherwise within ``blocking_timeout``.

    """
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


async def renew_lock(lock: AsyncRedisLock | None, token: str, timeout: int) -> bool:
    """Extend the TTL of a lock this ``token`` holds back out to ``timeout``."""
    if lock is None:
        return True

    lock.local.token = _token_bytes(token)
    try:
        await lock.reacquire()
        return True
    except LockNotOwnedError:
        return False


async def release_lock(lock: AsyncRedisLock | None, token: str) -> bool:
    """Release a lock held by ``token``."""
    if lock is None:
        return True

    lock.local.token = _token_bytes(token)
    try:
        await lock.release()
        return True
    except (LockNotOwnedError, LockError):
        log.warning("Lock was not owned at release time.")
        return False
