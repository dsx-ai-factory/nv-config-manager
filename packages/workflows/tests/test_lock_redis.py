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
"""Redis-backed proof that the token-based lock serializes concurrent holders.

Opt-in: runs against ``REDIS_HOST`` or a throwaway testcontainers Redis when
``LOCK_REDIS_TEST`` is set, and skips otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from nv_config_manager_workflows.lock import (
    acquire_lock,
    configure_lock_backend,
    release_lock,
    renew_lock,
)

HOLD_S = 0.3
TTL_S = 30


@pytest.fixture
async def redis_lock_backend() -> AsyncIterator[None]:
    """Point the lock helpers at a real Redis, or skip when none is available."""
    host = os.environ.get("REDIS_HOST")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    container = None

    if not host:
        if not os.environ.get("LOCK_REDIS_TEST"):
            pytest.skip("Set REDIS_HOST or LOCK_REDIS_TEST=1 to run the Redis-backed lock test")
        redis_container = pytest.importorskip(
            "testcontainers.redis", reason="testcontainers is not installed"
        )
        try:
            container = redis_container.RedisContainer("redis:7-alpine")
            container.start()
            host = container.get_container_host_ip()
            port = int(container.get_exposed_port(6379))
        except Exception as exc:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.stop()
            pytest.skip(f"No Redis available for lock test: {exc}")

    client: Redis = Redis(host=host, port=port)
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        if container is not None:
            container.stop()
        pytest.skip(f"Redis not reachable for lock test: {exc}")

    configure_lock_backend(client)
    try:
        yield
    finally:
        configure_lock_backend(None)
        await client.aclose()
        if container is not None:
            container.stop()


async def _critical_section(key: str, token: str, events: list[tuple[str, str, float]]) -> None:
    """Hold the lock across a short sleep, recording enter/exit timestamps."""
    assert await acquire_lock(key, token, timeout=TTL_S, blocking_timeout=10)
    try:
        events.append(("enter", token, time.monotonic()))
        await asyncio.sleep(HOLD_S)
        events.append(("exit", token, time.monotonic()))
    finally:
        await release_lock(key, token)


def _kinds_in_time_order(events: list[tuple[str, str, float]]) -> list[str]:
    return [kind for kind, _token, _ts in sorted(events, key=lambda e: e[2])]


async def test_same_key_holders_are_serialized(redis_lock_backend):
    """Two concurrent holders of one key never overlap their critical sections."""
    events: list[tuple[str, str, float]] = []

    await asyncio.gather(
        _critical_section("wf-lock:ngc:pkey=0x0005", "run-a", events),
        _critical_section("wf-lock:ngc:pkey=0x0005", "run-b", events),
    )

    assert _kinds_in_time_order(events) == ["enter", "exit", "enter", "exit"]


async def test_distinct_keys_run_concurrently(redis_lock_backend):
    """Holders of different keys hold independent locks and overlap freely."""
    events: list[tuple[str, str, float]] = []

    await asyncio.gather(
        _critical_section("wf-lock:ngc:pkey=0x0005", "run-a", events),
        _critical_section("wf-lock:ngc:pkey=0x0006", "run-b", events),
    )

    assert _kinds_in_time_order(events) == ["enter", "enter", "exit", "exit"]


async def test_renew_extends_holder_and_release_frees_key(redis_lock_backend):
    """A holder can renew its own lock, and release lets another token take it."""
    key = "wf-lock:ngc:pkey=0x0007"

    assert await acquire_lock(key, "run-a", timeout=TTL_S, blocking_timeout=5)
    # The holder can extend its own TTL.
    assert await renew_lock(key, "run-a", timeout=TTL_S) is True
    # A different token cannot grab it while held.
    assert await acquire_lock(key, "run-b", timeout=TTL_S, blocking_timeout=1) is False

    assert await release_lock(key, "run-a") is True
    # Once released, another token acquires immediately.
    assert await acquire_lock(key, "run-b", timeout=TTL_S, blocking_timeout=1) is True
    await release_lock(key, "run-b")


async def test_renew_fails_when_lock_lost_to_another_holder(redis_lock_backend):
    """Renewing a key owned by a different token reports the loss."""
    key = "wf-lock:ngc:pkey=0x0008"

    assert await acquire_lock(key, "run-b", timeout=TTL_S, blocking_timeout=5)
    # run-a never held it, so it cannot renew (and must not steal a held lock).
    assert await renew_lock(key, "run-a", timeout=TTL_S) is False
    await release_lock(key, "run-b")
