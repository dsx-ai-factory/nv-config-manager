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
"""Shared, process-wide storage + Config Store clients for the ZTP API.

Why this exists
---------------
Device-facing handlers used to build a fresh client *per request*:

* ``get_storage_client()`` created a new :class:`S3Client` each call — a new
  ``aioboto3.Session`` plus a ``connect()`` that resolves credentials (IRSA →
  STS on first use) and opens a brand-new connection pool. Measured at ~139ms
  of event-loop CPU per request on an idle pod.
* ``DeviceData.config_store_client()`` created a new
  :class:`ConfigStoreClient` each call — and its ``__init__`` builds a new
  ``aiohttp.TCPConnector`` + SSL context every time, so nothing is reused.

That setup work runs on ZTP's single asyncio event loop. Under a boot storm
it is a large fraction of each lap, which is what made ``/healthcheck`` miss
its probe timeout even when S3 and Config Store themselves were fine.

Streaming firmware/ONIE/files still use the per-request ``get_storage_client()``
path: the streaming helper takes ownership of the client and closes it when
the response finishes, so pointing those endpoints at the shared client
without a lifecycle change would close the pool mid-flight.

What this provides
------------------
* A single, pre-connected object-storage client reused across requests
  (keepalive + a real pool cap instead of one pool per request). File-backed
  storage is not pooled: its manifest is read at connect time and must stay
  current with uploads.
* One Config Store client keyed by its effective endpoint and TLS settings.
* Pooled clients are rebuilt when the INI (or a TLS file it names) changes;
  replaced clients close after in-flight operations have had time to finish.
* A bounded concurrency semaphore + short per-op timeout so a saturated or
  stuck storage/Config Store call sheds load fast as a retryable
  :class:`StorageUnavailableError` (surfaced as HTTP 503 ``Retry-After``)
  instead of accumulating on the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from configparser import ConfigParser
from typing import TYPE_CHECKING, Protocol

from nv_config_manager.common.client import ConfigStoreClient
from nv_config_manager.common.config import get_storage_client as _build_storage_client
from nv_config_manager.common.config import load_config
from nv_config_manager.common.ini import file_fingerprint
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.storage import ObjectStorageClient

if TYPE_CHECKING:
    # device.py imports this module at top level; a runtime import would be circular.
    from nv_config_manager.ztp.device import DeviceData

logger = get_logger(__name__, category=LogCategory.ZTP_API)

# Bound the number of in-flight storage/Config Store operations so ZTP applies
# backpressure instead of opening unbounded I/O during a boot storm. Callers
# that cannot get a slot within the acquire timeout, or whose op exceeds the op
# timeout, get a fast StorageUnavailableError (HTTP 503) rather than piling up.
_MAX_CONCURRENCY = 40
_ACQUIRE_TIMEOUT = 3.0
_OP_TIMEOUT = 8.0

_semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

# Replaced clients stay open this long so operations already using them finish.
_RETIRE_GRACE = _OP_TIMEOUT + 1.0

type _ConfigStoreKey = tuple[object, ...]


class _Closeable(Protocol):
    async def close(self) -> None: ...


_object_storage_client: ObjectStorageClient | None = None
_object_storage_config: ConfigParser | None = None
_object_storage_lock = asyncio.Lock()
_config_store_client: ConfigStoreClient | None = None
_config_store_client_key: _ConfigStoreKey | None = None
_retiring: dict[asyncio.Task[None], _Closeable] = {}


class StorageUnavailableError(Exception):
    """Object storage / Config Store is saturated or too slow; retry shortly."""


async def guarded_storage[T](
    factory: Callable[[], Awaitable[T]],
    *,
    op_timeout: float = _OP_TIMEOUT,
) -> T:
    """Run a storage/Config Store coroutine under backpressure + a short timeout.

    Takes a factory (not a coroutine) so the underlying request is only created
    once a slot is admitted. Semaphore saturation or a timeout is translated
    into a retryable :class:`StorageUnavailableError` (HTTP 503); every other
    exception (e.g. NotFound) propagates unchanged so real 404s stay 404s.
    """
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_ACQUIRE_TIMEOUT)
    except TimeoutError as exc:
        raise StorageUnavailableError(
            "Storage backend busy (backpressure); retry shortly."
        ) from exc
    try:
        async with asyncio.timeout(op_timeout):
            return await factory()
    except TimeoutError as exc:
        raise StorageUnavailableError(
            "Storage backend slow or unavailable; retry shortly."
        ) from exc
    finally:
        _semaphore.release()


async def get_object_storage_client() -> ObjectStorageClient:
    """Return the process-wide, pre-connected object storage client.

    Created and connected lazily behind a lock so concurrent first requests
    don't each build (and leak) their own client. Safe to share: the underlying
    aiobotocore client multiplexes concurrent calls over its connection pool.
    The client is rebuilt when the INI changes. File-backed storage returns a
    fresh client each call so its manifest reflects the latest uploads.
    """
    global _object_storage_client, _object_storage_config
    config = load_config()
    if _object_storage_client is not None and _object_storage_config is config:
        return _object_storage_client
    async with _object_storage_lock:
        config = load_config()
        if _object_storage_client is not None and _object_storage_config is config:
            return _object_storage_client
        client = await _connect_storage_client()
        if isinstance(client, FileStoreClient):
            return client
        previous = _object_storage_client
        _object_storage_client, _object_storage_config = client, config
        if previous is not None:
            _retire(previous)
        return client


async def _connect_storage_client() -> ObjectStorageClient:
    client = _build_storage_client()
    try:
        await guarded_storage(client.connect)
    except BaseException:
        await _close_quietly(client)
        raise
    return client


def _config_store_key() -> _ConfigStoreKey:
    """Identify the settings that DeviceData.config_store_client() builds from."""
    cfg = load_config()
    section = "config_store.client"
    ui_url = cfg.get(section, "ui_url", fallback=None)
    if cfg.getboolean(section, "use_internal_endpoint", fallback=False):
        return ("internal", cfg.get(section, "api_service", fallback=None), ui_url)
    cert = cfg.get("mtls", "tls_client_cert_path", fallback=None)
    key = cfg.get("mtls", "tls_client_key_path", fallback=None)
    verify = cfg.get(section, "verify", fallback=None)
    return (
        "external",
        cfg.get(section, "api_url", fallback=None),
        ui_url,
        verify,
        cert,
        key,
        file_fingerprint(cert),
        file_fingerprint(key),
        file_fingerprint(verify),
    )


def get_config_store_client(device_data: DeviceData) -> ConfigStoreClient:
    """Return the shared Config Store client for the current settings.

    Every device reaches the same endpoint, so one client (and its connection
    pool) serves all of them. A settings or TLS file change builds a new client.
    """
    global _config_store_client, _config_store_client_key
    key = _config_store_key()
    if _config_store_client is not None and _config_store_client_key == key:
        return _config_store_client
    previous = _config_store_client
    _config_store_client = device_data.config_store_client()
    _config_store_client_key = key
    if previous is not None:
        _retire(previous)
    return _config_store_client


def _retire(client: _Closeable) -> None:
    async def _close_later() -> None:
        await asyncio.sleep(_RETIRE_GRACE)
        await _close_quietly(client)

    task = asyncio.ensure_future(_close_later())
    _retiring[task] = client
    task.add_done_callback(lambda done: _retiring.pop(done, None))


async def _close_quietly(client: _Closeable) -> None:
    try:
        await client.close()
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
        logger.warning("Error closing storage client: %s", exc)


async def warm_storage_clients() -> None:
    """Best-effort pre-connect of the object storage client on app startup.

    Failures are logged and swallowed so the app still starts (e.g. in envs
    without S3 credentials); the client will be built lazily on first request.
    """
    try:
        await get_object_storage_client()
    except Exception as exc:  # noqa: BLE001 - startup warming must never crash boot
        logger.warning("Could not pre-connect object storage client at startup: %s", exc)


async def close_storage_clients() -> None:
    """Close all shared storage + Config Store clients (called on app shutdown)."""
    global _object_storage_client, _object_storage_config
    global _config_store_client, _config_store_client_key
    clients: list[_Closeable] = [
        c for c in (_object_storage_client, _config_store_client) if c is not None
    ]
    for task, client in list(_retiring.items()):
        task.cancel()
        clients.append(client)
    _retiring.clear()
    _object_storage_client = _object_storage_config = None
    _config_store_client = _config_store_client_key = None
    for client in clients:
        await _close_quietly(client)


def reset_storage_clients() -> None:
    """Drop shared clients without awaiting (test isolation helper)."""
    global _object_storage_client, _object_storage_config
    global _config_store_client, _config_store_client_key
    for task in _retiring:
        task.cancel()
    _retiring.clear()
    _object_storage_client = _object_storage_config = None
    _config_store_client = _config_store_client_key = None
