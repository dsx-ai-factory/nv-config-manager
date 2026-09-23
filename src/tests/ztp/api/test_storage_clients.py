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
from configparser import ConfigParser
from unittest.mock import AsyncMock, MagicMock

import pytest

from nv_config_manager.ztp.api import storage_clients
from nv_config_manager.ztp.api.storage_clients import (
    StorageUnavailableError,
    get_config_store_client,
    get_object_storage_client,
    guarded_storage,
)
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.s3 import S3NotFoundException


def _storage_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    return client


async def test_object_storage_client_rebuilt_when_config_changes(monkeypatch):
    """The pooled client is reused until the INI changes, then replaced and retired."""
    config = ConfigParser()
    monkeypatch.setattr(storage_clients, "load_config", lambda: config)
    monkeypatch.setattr(storage_clients, "_RETIRE_GRACE", 0)
    first, second = _storage_client(), _storage_client()
    monkeypatch.setattr(
        storage_clients, "_build_storage_client", MagicMock(side_effect=[first, second])
    )

    assert await get_object_storage_client() is first
    assert await get_object_storage_client() is first

    config = ConfigParser()
    assert await get_object_storage_client() is second
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    first.close.assert_awaited_once()


async def test_object_storage_connect_failure_closes_client(monkeypatch):
    """A failed connect closes the partial client and does not cache it."""
    broken = _storage_client()
    broken.connect.side_effect = ConnectionError("no route")
    monkeypatch.setattr(storage_clients, "_build_storage_client", lambda: broken)

    with pytest.raises(ConnectionError):
        await get_object_storage_client()

    broken.close.assert_awaited_once()
    assert storage_clients._object_storage_client is None


async def test_file_storage_client_is_not_pooled(monkeypatch, tmp_path):
    """File-backed storage reloads its manifest on every request."""
    monkeypatch.setattr(
        storage_clients, "_build_storage_client", lambda: FileStoreClient(base_path=str(tmp_path))
    )

    first = await get_object_storage_client()
    second = await get_object_storage_client()

    assert isinstance(first, FileStoreClient)
    assert first is not second
    assert storage_clients._object_storage_client is None


async def test_config_store_client_shared_across_instances(monkeypatch):
    """Devices on different instances share one client until the settings change."""
    monkeypatch.setattr(storage_clients, "_RETIRE_GRACE", 0)
    key = ("internal", "http://config-store", None)
    monkeypatch.setattr(storage_clients, "_config_store_key", lambda: key)
    built = [_storage_client(), _storage_client()]
    devices = []
    for instance in ("east", "west"):
        device = MagicMock(config_store_instance=instance)
        device.config_store_client = MagicMock(side_effect=lambda: built.pop(0))
        devices.append(device)

    first = get_config_store_client(devices[0])
    assert get_config_store_client(devices[1]) is first

    key = ("internal", "http://config-store-2", None)
    assert get_config_store_client(devices[1]) is not first
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    first.close.assert_awaited_once()


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
