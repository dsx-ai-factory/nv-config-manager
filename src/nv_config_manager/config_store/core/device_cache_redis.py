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
"""Redis-based device metadata cache service."""

from __future__ import annotations

import asyncio
from configparser import ConfigParser
from datetime import UTC, datetime
from typing import Any

from nv_config_manager.common.client import RedisClient
from nv_config_manager.common.config import dcim_cache_ttl, dcim_client, load_config, redis_client
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMClient, DeviceMetadata
from nv_config_manager_workflows.clients import async_result

logger = get_logger(__name__, category=LogCategory.CACHE)


class CachedDeviceMetadata:
    """Wrapper for cached device metadata with timestamp."""

    def __init__(self, metadata: DeviceMetadata, cached_at: datetime) -> None:
        """Initialize cached metadata.

        Args:
            metadata: Device metadata from the selected DCIM provider
            cached_at: When this was cached
        """
        self.metadata = metadata
        self.cached_at = cached_at

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "cached_at": self.cached_at.isoformat(),
        }


class DeviceCacheService:
    """Redis-based service for managing device metadata cache.

    This service maintains a cache of device metadata from the selected DCIM in Redis
    to avoid expensive GraphQL queries on every API request. It supports:
    - Background refresh of all devices at regular intervals
    - On-demand population for cache misses
    - JSON serialization via to_dict()/from_dict()
    - TTL-based expiration
    - Device name index for fast search
    - Active device set tracking for filtering removed devices
    """

    CACHE_KEY_PREFIX = "config_store_device:"
    DEVICE_INDEX_KEY = "config_store_device_index"  # Hash mapping name -> device ID
    ACTIVE_SET_KEY = "config_store_active_device_set"  # Set of active device IDs
    CACHE_TTL = 86400  # 24 hours default TTL

    def __init__(
        self,
        redis_client: RedisClient,
        dcim_client: DCIMClient,
        cache_ttl: int = CACHE_TTL,
    ) -> None:
        """Initialize cache service.

        Args:
            redis_client: Redis async client for caching
            dcim_client: Client for querying the selected DCIM provider
            cache_ttl: Cache TTL in seconds (default 24 hours)
        """
        self.redis_client = redis_client
        self.dcim_client = dcim_client
        self.cache_ttl = cache_ttl

    @classmethod
    async def from_config(
        cls,
        config: ConfigParser | None = None,
        provider_client: DCIMClient | None = None,
    ) -> DeviceCacheService:
        """Create DeviceCacheService from INI configuration.

        Args:
            config: ConfigParser with redis and DCIM provider sections. If None, loads from default.
            provider_client: Reuse an already initialized DCIM provider client when supplied.

        Returns:
            Configured and connected DeviceCacheService instance
        """
        if config is None:
            config = load_config()

        # Create Redis client from common module
        redis = redis_client(config=config)

        # Test connection
        socket_connect_timeout = config.getint("redis", "socket_connect_timeout", fallback=5)
        try:
            await asyncio.wait_for(redis.ping(), timeout=socket_connect_timeout)
            logger.info(
                "Redis connection established: %s:%d (db=%d) ssl=%s timeout=%ds",
                config.get("redis", "host"),
                config.getint("redis", "port"),
                config.getint("redis", "db"),
                config.getboolean("redis", "ssl"),
                socket_connect_timeout,
            )
        except TimeoutError:
            logger.error(
                "Failed to connect to Redis at %s:%d - connection timeout after %ds",
                config.get("redis", "host"),
                config.getint("redis", "port"),
                socket_connect_timeout,
            )
            raise
        except Exception as e:
            logger.error(
                "Failed to connect to Redis at %s:%d - %s",
                config.get("redis", "host"),
                config.getint("redis", "port"),
                str(e),
            )
            raise

        return cls(
            redis_client=redis,
            dcim_client=provider_client or dcim_client(config),
            cache_ttl=dcim_cache_ttl(config, default=cls.CACHE_TTL),
        )

    def _make_key(self, device_uuid: str) -> str:
        """Generate Redis key for a provider-owned device identifier.

        Args:
            device_uuid: DCIM provider device identifier

        Returns:
            Redis key string
        """
        return f"{self.CACHE_KEY_PREFIX}{str(device_uuid)}"

    async def get_device_metadata(
        self,
        device_uuid: str,
        refresh_on_miss: bool = True,
    ) -> DeviceMetadata | None:
        """Get device metadata from cache.

        Args:
            device_uuid: DCIM provider device identifier
            refresh_on_miss: If True, query the selected DCIM on cache miss

        Returns:
            DeviceMetadata or None if not found
        """
        key = self._make_key(device_uuid)

        # Try cache first
        try:
            cached = await self.redis_client.get(key)
            if cached and isinstance(cached, dict):
                logger.debug("Cache hit for device %s", device_uuid)
                meta_dict = cached.get("metadata", {})
                return DeviceMetadata.from_dict(meta_dict)
        except Exception as e:
            logger.error("Failed to get device %s from cache: %s", device_uuid, e)

        # Cache miss - optionally fetch from the selected DCIM.
        if refresh_on_miss:
            logger.info("Cache miss for device %s, fetching from DCIM", device_uuid)
            metadata = await self.refresh_device(device_uuid)
            return metadata

        return None

    async def refresh_device(self, device_uuid: str) -> DeviceMetadata | None:
        """Refresh metadata for a single device.

        Args:
            device_uuid: DCIM provider device identifier

        Returns:
            DeviceMetadata or None if not found
        """
        if not self.dcim_client.is_valid_device_id(device_uuid):
            logger.warning(
                "Invalid device identifier for configured DCIM provider: %s", device_uuid
            )
            return None

        try:
            metadata = await self.dcim_client.get_device_metadata(device_uuid)

            if not metadata:
                logger.warning("Device %s not found in DCIM", device_uuid)
                return None

            metadata.device_url = self.dcim_client.get_device_ui_url(metadata.device_id)

            # Cache it
            await self._cache_metadata(device_uuid, metadata)

            logger.info("Refreshed cache for device %s", device_uuid)
            return metadata

        except Exception as e:
            logger.error("Failed to refresh device %s: %s", device_uuid, e)
            return None

    async def refresh_all_devices(self, limit: int = 1000) -> int:
        """Refresh metadata for all devices from the selected DCIM provider.

        Fetches all nv-config-manager devices, updates the metadata cache, and replaces
        the active device set so inactive devices can be identified.

        Args:
            limit: Maximum number of devices to fetch

        Returns:
            Number of devices refreshed
        """
        logger.info("Starting full cache refresh (limit=%d)", limit)

        try:
            devices = await self.dcim_client.get_managed_device_metadata(page_size=limit)

            if not devices:
                logger.warning("No devices returned from DCIM")
                return 0

            active_uuids: set[str] = set()
            count = 0
            for metadata in devices:
                try:
                    if not self.dcim_client.is_valid_device_id(metadata.device_id):
                        logger.error(
                            "Configured DCIM provider returned an invalid device identifier: %s",
                            metadata.device_id,
                        )
                        continue
                    metadata.device_url = self.dcim_client.get_device_ui_url(metadata.device_id)

                    device_uuid = metadata.device_id
                    await self._cache_metadata(device_uuid, metadata)
                    active_uuids.add(device_uuid)
                    count += 1
                except Exception as e:
                    logger.error("Failed to cache device %s: %s", metadata.device_id, e)

            await self._replace_active_set(active_uuids)

            logger.info("Refreshed cache for %d devices", count)
            return count

        except Exception as e:
            logger.error("Failed to refresh all devices: %s", e)
            return 0

    async def search_devices_by_name(
        self, query: str, limit: int = 10
    ) -> list[tuple[str, DeviceMetadata]]:
        """Search devices by name (partial, case-insensitive match).

        Uses the device name index for fast lookups.

        Args:
            query: Search query (partial name match)
            limit: Maximum number of results

        Returns:
            List of (device identifier, metadata) tuples matching the query
        """
        query_lower = query.lower()
        matches: list[tuple[str, DeviceMetadata]] = []

        try:
            # Get all device names from the index (hash)
            # This returns a dict of {name: device_id}
            # Use deserialize=False since identifiers are stored as plain strings.
            name_index = await self.redis_client.hgetall(self.DEVICE_INDEX_KEY, deserialize=False)

            if not name_index:
                logger.debug("Device index is empty")
                return []

            # Search through names for partial matches
            for name, device_id_bytes in name_index.items():
                if len(matches) >= limit:
                    break

                device_id = (
                    device_id_bytes.decode("utf-8")
                    if isinstance(device_id_bytes, bytes)
                    else device_id_bytes
                )

                # Check if query matches (case-insensitive partial match)
                if query_lower in name.lower():
                    try:
                        # Get full metadata from cache
                        metadata = await self.get_device_metadata(device_id, refresh_on_miss=False)
                        if metadata:
                            matches.append((device_id, metadata))
                    except Exception as e:
                        logger.debug("Failed to get metadata for %s: %s", device_id, e)
                        continue

            return matches

        except Exception as e:
            logger.error("Failed to search devices by name: %s", e)
            return []

    async def is_device_active(self, device_uuid: str) -> bool:
        """Check if a device is in the active nv-config-manager device set.

        Args:
            device_uuid: DCIM provider device identifier

        Returns:
            True if the device is currently active in nv-config-manager
        """
        try:
            return bool(
                await async_result(
                    self.redis_client.redis.sismember(self.ACTIVE_SET_KEY, str(device_uuid))
                )
            )
        except Exception as e:
            logger.error("Failed to check active status for %s: %s", device_uuid, e)
            return True  # Default to active on error to avoid hiding devices

    async def get_active_device_uuids(self) -> set[str]:
        """Get the set of all active provider-owned device identifiers.

        Returns:
            Device identifiers currently active in nv-config-manager
        """
        try:
            members = await async_result(self.redis_client.redis.smembers(self.ACTIVE_SET_KEY))
            return {m.decode() if isinstance(m, bytes) else m for m in members}
        except Exception as e:
            logger.error("Failed to get active device set: %s", e)
            return set()

    async def _replace_active_set(self, active_uuids: set[str]) -> None:
        """Atomically replace the active device identifier set in Redis.

        Args:
            active_uuids: Provider-owned identifiers for currently active devices
        """
        try:
            pipe = self.redis_client.redis.pipeline()
            pipe.delete(self.ACTIVE_SET_KEY)
            if active_uuids:
                pipe.sadd(self.ACTIVE_SET_KEY, *active_uuids)
            await pipe.execute()
            logger.info("Updated active device set: %d devices", len(active_uuids))
        except Exception as e:
            logger.error("Failed to update active device set: %s", e)

    async def _cache_metadata(self, device_uuid: str, metadata: DeviceMetadata) -> None:
        """Store device metadata in cache and update the name index.

        Args:
            device_uuid: DCIM provider device identifier
            metadata: Device metadata to cache
        """
        key = self._make_key(device_uuid)
        cached = CachedDeviceMetadata(
            metadata=metadata,
            cached_at=datetime.now(UTC),
        )

        if self.cache_ttl:
            await self.redis_client.setex(key, self.cache_ttl, cached.to_dict())
        else:
            await self.redis_client.set(key, cached.to_dict(), ttl=None)

        # Update the device name index (hash: name -> ID, no serialization for plain strings)
        await self.redis_client.hset(
            self.DEVICE_INDEX_KEY, metadata.name, str(device_uuid), serialize=False
        )

        logger.debug("Cached device %s (TTL=%ds)", device_uuid, self.cache_ttl)

    async def delete_device(self, device_uuid: str) -> None:
        """Remove device from cache, index, and active set.

        Args:
            device_uuid: DCIM provider device identifier
        """
        metadata = await self.get_device_metadata(device_uuid, refresh_on_miss=False)

        key = self._make_key(device_uuid)
        await self.redis_client.delete(key)

        if metadata:
            await self.redis_client.hdel(self.DEVICE_INDEX_KEY, metadata.name)

        try:
            await async_result(self.redis_client.redis.srem(self.ACTIVE_SET_KEY, str(device_uuid)))
        except Exception as e:
            logger.error("Failed to remove %s from active set: %s", device_uuid, e)

        logger.info("Removed device %s from cache", device_uuid)

    async def clear_cache(self) -> int:
        """Clear all device metadata from cache and index.

        Returns:
            Number of keys deleted
        """
        keys = await self.redis_client.keys(f"{self.CACHE_KEY_PREFIX}*")

        # Delete all device keys
        for key in keys:
            await self.redis_client.delete(key)

        # Clear the device name index
        await self.redis_client.delete(self.DEVICE_INDEX_KEY)

        count = len(keys)
        logger.info("Cleared %d devices from cache and index", count)
        return count

    async def get_cache_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        keys = await self.redis_client.keys(f"{self.CACHE_KEY_PREFIX}*")
        total_devices = len(keys)

        # Sample a few to check TTL
        sample_ttls = []
        for key in keys[: min(10, len(keys))]:
            ttl = await self.redis_client.ttl(key)
            if ttl > 0:
                sample_ttls.append(ttl)

        avg_ttl = sum(sample_ttls) / len(sample_ttls) if sample_ttls else 0

        return {
            "total_devices": total_devices,
            "average_ttl_seconds": int(avg_ttl),
            "cache_key_prefix": self.CACHE_KEY_PREFIX,
            "configured_ttl_seconds": self.cache_ttl,
        }


async def background_cache_refresh_loop(
    cache_service: DeviceCacheService,
    interval_seconds: int = 3600,  # 1 hour default
) -> None:
    """Background task to periodically refresh device metadata cache.

    Args:
        cache_service: Device cache service
        interval_seconds: Refresh interval in seconds
    """
    logger.info("Starting background cache refresh loop (interval=%ds)", interval_seconds)

    while True:
        try:
            count = await cache_service.refresh_all_devices()
            logger.info("Background refresh completed: %d devices", count)

        except Exception as e:
            logger.error("Error in background refresh loop: %s", e)

        # Wait for next interval
        await asyncio.sleep(interval_seconds)
