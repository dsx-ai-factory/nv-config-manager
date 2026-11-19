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
"""Cached, bounded DCIM reads for the ZTP device API.

A boot is several HTTP requests, and each one used to open a new DCIM client
and read the same device. This keeps one client for the process, caches the
device for a few seconds, and fails fast when the DCIM is saturated.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import aiohttp

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import (
    DCIMClient,
    DCIMConnectivityError,
    DCIMNotFoundError,
    create_dcim_client,
)
from nv_config_manager.ztp.device import DeviceData

logger = get_logger(__name__, category=LogCategory.ZTP_API)

_TRANSIENT_ERRORS = (
    TimeoutError,
    aiohttp.ClientError,
    DCIMConnectivityError,
)


class DCIMUnavailableError(Exception):
    """The DCIM is saturated or unhealthy. Callers should retry shortly."""


def _float_setting(name: str, default: float) -> float:
    config = load_config()
    if not config.has_section("nautobot"):
        return default
    return config.getfloat("nautobot", name, fallback=default)


def _int_setting(name: str, default: int) -> int:
    config = load_config()
    if not config.has_section("nautobot"):
        return default
    return config.getint("nautobot", name, fallback=default)


class DeviceReadCache:
    """Process-wide device reads: one client, a short cache, and a limiter."""

    def __init__(self) -> None:
        """Load tunables from the nautobot INI section, with safe defaults."""
        self._cache_ttl = _float_setting("cache_ttl", 5.0)
        self._request_timeout = _float_setting("request_timeout", 8.0)
        self._acquire_timeout = _float_setting("acquire_timeout", 3.0)
        self._retry_backoff = _float_setting("request_retry_backoff", 0.1)
        self._max_attempts = max(1, _int_setting("request_max_attempts", 2))
        self._breaker_threshold = _int_setting("breaker_failure_threshold", 8)
        self._breaker_cooldown = _float_setting("breaker_cooldown", 10.0)
        self._semaphore = asyncio.Semaphore(_int_setting("max_concurrency", 40))
        self._cache: dict[str, tuple[float, DeviceData]] = {}
        self._inflight: dict[str, asyncio.Task[DeviceData]] = {}
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0
        self._client: DCIMClient | None = None
        self._client_lock = asyncio.Lock()

    async def load(self, device_id: str) -> DeviceData:
        """Return device data, sharing one in-flight read and a short cache."""
        cached = self._cache_get(device_id)
        if cached is not None:
            return cached

        task = self._inflight.get(device_id)
        if task is None:
            task = asyncio.ensure_future(self._load(device_id))
            self._inflight[device_id] = task

            def _discard(_task: asyncio.Task[DeviceData], key: str = device_id) -> None:
                self._inflight.pop(key, None)

            task.add_done_callback(_discard)
        return await task

    async def serial(self, device_id: str) -> str:
        """Return the device serial through the shared client and limiter, uncached."""

        async def _fetch() -> str:
            client = await self._client_session()
            return await client.get_device_serial(device_id)

        return await self._guarded(_fetch)

    def forget(self, device_id: str) -> None:
        """Drop a cached device after a write that changes it."""
        self._cache.pop(device_id, None)

    async def close(self) -> None:
        """Close the shared DCIM client. Safe to call more than once."""
        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    def _cache_get(self, device_id: str) -> DeviceData | None:
        entry = self._cache.get(device_id)
        if entry is None:
            return None
        expiry, data = entry
        if time.monotonic() >= expiry:
            self._cache.pop(device_id, None)
            return None
        return data

    def _cache_put(self, device_id: str, data: DeviceData) -> None:
        if self._cache_ttl > 0:
            self._cache[device_id] = (time.monotonic() + self._cache_ttl, data)

    def _breaker_check(self) -> None:
        if self._breaker_open_until and time.monotonic() < self._breaker_open_until:
            raise DCIMUnavailableError("DCIM circuit breaker is open; retry shortly.")

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._breaker_open_until = time.monotonic() + self._breaker_cooldown
            logger.warning(
                "DCIM circuit breaker opened after %d consecutive failures",
                self._consecutive_failures,
            )

    async def _guarded[T](self, factory: Callable[[], Awaitable[T]]) -> T:
        self._breaker_check()
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._acquire_timeout)
        except TimeoutError as exc:
            raise DCIMUnavailableError("DCIM concurrency limit reached; retry shortly.") from exc
        try:
            last_exc: BaseException | None = None
            for attempt in range(self._max_attempts):
                try:
                    async with asyncio.timeout(self._request_timeout):
                        result = await factory()
                except _TRANSIENT_ERRORS as exc:
                    last_exc = exc
                    if attempt + 1 < self._max_attempts and self._retry_backoff > 0:
                        await asyncio.sleep(self._retry_backoff)
                    continue
                except Exception:
                    self._record_success()
                    raise
                else:
                    self._record_success()
                    return result
            self._record_failure()
            raise DCIMUnavailableError("DCIM is slow or unavailable after retries.") from last_exc
        finally:
            self._semaphore.release()

    async def _client_session(self) -> DCIMClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = create_dcim_client()
        return self._client

    async def _load(self, device_id: str) -> DeviceData:
        async def _fetch() -> DeviceData:
            client = await self._client_session()
            return DeviceData.from_dcim(await client.get_ztp_device(device_id))

        try:
            data = await self._guarded(_fetch)
        except DCIMNotFoundError:
            raise
        self._cache_put(device_id, data)
        return data


_cache: DeviceReadCache | None = None


def _reads() -> DeviceReadCache:
    global _cache
    if _cache is None:
        _cache = DeviceReadCache()
    return _cache


async def load_device(device_id: str) -> DeviceData:
    """Load one device through the shared cache."""
    return await _reads().load(device_id)


async def load_device_serial(device_id: str) -> str:
    """Load one device serial through the shared client and limiter."""
    return await _reads().serial(device_id)


def forget_device(device_id: str) -> None:
    """Drop one device from the cache."""
    if _cache is not None:
        _cache.forget(device_id)


async def close_device_reads() -> None:
    """Close the shared DCIM client on shutdown."""
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None


def reset_device_reads() -> None:
    """Drop cache state without awaiting. Tests close the client separately."""
    global _cache
    _cache = None
