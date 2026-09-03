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
"""Configuration-independent asynchronous Redis client."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from datetime import timedelta
from types import TracebackType
from typing import Any, TypedDict, cast

import redis.asyncio as redis_asyncio

logger = logging.getLogger(__name__)

# Pickle protocol markers (first two bytes of any pickle payload)
_PICKLE_MARKERS = {b"\x80\x02", b"\x80\x03", b"\x80\x04", b"\x80\x05"}


def _is_pickle(data: bytes) -> bool:
    """Check if data looks like a pickle payload (vs JSON)."""
    return len(data) >= 2 and data[:2] in _PICKLE_MARKERS


def async_result[ResultT](response: Awaitable[ResultT] | ResultT) -> Awaitable[ResultT]:
    """Narrow a raw redis-py command result to its awaitable form.

    The command mixins are shared between the sync and async clients, so their
    inline types union both returns; ``redis.asyncio`` always yields the awaitable.
    """
    return cast("Awaitable[ResultT]", response)


class RedisSettings(TypedDict):
    """Explicit connection settings accepted by :class:`RedisClient`."""

    host: str
    port: int
    db: int
    ssl: bool
    password: str | None
    socket_timeout: int
    socket_connect_timeout: int


class RedisClient:
    """Async Redis client with JSON and raw-value caching utilities.

    Connection settings are explicit so activities can use this client without
    depending on the application's configuration format.
    """

    DEFAULT_TTL = timedelta(days=14)

    def __init__(
        self,
        host: str,
        port: int = 6379,
        db: int = 0,
        ssl: bool = False,
        password: str | None = None,
        socket_timeout: int = 5,
        socket_connect_timeout: int = 5,
    ) -> None:
        """Initialize the asynchronous Redis connection."""
        self._redis: redis_asyncio.Redis = redis_asyncio.Redis(
            host=host,
            port=port,
            db=db,
            ssl=ssl,
            password=password or None,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            decode_responses=False,
        )

    async def __aenter__(self) -> RedisClient:
        """Return this client for use as an asynchronous context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the Redis connection when leaving an asynchronous context."""
        await self.close()

    @property
    def redis(self) -> redis_asyncio.Redis:
        """Get the underlying asynchronous Redis connection."""
        return self._redis

    async def ping(self) -> bool:
        """Test the Redis connection."""
        return await async_result(self._redis.ping())

    async def set(
        self, key: str, value: Any, ttl: timedelta | None = None, serialize: bool = True
    ) -> None:
        """Set a value, using the default TTL when one is not supplied."""
        if serialize:
            value = json.dumps(value).encode()
        await self._redis.set(key, value, ex=ttl or self.DEFAULT_TTL)

    async def get(self, key: str, deserialize: bool = True) -> Any | None:
        """Get a value, treating legacy pickle values as cache misses."""
        value = await async_result(self._redis.get(key))
        if value and deserialize:
            if _is_pickle(value):
                logger.warning("Discarding legacy pickle value for key=%s (cache miss)", key)
                return None
            return json.loads(value)
        return value

    async def delete(self, key: str) -> None:
        """Delete a key."""
        await async_result(self._redis.delete(key))

    async def exists(self, key: str) -> bool:
        """Check whether a key exists."""
        return bool(await async_result(self._redis.exists(key)))

    async def keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern."""
        result = await async_result(self._redis.keys(pattern))
        return [key.decode() if isinstance(key, bytes) else key for key in result]

    async def hset(self, name: str, key: str, value: Any, serialize: bool = True) -> None:
        """Set a hash field value."""
        if serialize:
            value = json.dumps(value).encode()
        # redis-py types hash values as str; this client runs with decode_responses=False.
        await async_result(self._redis.hset(name, key, cast("str", value)))

    async def hget(self, name: str, key: str, deserialize: bool = True) -> Any | None:
        """Get a hash field value."""
        raw = await async_result(self._redis.hget(name, key))
        value = cast("bytes | None", raw)
        if value and deserialize:
            if _is_pickle(value):
                logger.warning("Discarding legacy pickle value for hash=%s key=%s", name, key)
                return None
            return json.loads(value)
        return value

    async def hdel(self, name: str, *keys: str) -> None:
        """Delete one or more hash fields."""
        await async_result(self._redis.hdel(name, *keys))

    async def hgetall(self, name: str, deserialize: bool = True) -> dict[str, Any]:
        """Get all hash fields and values."""
        result = await async_result(self._redis.hgetall(name))
        decoded: dict[str, Any] = {}
        for key, value in result.items():
            key_str = key.decode() if isinstance(key, bytes) else key
            if deserialize:
                if _is_pickle(value):
                    logger.warning(
                        "Discarding legacy pickle value for hash=%s key=%s", name, key_str
                    )
                    continue
                decoded[key_str] = json.loads(value)
            else:
                decoded[key_str] = value
        return decoded

    async def expire(self, key: str, ttl: timedelta) -> None:
        """Set expiration on a key."""
        await self._redis.expire(key, ttl)

    async def ttl(self, key: str) -> int:
        """Get a key's time-to-live in seconds."""
        return await async_result(self._redis.ttl(key))

    async def setex(self, key: str, seconds: int, value: Any, serialize: bool = True) -> None:
        """Set a value with an expiration expressed in seconds."""
        if serialize:
            value = json.dumps(value).encode()
        await self._redis.setex(key, seconds, value)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
