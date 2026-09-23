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
"""Tests for cached, bounded ZTP device reads."""

import asyncio

import pytest

from nv_config_manager.dcim import DCIMConnectivityError, DCIMNotFoundError, ZTPDevice
from nv_config_manager.ztp.api.device_reads import DCIMUnavailableError, DeviceReadCache


def _ztp_device(device_id: str = "dev-1") -> ZTPDevice:
    return ZTPDevice(
        device_id=device_id,
        name="leaf-1",
        addresses=["10.0.0.1"],
        platform_name="EOS",
        firmware_version="1.0",
        config_store_instance=None,
    )


class _FakeClient:
    def __init__(self, result: ZTPDevice | Exception) -> None:
        self._result = result
        self.calls = 0

    async def get_ztp_device(self, _device_id: str) -> ZTPDevice:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def close(self) -> None:
        return None


async def test_load_caches_until_ttl():
    """A second read inside the TTL does not call the DCIM again."""
    cache = DeviceReadCache()
    cache._cache_ttl = 30
    client = _FakeClient(_ztp_device())
    cache._client = client  # type: ignore[assignment]

    first = await cache.load("dev-1")
    second = await cache.load("dev-1")

    assert first.id == second.id == "dev-1"
    assert client.calls == 1


async def test_not_found_does_not_open_the_breaker():
    """A missing device is a real answer, not a DCIM outage."""
    cache = DeviceReadCache()
    cache._breaker_threshold = 1
    cache._client = _FakeClient(DCIMNotFoundError("missing"))  # type: ignore[assignment]

    with pytest.raises(DCIMNotFoundError):
        await cache.load("dev-1")

    assert cache._breaker_open_until == 0.0
    assert "dev-1" not in cache._cache


async def test_transient_failures_shed_as_unavailable():
    """Connectivity errors become one retryable failure after the attempts."""
    cache = DeviceReadCache()
    cache._max_attempts = 2
    cache._retry_backoff = 0
    cache._request_timeout = 1
    cache._client = _FakeClient(DCIMConnectivityError("down"))  # type: ignore[assignment]

    with pytest.raises(DCIMUnavailableError):
        await cache.load("dev-1")

    assert cache._consecutive_failures == 1
    assert "dev-1" not in cache._cache


async def test_saturated_limiter_sheds():
    """A full concurrency limit fails fast instead of queueing forever."""
    cache = DeviceReadCache()
    cache._acquire_timeout = 0.01
    cache._semaphore = asyncio.Semaphore(1)
    await cache._semaphore.acquire()

    async def _fetch() -> str:
        return "ok"

    with pytest.raises(DCIMUnavailableError):
        await cache._guarded(_fetch)


async def test_concurrent_loads_share_one_read():
    """Two callers for the same device wait on one in-flight read."""
    cache = DeviceReadCache()
    cache._cache_ttl = 30
    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowClient(_FakeClient):
        async def get_ztp_device(self, device_id: str) -> ZTPDevice:
            self.calls += 1
            started.set()
            await release.wait()
            return _ztp_device(device_id)

    client = _SlowClient(_ztp_device())
    cache._client = client  # type: ignore[assignment]

    first = asyncio.create_task(cache.load("dev-1"))
    await started.wait()
    second = asyncio.create_task(cache.load("dev-1"))
    await asyncio.sleep(0)
    release.set()

    assert (await first).id == (await second).id == "dev-1"
    assert client.calls == 1
