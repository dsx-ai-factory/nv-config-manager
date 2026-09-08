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
"""Standalone cache refresh service for DCIM device metadata.

This service runs independently from the API and periodically refreshes
the Redis cache with device metadata from the selected DCIM provider.
"""

import asyncio
import signal
import sys
from typing import Any

from nv_config_manager.common.log import LogCategory, configure_logging, get_logger
from nv_config_manager.config_store.config import settings
from nv_config_manager.config_store.core.device_cache_redis import (
    DeviceCacheService,
    background_cache_refresh_loop,
)

configure_logging(service="config-store-cache")
logger = get_logger(__name__, category=LogCategory.CACHE)

# Global state for graceful shutdown
shutdown_event = asyncio.Event()


def signal_handler(signum: int, frame: Any) -> None:
    """Handle shutdown signals."""
    logger.info("Received signal %s, initiating shutdown", signum)
    shutdown_event.set()


async def main() -> None:
    """Main entry point for the cache refresh service."""
    logger.info("Starting DCIM cache refresh service")

    cache_service = None
    refresh_task = None

    try:
        # Create cache service using factory method
        config = settings.config
        logger.info(
            "Connecting to Redis at %s:%d (db=%d)",
            config.get("redis", "host"),
            config.getint("redis", "port"),
            config.getint("redis", "db"),
        )
        cache_service = await DeviceCacheService.from_config(config)
        logger.info("Cache service initialized successfully")

        # Start background cache refresh loop
        cache_refresh_interval = config.getint(
            "dcim",
            "cache_refresh_interval",
            fallback=config.getint("nautobot", "cache_refresh_interval", fallback=3600),
        )
        logger.info(
            "Starting cache refresh loop (interval: %d seconds)",
            cache_refresh_interval,
        )
        refresh_task = asyncio.create_task(
            background_cache_refresh_loop(
                cache_service=cache_service,
                interval_seconds=cache_refresh_interval,
            )
        )

        # Wait for shutdown signal
        await shutdown_event.wait()

    except Exception as e:
        logger.error("Fatal error in cache refresh service: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Shutting down cache refresh service")

        # Cancel refresh task
        if refresh_task:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                logger.info("Cache refresh task cancelled")

        # Close connections
        if cache_service:
            await cache_service.dcim_client.close()
            logger.info("Closed DCIM client")

            await cache_service.redis_client.close()
            logger.info("Closed Redis connection")

        logger.info("Cache refresh service stopped")


def cli_main() -> None:
    """CLI entrypoint for cache refresh service."""
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the service
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")


if __name__ == "__main__":
    cli_main()
