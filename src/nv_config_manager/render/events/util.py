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
from enum import Enum
from typing import TYPE_CHECKING

import nats.js
from nv_config_manager_infrastructure.redis import async_result

from nv_config_manager.common.client.factory import redis_client
from nv_config_manager.common.config.environment import (
    is_aggregate_environment,
    is_local_environment,
)
from nv_config_manager.common.config.nats import (
    NATSConnectionManager,
    nats_config_manager_api_prefix,
    nats_connection,
    nats_render_change_config,
)
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMClient, dcim_client_session
from nv_config_manager.render.exceptions import is_transient_dependency_error

if TYPE_CHECKING:
    from nv_config_manager.common.client.redis import RedisClient


QUEUED_FLAG_TTL_SECONDS = 60
# Outlasts the default delivery limit's retries of one change event, and is
# renewed by every attempt that queues a device or fails.
PUBLISHED_DEVICES_TTL_SECONDS = 3600

_RETAKE_CLAIM = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
return false
"""
_RELEASE_CLAIM = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class DeviceNotEnabledError(Exception):
    """To be raised if a device is not enabled for NVIDIA Config Manager."""


class EnqueueResult(Enum):
    """Outcome of a render request that did not fail."""

    PUBLISHED = "published"
    ALREADY_QUEUED = "already_queued"


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


async def is_queued(device_uuid: str, client: RedisClient | None = None) -> bool:
    """Return whether a device already has a pending render, without claiming it."""
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, never consider as queued
        return False

    try:
        return bool(await client.redis.exists(f"{device_uuid}_queued"))
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def claim_queued(
    device_uuid: str,
    client: RedisClient | None = None,
    token: str | None = None,
    retake: bool = False,
) -> bool:
    """Mark a device as queued for rendering unless it already is.

    Returns whether this caller set the flag. Checking and setting in one
    command keeps two concurrent producers from both publishing a render.

    ``token`` identifies the request making the claim, so a retry of that
    request can recognize a claim it left behind. ``retake`` renews the flag
    when it still holds that token, and otherwise claims it as usual, so a
    claim another producer made in the meantime is never overwritten.
    """
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, never consider as queued
        return True

    try:
        queue_key = f"{device_uuid}_queued"
        if retake and token is not None:
            retake_claim = client.redis.register_script(_RETAKE_CLAIM)
            if await retake_claim(keys=[queue_key], args=[token, QUEUED_FLAG_TTL_SECONDS]):
                return True
        return bool(
            await client.redis.set(queue_key, token or 1, nx=True, ex=QUEUED_FLAG_TTL_SECONDS)
        )
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def _is_own_claim(device_uuid: str, token: str | None, client: RedisClient | None) -> bool:
    """Return whether the queued flag was set by an earlier attempt at this request.

    Such a flag outlived a publish that failed: an attempt that published is
    recorded for the request and never reaches this check.
    """
    if token is None or client is None:
        return False
    value = await client.redis.get(f"{device_uuid}_queued")
    return value in (token, token.encode())


async def clear_queued(
    device_uuid: str, client: RedisClient | None = None, token: str | None = None
) -> None:
    """Clear the queued flag for a device.

    With ``token``, the flag is cleared only while it still holds that
    request's claim, so a claim another producer made since is left alone.
    """
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, skip Redis operations
        return

    try:
        queue_key = f"{device_uuid}_queued"
        if token is None:
            await client.delete(queue_key)
        else:
            await client.redis.register_script(_RELEASE_CLAIM)(keys=[queue_key], args=[token])
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


def _published_key(retry_key: str) -> str:
    return f"render_event_published:{retry_key}"


async def _load_published(
    retry_key: str | None,
    client: RedisClient | None,
    logger: logging.Logger | logging.LoggerAdapter,
) -> set[str]:
    """Return the devices an earlier attempt at this request already queued."""
    if retry_key is None or client is None:
        return set()
    try:
        members = await async_result(client.redis.smembers(_published_key(retry_key)))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not read devices already queued for this request: %s", exc)
        return set()
    return {member.decode() if isinstance(member, bytes) else member for member in members}


async def _record_published(
    retry_key: str | None,
    device_uuids: list[str],
    client: RedisClient | None,
    logger: logging.Logger | logging.LoggerAdapter,
) -> None:
    """Remember the devices this attempt queued so a retry skips them.

    Every attempt that queues a device or fails renews the record.
    """
    if retry_key is None or client is None:
        return
    key = _published_key(retry_key)
    try:
        async with client.redis.pipeline(transaction=True) as pipe:
            if device_uuids:
                pipe.sadd(key, *device_uuids)
            pipe.expire(key, PUBLISHED_DEVICES_TTL_SECONDS)
            await pipe.execute()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not record queued devices, so a retry may queue them again: %s", exc)


async def renew_published(retry_key: str) -> None:
    """Keep the record of devices a request queued while that request is still retried.

    For a failure that happens before the request reaches ``queue_render_batch``,
    which renews the record itself.
    """
    logger = get_logger(__name__, category=LogCategory.RENDER_EVENT)
    client = _get_queue_redis_client()
    try:
        await _record_published(retry_key, [], client, logger)
    finally:
        await _close_queue_redis_client(client)


def _log_duplicate(logger: logging.Logger | logging.LoggerAdapter, device_uuid: str) -> None:
    logger.info(
        "Device %s already has a pending render, skipping duplicate queue request", device_uuid
    )


async def _process_single_device_enqueue(
    device_uuid: str,
    commit_message: str,
    user: str,
    timestamp: str,
    jetstream: nats.js.JetStreamContext,
    logger: logging.Logger | logging.LoggerAdapter,
    queue_client: RedisClient | None,
    dcim_client: DCIMClient,
    claim_token: str | None = None,
) -> EnqueueResult | dict[str, str]:
    """
    Process a single device render operation.

    Args:
        device_uuid: Device UUID to process
        commit_message: Commit message for the render
        user: User initiating the render
        timestamp: Timestamp for the render
        jetstream: NATS JetStream instance to use for publishing
        logger: Logger instance
        claim_token: Identifies a request that is repeated after a transient
            failure, so a retry is not skipped as a duplicate of its own
            failed attempt

    Returns:
        The outcome if successful, dict with error details if failed
    """
    claimed = False
    try:
        # Skips known duplicates without a DCIM lookup. The claim below still
        # decides between producers that both pass this check.
        own_claim = False
        if await is_queued(device_uuid, queue_client):
            own_claim = await _is_own_claim(device_uuid, claim_token, queue_client)
            if not own_claim:
                _log_duplicate(logger, device_uuid)
                return EnqueueResult.ALREADY_QUEUED

        # Checked before claiming: a claim that is then dropped would make a
        # concurrent request for a newly enabled device skip as a duplicate.
        if not await should_run(device_uuid, dcim_client):
            return {
                "device_uuid": device_uuid,
                "error": f"{device_uuid} is not enabled for configuration renders.",
            }

        claimed = await claim_queued(device_uuid, queue_client, token=claim_token, retake=own_claim)
        if not claimed:
            _log_duplicate(logger, device_uuid)
            return EnqueueResult.ALREADY_QUEUED

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
        return EnqueueResult.PUBLISHED

    except Exception as exc:
        # Another producer's flag still stands for its own pending render.
        if claimed:
            try:
                await clear_queued(device_uuid, queue_client, token=claim_token)
            except Exception as clear_exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Could not clear the queued flag for %s, so requests in the next "
                    "%d seconds are skipped as duplicates: %s",
                    device_uuid,
                    QUEUED_FLAG_TTL_SECONDS,
                    clear_exc,
                )
        if is_transient_dependency_error(exc):
            raise
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

        if isinstance(result, dict):
            # There was an error
            error_msg = result["error"]
            if "not enabled for configuration renders" in error_msg:
                raise DeviceNotEnabledError(error_msg)
            else:
                raise Exception(error_msg)

        if result is EnqueueResult.PUBLISHED:
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
    raise_transient_errors: bool = False,
    retry_key: str | None = None,
) -> tuple[int, list[dict[str, str]]]:
    """
    Queue renders for multiple devices in parallel with controlled concurrency.

    Args:
        device_uuids: List of device UUIDs to queue renders for
        commit_message: Commit message for all renders
        user: User initiating the renders
        timestamp: Timestamp for all renders
        max_concurrency: Maximum number of concurrent operations
        raise_transient_errors: Raise the first transient DCIM or Config Store
            failure after the batch completes, so the caller can retry it,
            instead of reporting it as a failed device
        retry_key: Identifies a request the caller repeats after a transient
            failure. Devices queued by an earlier attempt are skipped, so the
            retry queues only the devices that failed, and a queued flag an
            earlier attempt left behind does not make the retry skip a device

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
        ) -> EnqueueResult | dict[str, str]:
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
                    claim_token=retry_key,
                )

        failed_devices = []
        transient_errors: list[BaseException] = []

        try:
            published = await _load_published(retry_key, queue_client, logger)
            pending = [device_uuid for device_uuid in device_uuids if device_uuid not in published]
            if published:
                logger.info(
                    "Skipping %d devices an earlier attempt already queued",
                    len(device_uuids) - len(pending),
                )

            if dcim_client is None:
                async with dcim_client_session() as session_client:
                    tasks = [
                        process_single_device(device_uuid, session_client)
                        for device_uuid in pending
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                tasks = [process_single_device(device_uuid, dcim_client) for device_uuid in pending]
                results = await asyncio.gather(*tasks, return_exceptions=True)

            queued_devices = []
            for device_uuid, result in zip(pending, results, strict=True):
                if isinstance(result, BaseException):
                    if is_transient_dependency_error(result):
                        transient_errors.append(result)
                    failed_devices.append({"device_uuid": device_uuid, "error": str(result)})
                elif isinstance(result, dict):
                    # Failed device (result is dict[str, str])
                    failed_devices.append(result)
                elif result is EnqueueResult.PUBLISHED:
                    # An already-queued device is left out: its flag can outlive
                    # a publish that failed, so it must stay eligible for a retry.
                    queued_devices.append(device_uuid)

            # Recorded even when this batch succeeds: the caller retries the whole
            # request if another of its batches fails.
            if queued_devices or transient_errors:
                await _record_published(retry_key, queued_devices, queue_client, logger)
        finally:
            await _close_queue_redis_client(queue_client)

        queued_count = len(device_uuids) - len(failed_devices)
        logger.info(
            f"Batch operation completed: {queued_count} queued, {len(failed_devices)} failed"
        )
        if raise_transient_errors and transient_errors:
            raise transient_errors[0]
        return queued_count, failed_devices

    finally:
        # Clean up NATS connection if we created it
        if nats_should_close and nats_conn and not nats_conn.is_closed:
            connection_manager.clear_connection()
            await nats_conn.close()
