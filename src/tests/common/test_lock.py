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
are covered there; this file covers what stays in the service -- ``create_lock``
for the render consumers, and the startup wiring that hands the workflow package
its Redis connection.
"""

from types import SimpleNamespace

import pytest
from redis.asyncio.lock import Lock as AsyncRedisLock

from nv_config_manager.common import lock as lock_module
from nv_config_manager.common.lock import (
    _FakeLock,
    configure_workflow_lock_backend,
    create_lock,
)
from nv_config_manager_workflows import lock as workflow_lock_module


@pytest.fixture(autouse=True)
def _reset_lock_client(monkeypatch):
    """Clear the module-level Redis client cached between calls."""
    monkeypatch.setattr(lock_module, "_lock_redis_client", None)
    monkeypatch.setattr(workflow_lock_module, "_lock_redis", workflow_lock_module._UNSET)


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


class TestConfigureWorkflowLockBackend:
    """Startup wiring: one decision feeds both create_lock and the workflow lock."""

    def test_local_environment_configures_no_op_locks(self, monkeypatch):
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: True)

        configure_workflow_lock_backend()

        assert workflow_lock_module._lock_redis is None

    def test_shared_environment_passes_the_redis_connection(self, monkeypatch):
        connection = object()
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)
        monkeypatch.setattr(
            lock_module, "redis_client", lambda db_key: SimpleNamespace(redis=connection)
        )

        configure_workflow_lock_backend()

        assert workflow_lock_module._lock_redis is connection

    @pytest.mark.asyncio
    async def test_create_lock_and_workflow_lock_agree_on_the_backend(self, monkeypatch):
        """Both paths derive from the same client, so they cannot disagree."""
        monkeypatch.setattr(lock_module, "is_local_environment", lambda: False)

        configure_workflow_lock_backend()
        lock = await create_lock("resource-e")

        assert lock.redis is workflow_lock_module._lock_redis
