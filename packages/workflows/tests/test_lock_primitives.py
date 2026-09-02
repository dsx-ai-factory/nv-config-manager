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
"""Tests for the token-based distributed lock primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

from nv_config_manager_workflows import lock as lock_module
from nv_config_manager_workflows.lock import (
    LockBackendNotConfiguredError,
    acquire_lock,
    configure_lock_backend,
    release_lock,
    renew_lock,
)


@pytest.fixture(autouse=True)
def _unconfigured_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a pristine, unconfigured module state."""
    monkeypatch.setattr(lock_module, "_lock_redis", lock_module._UNSET)


class TestBackendConfiguration:
    """The host application chooses the backend; the module holds no config."""

    async def test_unconfigured_backend_fails_with_a_named_error(self):
        """An unconfigured backend must not surface as an AttributeError."""
        with pytest.raises(LockBackendNotConfiguredError, match="configure_lock_backend"):
            await acquire_lock("k", "t", timeout=30)

    async def test_none_backend_builds_no_redis_lock(self):
        configure_lock_backend(None)

        assert lock_module._redis_lock("k", timeout=30) is None

    async def test_redis_backend_builds_a_lock_bound_to_that_client(self):
        client = Redis(host="localhost")  # constructing does not connect
        configure_lock_backend(client)

        lock = lock_module._redis_lock("resource-a", timeout=42)

        assert lock is not None
        assert lock.redis is client
        assert lock.name == "resource-a"
        assert lock.timeout == 42


class TestLocalNoop:
    """configure_lock_backend(None) reproduces the old local single-process path."""

    @pytest.fixture(autouse=True)
    def _local(self):
        configure_lock_backend(None)

    async def test_acquire_returns_true(self):
        assert await acquire_lock("k", "token", timeout=30) is True

    async def test_renew_returns_true(self):
        assert await renew_lock("k", "token", timeout=30) is True

    async def test_release_returns_true(self):
        assert await release_lock("k", "token") is True


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
    def use_fake(self, monkeypatch: pytest.MonkeyPatch):
        def _install(owner: bytes | None) -> _FakeRedisLock:
            fake = _FakeRedisLock(owner=owner)
            monkeypatch.setattr(lock_module, "_redis_lock", lambda name, timeout: fake)
            return fake

        return _install

    async def test_free_lock_is_acquired_without_blocking(self, use_fake):
        fake = use_fake(owner=None)
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=5) is True
        assert fake.blocking_waits == []

    async def test_own_lock_is_refreshed_without_blocking(self, use_fake):
        """A retried acquire whose result was lost refreshes its own lock."""
        fake = use_fake(owner=b"t")
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=5) is True
        # Never waited out blocking_timeout on a lock we already hold.
        assert fake.blocking_waits == []

    async def test_conflict_fails_fast_when_non_blocking(self, use_fake):
        fake = use_fake(owner=b"other")
        assert await acquire_lock("k", "t", timeout=30, blocking=False) is False
        assert fake.blocking_waits == []

    async def test_conflict_waits_when_blocking(self, use_fake):
        fake = use_fake(owner=b"other")
        assert await acquire_lock("k", "t", timeout=30, blocking_timeout=3) is False
        assert fake.blocking_waits == [3]

    async def test_renew_reports_a_lost_lock(self, use_fake):
        use_fake(owner=b"other")
        assert await renew_lock("k", "t", timeout=30) is False
