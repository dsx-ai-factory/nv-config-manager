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
"""Tests for the shared Redis-backed distributed lock."""

from types import SimpleNamespace

import pytest
from nv_config_manager_infrastructure.lock import NoopLock
from redis.asyncio.lock import Lock as AsyncRedisLock
from redis.exceptions import LockNotOwnedError

from nv_config_manager.common import lock as lock_module
from nv_config_manager.common.lock import (
    _FakeLock,
    acquire_lock,
    create_lock,
    release_lock,
    renew_lock,
)


@pytest.fixture(autouse=True)
def _reset_lock_client(monkeypatch):
    """Clear the module-level Redis client cached between calls."""
    monkeypatch.setattr(lock_module, "_lock_redis_client", None)


class TestFakeLock:
    def test_is_infrastructure_noop_lock_alias(self):
        assert _FakeLock is NoopLock

    @pytest.mark.asyncio
    async def test_acquire_and_release_are_noops(self):
        fake = _FakeLock()
        assert await fake.acquire() is True
        assert await fake.release() is True

    @pytest.mark.asyncio
    async def test_usable_as_async_context_manager(self):
        entered = False
        async with _FakeLock():
            entered = True
        assert entered is True


class TestCreateLock:
    @pytest.mark.asyncio
    async def test_returns_fake_lock_in_local_environment(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: True)

        lock = await create_lock("resource-a")

        assert isinstance(lock, _FakeLock)

    @pytest.mark.asyncio
    async def test_returns_redis_lock_outside_local_environment(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)

        lock = await create_lock("resource-b", timeout=42, blocking_timeout=7)

        assert isinstance(lock, AsyncRedisLock)
        assert lock.name == "resource-b"
        assert lock.timeout == 42
        assert lock.blocking_timeout == 7

    @pytest.mark.asyncio
    async def test_redis_client_is_reused_across_calls(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)

        first = await create_lock("resource-c")
        second = await create_lock("resource-d")

        assert first.redis is second.redis


class TestTokenHelpersLocalNoop:
    """Without a shared Redis, the token helpers are no-ops that report success."""

    @pytest.fixture(autouse=True)
    def _force_local(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: True)

    @pytest.mark.asyncio
    async def test_acquire_returns_true(self):
        assert await acquire_lock("k", "token", timeout=30) is True

    @pytest.mark.asyncio
    async def test_renew_returns_true(self):
        assert await renew_lock("k", "token", timeout=30) is True

    @pytest.mark.asyncio
    async def test_release_returns_true(self):
        assert await release_lock("k", "token") is True


@pytest.mark.parametrize("client", [None, SimpleNamespace(redis=object())])
def test_token_lock_backend_uses_service_selected_redis(client, monkeypatch):
    selected: list[object | None] = []
    backend = object()
    monkeypatch.setattr(lock_module, "_get_lock_redis_client", lambda: client)
    monkeypatch.setattr(
        lock_module,
        "TokenLockBackend",
        lambda redis: selected.append(redis) or backend,
    )

    assert lock_module.token_lock_backend() is backend
    assert selected == [client.redis if client is not None else None]


class _FakeRedisLock:
    """Minimal async Lock stand-in that models single-owner reentrancy."""

    def __init__(self, owner: bytes | None = None) -> None:
        self.local = SimpleNamespace(token=None)
        self._owner = owner
        self.blocking_waits: list[float | None] = []

    async def acquire(
        self,
        token: bytes | None = None,
        blocking: bool = True,
        blocking_timeout: float | None = None,
    ) -> bool:
        if blocking:
            self.blocking_waits.append(blocking_timeout)
        if self._owner is None:
            self._owner = token
            return True
        return False

    async def reacquire(self) -> bool:
        if self._owner is not None and self._owner == self.local.token:
            return True
        raise LockNotOwnedError("lock is not owned by this token")


class TestAcquireLockReentrancy:
    """acquire_lock is idempotent for a token and honors the blocking flag."""

    @pytest.fixture
    def use_fake(self, monkeypatch):
        def _install(owner: bytes | None) -> _FakeRedisLock:
            fake = _FakeRedisLock(owner=owner)
            backend = lock_module.TokenLockBackend(None)
            monkeypatch.setattr(backend, "_lock", lambda name, timeout: fake)
            monkeypatch.setattr(lock_module, "token_lock_backend", lambda: backend)
            return fake

        return _install

    @pytest.mark.asyncio
    async def test_free_lock_is_acquired_without_blocking(self, use_fake):
        fake = use_fake(owner=None)
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=5) is True
        assert fake.blocking_waits == []

    @pytest.mark.asyncio
    async def test_own_lock_is_refreshed_without_blocking(self, use_fake):
        """A retried acquire whose result was lost refreshes its own lock."""
        fake = use_fake(owner=b"t")
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=5) is True
        # Never waited out blocking_timeout on a lock we already hold.
        assert fake.blocking_waits == []

    @pytest.mark.asyncio
    async def test_conflict_fails_fast_when_non_blocking(self, use_fake):
        fake = use_fake(owner=b"other")
        assert await acquire_lock("k", "t", timeout=30, blocking=False) is False
        assert fake.blocking_waits == []

    @pytest.mark.asyncio
    async def test_conflict_waits_when_blocking(self, use_fake):
        fake = use_fake(owner=b"other")
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=3) is False
        assert fake.blocking_waits == [3]
