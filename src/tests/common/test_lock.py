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
"""Tests for the service-side distributed lock module.

The token-based helpers now live in :mod:`nv_config_manager_workflows.lock` and
are covered there; this file covers the service's shared lock backend.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from redis.asyncio.lock import Lock as AsyncRedisLock

from nv_config_manager.common import lock as lock_module
from nv_config_manager.common.lock import _FakeLock, create_lock, workflow_lock_backend


@pytest.fixture(autouse=True)
def _reset_lock_client(monkeypatch):
    """Clear the module-level Redis client cached between calls."""
    monkeypatch.setattr(lock_module, "_lock_redis_client", None)


def test_token_helpers_are_not_exported_from_the_service_module() -> None:
    for name in (
        "acquire_lock",
        "configure_lock_backend",
        "configure_workflow_lock_backend",
        "release_lock",
        "renew_lock",
    ):
        assert not hasattr(lock_module, name)


class TestFakeLock:
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


class TestWorkflowLockBackend:
    def test_returns_none_in_local_environment(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: True)

        assert workflow_lock_backend() is None

    def test_returns_shared_redis_connection(self, monkeypatch):
        connection = object()
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)
        monkeypatch.setattr(
            lock_module, "redis_client", lambda db_key: SimpleNamespace(redis=connection)
        )

        assert workflow_lock_backend() is connection

    @pytest.mark.asyncio
    async def test_create_lock_and_workflow_backend_share_connection(self, monkeypatch):
        connection = Mock()
        redis_client = Mock(return_value=SimpleNamespace(redis=connection))
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)
        monkeypatch.setattr(lock_module, "redis_client", redis_client)

        lock = await create_lock("resource-e")

        assert isinstance(lock, AsyncRedisLock)
        assert lock.redis is workflow_lock_backend()
        redis_client.assert_called_once_with(db_key="lock_db")


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

        assert isinstance(first, AsyncRedisLock)
        assert isinstance(second, AsyncRedisLock)
        assert first.redis is second.redis
