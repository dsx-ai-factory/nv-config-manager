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
"""Utility methods for parsing NB NATS messages."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import TYPE_CHECKING

import nats.js

from nv_config_manager.common.config import (
    LogCategory,
    NATSConnectionManager,
    get_logger,
    is_aggregate_environment,
    is_local_environment,
    nats_config_manager_api_prefix,
    nats_connection,
    nats_render_change_config,
    redis_client,
)
from nv_config_manager.dcim import DCIMClient, dcim_client_session

if TYPE_CHECKING:
    from nv_config_manager.common.client import RedisClient


class DeviceNotEnabledError(Exception):
    """To be raised if a device is not enabled for NVIDIA Config Manager."""


def _get_queue_redis_client() -> RedisClient | None:
    """Create a Redis client for queue operations."""
    if is_local_environment():
        return None

    return redis_client(db_key="lock_db")


async def _close_queue_redis_client(client: RedisClient | None) -> None:
    """Close a queue Redis client if one was created for this operation."""
    if client is None:
        return
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def mark_queued(device_uuid: str, client: RedisClient | None = None) -> None:
    """Mark a device as queued for rendering."""
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, skip Redis operations
        return

    try:
        queue_key = f"{device_uuid}_queued"
        await client.setex(queue_key, 60, 1, serialize=False)  # TTL of 60 seconds
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def is_queued(device_uuid: str, client: RedisClient | None = None) -> bool:
    """Check if a device is already queued for rendering."""
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, never consider as queued
        return False

    try:
        queue_key = f"{device_uuid}_queued"
        return await client.exists(queue_key)
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def clear_queued(device_uuid: str, client: RedisClient | None = None) -> None:
    """Clear the queued flag for a device."""
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, skip Redis operations
        return

    try:
        queue_key = f"{device_uuid}_queued"
        await client.delete(queue_key)
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def should_run(device_uuid: str, dcim_client: DCIMClient) -> bool:
    """Return true if device is enabled for rendering."""
    device = await dcim_client.get_render_device_status(device_uuid)
    if device is None or not device.render_enabled:
        return False

    env_aggregate_managed = is_aggregate_environment()
    return env_aggregate_managed == device.is_aggregate_managed


async def _process_single_device_enqueue(
    device_uuid: str,
    commit_message: str,
    user: str,
    timestamp: str,
    jetstream: nats.js.JetStreamContext,
    logger: logging.Logger | logging.LoggerAdapter,
    queue_client: RedisClient | None,
    dcim_client: DCIMClient,
) -> dict[str, str] | None:
    """
    Process a single device render operation.

    Args:
        device_uuid: Device UUID to process
        commit_message: Commit message for the render
        user: User initiating the render
        timestamp: Timestamp for the render
        jetstream: NATS JetStream instance to use for publishing
        logger: Logger instance

    Returns:
        None if successful, dict with error details if failed
    """
    try:
        # Check if device is already queued for rendering
        if await is_queued(device_uuid, queue_client):
            logger.info(
                "Device %s already has a pending render, skipping duplicate queue request",
                device_uuid,
            )
            return None

        if not await should_run(device_uuid, dcim_client):
            return {
                "device_uuid": device_uuid,
                "error": f"{device_uuid} is not enabled for configuration renders.",
            }

        # Mark device as queued before publishing
        await mark_queued(device_uuid, queue_client)

        # Publish message using provided jetstream connection
        message = {
            "device_id": device_uuid,
            "commit_message": commit_message,
            "user": user,
            "@timestamp": timestamp,
        }

        stream, subject = nats_render_change_config()
        await jetstream.publish(
            subject=subject,
            payload=json.dumps(message).encode("utf-8"),
            stream=stream,
        )

        logger.debug(f"Successfully queued device {device_uuid}")
        return None  # Success case

    except Exception as exc:
        # Clear the queued flag if we set it but publishing failed
        await clear_queued(device_uuid, queue_client)
        error_msg = str(exc)
        logger.warning(f"Failed to queue device {device_uuid}: {error_msg}")
        return {"device_uuid": device_uuid, "error": error_msg}


async def queue_render(
    device_uuid: str,
    commit_message: str,
    user: str,
    timestamp: str,
    dcim_client: DCIMClient | None = None,
) -> None:
    """Queue a device render via NATS."""
    logger = get_logger(__name__, category=LogCategory.RENDER_EVENT)

    # Try to use shared connection first, fallback to creating new one
    connection_manager = NATSConnectionManager()
    nats_conn = connection_manager.get_connection()

    if nats_conn is None or nats_conn.is_closed:
        # Fallback to creating a new connection if no shared connection available
        logger.info("No shared NATS connection available, creating new one")
        nats_conn = await nats_connection()
        should_close = True
    else:
        should_close = False

    try:
        jetstream = nats_conn.jetstream(prefix=nats_config_manager_api_prefix())
        queue_client = _get_queue_redis_client()

        # Use the shared processing function
        try:
            if dcim_client is None:
                async with dcim_client_session() as session_client:
                    result = await _process_single_device_enqueue(
                        device_uuid,
                        commit_message,
                        user,
                        timestamp,
                        jetstream,
                        logger,
                        queue_client,
                        session_client,
                    )
            else:
                result = await _process_single_device_enqueue(
                    device_uuid,
                    commit_message,
                    user,
                    timestamp,
                    jetstream,
                    logger,
                    queue_client,
                    dcim_client,
                )
        finally:
            await _close_queue_redis_client(queue_client)

        if result is not None:
            # There was an error
            error_msg = result["error"]
            if "not enabled for configuration renders" in error_msg:
                raise DeviceNotEnabledError(error_msg)
            else:
                raise Exception(error_msg)

        # Log successful publishing for backward compatibility
        logger.info("Published message for device %s to NATS stream", device_uuid)

    finally:
        # Only close if we created the connection ourselves
        if should_close:
            await nats_conn.close()


async def queue_render_batch(
    device_uuids: list[str],
    commit_message: str,
    user: str,
    timestamp: str,
    max_concurrency: int = 20,
    dcim_client: DCIMClient | None = None,
) -> tuple[int, list[dict[str, str]]]:
    """
    Queue renders for multiple devices in parallel with controlled concurrency.

    Args:
        device_uuids: List of device UUIDs to queue renders for
        commit_message: Commit message for all renders
        user: User initiating the renders
        timestamp: Timestamp for all renders
        max_concurrency: Maximum number of concurrent operations

    Returns:
        Tuple of (queued_count, failed_devices)
    """

    logger = get_logger(__name__, category=LogCategory.RENDER_EVENT)
    logger.info(
        f"Starting batch queue operation for {len(device_uuids)} devices with max concurrency {max_concurrency}"
    )

    # Pre-establish connections for reuse
    nats_conn = None
    nats_should_close = False

    try:
        # Set up shared NATS connection
        connection_manager = NATSConnectionManager()
        nats_conn = connection_manager.get_connection()

        if nats_conn is None or nats_conn.is_closed:
            logger.info("Establishing shared NATS connection for batch operation")
            nats_conn = await nats_connection()
            connection_manager.set_connection(nats_conn)
            nats_should_close = True

        jetstream = nats_conn.jetstream(prefix=nats_config_manager_api_prefix())
        queue_client = _get_queue_redis_client()

        # Create semaphore to limit concurrency across all devices
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process_single_device(
            device_uuid: str, session_client: DCIMClient
        ) -> dict[str, str] | None:
            """Process a single device with concurrency control."""
            async with semaphore:
                return await _process_single_device_enqueue(
                    device_uuid,
                    commit_message,
                    user,
                    timestamp,
                    jetstream,
                    logger,
                    queue_client,
                    session_client,
                )

        try:
            if dcim_client is None:
                async with dcim_client_session() as session_client:
                    tasks = [
                        process_single_device(device_uuid, session_client)
                        for device_uuid in device_uuids
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                tasks = [
                    process_single_device(device_uuid, dcim_client) for device_uuid in device_uuids
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await _close_queue_redis_client(queue_client)

        failed_devices = []

        for result in results:
            if isinstance(result, Exception):
                # This shouldn't happen due to our exception handling, but just in case
                failed_devices.append({"device_uuid": "unknown", "error": str(result)})
            elif isinstance(result, dict):
                # Failed device (result is dict[str, str])
                failed_devices.append(result)
            # If result is None, it was successful - no action needed

        queued_count = len(device_uuids) - len(failed_devices)
        logger.info(
            f"Batch operation completed: {queued_count} queued, {len(failed_devices)} failed"
        )
        return queued_count, failed_devices

    finally:
        # Clean up NATS connection if we created it
        if nats_should_close and nats_conn and not nats_conn.is_closed:
            connection_manager.clear_connection()
            await nats_conn.close()
