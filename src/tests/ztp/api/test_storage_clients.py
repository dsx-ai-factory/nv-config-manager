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
"""Tests for the shared ZTP storage / Config Store clients + backpressure."""

import asyncio

import pytest

from nv_config_manager.ztp.api import storage_clients
from nv_config_manager.ztp.api.storage_clients import (
    StorageUnavailableError,
    guarded_storage,
)
from nv_config_manager.ztp.s3 import S3NotFoundException


async def test_guarded_storage_returns_value():
    """A fast op returns its value unchanged through the guard."""

    async def factory() -> str:
        return "ok"

    assert await guarded_storage(factory) == "ok"


async def test_guarded_storage_timeout_raises_unavailable():
    """An op that exceeds the op timeout is shed as a retryable 503-mapped error."""

    async def slow() -> str:
        await asyncio.sleep(0.2)
        return "never"

    with pytest.raises(StorageUnavailableError):
        await guarded_storage(slow, op_timeout=0.01)


async def test_guarded_storage_propagates_logical_errors():
    """NotFound (a real answer) propagates unchanged — it must stay a 404, not 503."""

    async def missing() -> str:
        raise S3NotFoundException("nope")

    with pytest.raises(S3NotFoundException):
        await guarded_storage(missing)


async def test_guarded_storage_backpressure_sheds_when_saturated(monkeypatch):
    """When all concurrency slots are held, a new op fails fast as unavailable."""
    # Drain the semaphore to 0 permits and use a tiny acquire timeout so the
    # test is fast and deterministic.
    monkeypatch.setattr(storage_clients, "_ACQUIRE_TIMEOUT", 0.01)
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # now 0 permits available
    monkeypatch.setattr(storage_clients, "_semaphore", sem)

    async def factory() -> str:
        return "ok"

    with pytest.raises(StorageUnavailableError):
        await guarded_storage(factory)
