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
"""Async Redis Client.

Shared async Redis client for all NVIDIA Config Manager services.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from datetime import timedelta
from typing import Any, cast

import redis.asyncio as redis_asyncio

logger = logging.getLogger(__name__)


def async_result[T](value: Awaitable[T] | T) -> Awaitable[T]:
    """Narrow redis-py's shared sync/async command annotations."""
    return cast("Awaitable[T]", value)


# Pickle protocol markers (first two bytes of any pickle payload)
_PICKLE_MARKERS = {b"\x80\x02", b"\x80\x03", b"\x80\x04", b"\x80\x05"}


def _is_pickle(data: bytes) -> bool:
    """Check if data looks like a pickle payload (vs JSON)."""
    return len(data) >= 2 and data[:2] in _PICKLE_MARKERS


class RedisClient:
    """Async Redis Client with caching utilities.

    Serializes values as JSON. Detects legacy pickle-format values on read
    and treats them as cache misses (returns None) to safely migrate away
    from pickle deserialization.
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
        """Initialize async Redis client.

        Args:
            host: Redis host
            port: Redis port
            db: Redis database number
            ssl: Whether to use SSL
            password: Redis password
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
        """
        params: dict[str, Any] = {
            "host": host,
            "port": port,
            "db": db,
            "ssl": ssl,
            "socket_timeout": socket_timeout,
            "socket_connect_timeout": socket_connect_timeout,
            "decode_responses": False,
        }
        if password:
            params["password"] = password

        self._redis = redis_asyncio.Redis(**params)

    @property
    def redis(self) -> redis_asyncio.Redis:
        """Get the underlying async Redis connection."""
        return self._redis

    async def ping(self) -> bool:
        """Test connection to Redis."""
        return await async_result(self._redis.ping())

    async def set(
        self, key: str, value: Any, ttl: timedelta | None = None, serialize: bool = True
    ) -> None:
        """Set a value in Redis.

        Args:
            key: Redis key
            value: Value to store
            ttl: Time-to-live (uses DEFAULT_TTL if None)
            serialize: Whether to JSON-serialize the value
        """
        if serialize:
            value = json.dumps(value).encode()
        await self._redis.set(key, value, ex=ttl or self.DEFAULT_TTL)

    async def get(self, key: str, deserialize: bool = True) -> Any | None:
        """Get a value from Redis.

        Args:
            key: Redis key
            deserialize: Whether to JSON-deserialize the value

        Returns:
            The stored value, or None if not found or if the value is
            a legacy pickle payload (treated as a cache miss).
        """
        value = await self._redis.get(key)
        if value and deserialize:
            if _is_pickle(value):
                logger.warning("Discarding legacy pickle value for key=%s (cache miss)", key)
                return None
            return json.loads(value)
        return value

    async def delete(self, key: str) -> None:
        """Delete a key from Redis."""
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        return bool(await self._redis.exists(key))

    async def keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern."""
        result = await self._redis.keys(pattern)
        return [k.decode() if isinstance(k, bytes) else k for k in result]

    async def hset(self, name: str, key: str, value: Any, serialize: bool = True) -> None:
        """Set a hash field value."""
        if serialize:
            value = json.dumps(value).encode()
        # redis-py accepts bytes when decode_responses=False; its hset annotation is narrower.
        await async_result(self._redis.hset(name, key, cast(Any, value)))

    async def hget(self, name: str, key: str, deserialize: bool = True) -> Any | None:
        """Get a hash field value."""
        value = cast(bytes | None, await async_result(self._redis.hget(name, key)))
        if value and deserialize:
            if _is_pickle(value):
                logger.warning("Discarding legacy pickle value for hash=%s key=%s", name, key)
                return None
            return json.loads(value)
        return value

    async def hdel(self, name: str, *keys: str) -> None:
        """Delete hash field(s)."""
        await async_result(self._redis.hdel(name, *keys))

    async def hgetall(self, name: str, deserialize: bool = True) -> dict[str, Any]:
        """Get all hash fields and values."""
        result = await async_result(self._redis.hgetall(name))
        decoded: dict[str, Any] = {}
        for k, v in result.items():
            key_str = k.decode() if isinstance(k, bytes) else k
            if deserialize:
                if _is_pickle(v):
                    logger.warning(
                        "Discarding legacy pickle value for hash=%s key=%s", name, key_str
                    )
                    continue
                decoded[key_str] = json.loads(v)
            else:
                decoded[key_str] = v
        return decoded

    async def expire(self, key: str, ttl: timedelta) -> None:
        """Set expiration on a key."""
        await self._redis.expire(key, ttl)

    async def ttl(self, key: str) -> int:
        """Get time-to-live for a key in seconds."""
        return int(await self._redis.ttl(key))

    async def setex(self, key: str, seconds: int, value: Any, serialize: bool = True) -> None:
        """Set a value with expiration in seconds.

        Args:
            key: Redis key
            seconds: Expiration time in seconds
            value: Value to store
            serialize: Whether to JSON-serialize the value
        """
        if serialize:
            value = json.dumps(value).encode()
        await self._redis.setex(key, seconds, value)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()
