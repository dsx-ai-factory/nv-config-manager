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
"""Bounded admission control for ZTP object-storage downloads."""

import asyncio
import logging
import threading
from configparser import ConfigParser
from time import monotonic

from prometheus_client import Gauge, Histogram

from nv_config_manager.common.config_loader import load_config

logger = logging.getLogger(__name__)

DOWNLOAD_ADMISSION_ACTIVE = Gauge(
    "storage_download_admission_active",
    "Object-storage downloads currently admitted by protocol.",
    labelnames=("protocol",),
    namespace="nv_config_manager",
    subsystem="ztp",
)
DOWNLOAD_ADMISSION_WAIT_SECONDS = Histogram(
    "storage_download_admission_wait_seconds",
    "Time downloads wait for object-storage admission by protocol.",
    labelnames=("protocol",),
    namespace="nv_config_manager",
    subsystem="ztp",
)


def get_positive_int_config(
    name: str,
    default: int,
    config: ConfigParser | None = None,
) -> int:
    """Read a positive integer from the ZTP INI section, falling back safely."""
    app_config = config if config is not None else load_config()
    try:
        parsed = app_config.getint("ztp", name, fallback=default)
    except ValueError:
        value = app_config.get("ztp", name, fallback=None)
        logger.warning("Invalid [ztp] %s=%r; using default %d", name, value, default)
        return default
    if parsed <= 0:
        logger.warning("Invalid [ztp] %s=%r; using default %d", name, parsed, default)
        return default
    return parsed


class AsyncDownloadLimiter:
    """Limit concurrent HTTP streams while retaining a permit for their lifetime."""

    def __init__(self, capacity: int, protocol: str) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._semaphore = asyncio.Semaphore(capacity)
        self._protocol = protocol
        self._active = 0

    @property
    def active(self) -> int:
        """Return the number of downloads currently holding a permit."""
        return self._active

    async def acquire(self) -> float:
        """Wait for and acquire a permit, returning the wait duration."""
        started_at = monotonic()
        await self._semaphore.acquire()
        wait_seconds = monotonic() - started_at
        self._active += 1
        DOWNLOAD_ADMISSION_ACTIVE.labels(protocol=self._protocol).set(self._active)
        DOWNLOAD_ADMISSION_WAIT_SECONDS.labels(protocol=self._protocol).observe(wait_seconds)
        return wait_seconds

    def release(self) -> None:
        """Release one permit after the response stream has finished."""
        if self._active <= 0:
            raise RuntimeError("cannot release a download permit that is not held")
        self._active -= 1
        DOWNLOAD_ADMISSION_ACTIVE.labels(protocol=self._protocol).set(self._active)
        self._semaphore.release()


class ThreadDownloadLimiter:
    """Thread-safe counterpart for Paramiko's synchronous SFTP callbacks."""

    def __init__(self, capacity: int, protocol: str) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._semaphore = threading.BoundedSemaphore(capacity)
        self._protocol = protocol
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        """Return the number of downloads currently holding a permit."""
        with self._lock:
            return self._active

    def acquire(self) -> float:
        """Wait for and acquire a permit, returning the wait duration."""
        started_at = monotonic()
        self._semaphore.acquire()
        wait_seconds = monotonic() - started_at
        with self._lock:
            self._active += 1
            DOWNLOAD_ADMISSION_ACTIVE.labels(protocol=self._protocol).set(self._active)
        DOWNLOAD_ADMISSION_WAIT_SECONDS.labels(protocol=self._protocol).observe(wait_seconds)
        return wait_seconds

    def release(self) -> None:
        """Release one permit after the SFTP handle has closed."""
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("cannot release a download permit that is not held")
            self._active -= 1
            DOWNLOAD_ADMISSION_ACTIVE.labels(protocol=self._protocol).set(self._active)
        self._semaphore.release()
