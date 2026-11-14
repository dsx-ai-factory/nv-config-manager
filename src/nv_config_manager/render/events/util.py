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
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import nats.js

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
    nats_render_consumer_name,
)
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMClient, dcim_client_session

if TYPE_CHECKING:
    from nv_config_manager.common.client.redis import RedisClient


class DeviceNotEnabledError(Exception):
    """To be raised if a device is not enabled for NVIDIA Config Manager."""


# How long a device stays deduped while its render is queued but not yet
# processed. This has to cover worst-case queue latency: a change to one shared
# Nautobot object fans out to every attached device, so if the flag expires
# before the render runs, the next change re-publishes the same device and the
# queue amplifies by one copy per change.
QUEUED_FLAG_TTL_SECONDS = 3600

# Message field carrying the token of the queue claim that published it.
QUEUE_CLAIM_FIELD = "queue_claim"

# A claim that has not recorded its published message by now belongs to a
# producer that died between claiming and publishing.
CLAIM_PUBLISH_GRACE_SECONDS = 60

# How long one read of the render consumer's progress is reused when checking
# claims, so a burst of duplicate requests costs one lookup rather than one each.
_CONSUMER_PROGRESS_CACHE_SECONDS = 5

# Take the slot if it is free, otherwise return whoever holds it. A command
# retried after a lost reply then finds its own value and still knows it won.
_CLAIM_SCRIPT = """
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return ARGV[1]
end
return redis.call('GET', KEYS[1])
"""

# Swap the claim only while it still holds the expected value. Finding the new
# value already in place counts as success, for the same retry reason.
_REPLACE_CLAIM_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
if current == ARGV[2] then
    return 1
end
return 0
"""

_CLEAR_OWN_CLAIM_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current and string.sub(current, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. ':' then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_consumer_progress_cache: dict[tuple[str, str], tuple[float, int]] = {}


@dataclass(frozen=True)
class QueueClaim:
    """A producer's hold on a device's render queue slot.

    The slot stores ``token:claimed_at`` until the message is published, then
    ``token:claimed_at:stream_seq``. The sequence lets a later producer tell a
    claim whose message is still waiting from one whose message is gone.
    """

    token: str
    claimed_at: int
    stream_seq: int | None = None

    def encode(self) -> bytes:
        """Return the value stored in the slot."""
        fields = [self.token, str(self.claimed_at)]
        if self.stream_seq is not None:
            fields.append(str(self.stream_seq))
        return ":".join(fields).encode()

    @classmethod
    def decode(cls, raw: bytes | str | None) -> QueueClaim | None:
        """Parse a slot value, or return None for a value this format did not write."""
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        fields = text.split(":")
        if len(fields) not in (2, 3):
            return None
        try:
            return cls(
                token=fields[0],
                claimed_at=int(fields[1]),
                stream_seq=int(fields[2]) if len(fields) == 3 else None,
            )
        except ValueError:
            return None


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


def _queue_key(device_uuid: str) -> str:
    return f"{device_uuid}_queued"


async def _eval(client: RedisClient, script: str, key: str, *args: bytes | str | int) -> object:
    result = client.redis.eval(script, 1, key, *args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _render_consumer_ack_floor(jetstream: nats.js.JetStreamContext) -> int | None:
    """Return the stream sequence up to which the render consumer is done with messages.

    Uses consumer INFO, which the render consumers already require, rather than
    stream INFO, which externally managed streams need not grant. Returns None
    when it cannot be read, so callers keep the claim they found.
    """
    stream, _ = nats_render_change_config()
    durable = nats_render_consumer_name()
    cached = _consumer_progress_cache.get((stream, durable))
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        info = await jetstream.consumer_info(stream, durable)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        get_logger(__name__, category=LogCategory.RENDER_EVENT).debug(
            "Could not read render consumer progress to check a queue claim: %s", exc
        )
        return None
    ack_floor = info.ack_floor.stream_seq if info.ack_floor else 0
    _consumer_progress_cache[(stream, durable)] = (
        now + _CONSUMER_PROGRESS_CACHE_SECONDS,
        ack_floor,
    )
    return ack_floor


async def _claim_is_abandoned(holder: QueueClaim, jetstream: nats.js.JetStreamContext) -> bool:
    """Return whether no message will ever release this claim.

    That happens when the producer died before publishing, or when the claim's
    message is behind the consumer's ack floor without having released it: it
    was settled, stopped redelivering, or left the stream, for example through
    a stream limit.
    """
    if holder.stream_seq is None:
        return time.time() - holder.claimed_at > CLAIM_PUBLISH_GRACE_SECONDS
    ack_floor = await _render_consumer_ack_floor(jetstream)
    return ack_floor is not None and holder.stream_seq <= ack_floor


async def claim_queued(
    device_uuid: str,
    client: RedisClient | None = None,
    jetstream: nats.js.JetStreamContext | None = None,
) -> QueueClaim | None:
    """Atomically claim the render queue slot for a device.

    Returns the claim when the caller won it and should publish, or None when a
    render is already queued. Separate ``exists`` and ``setex`` calls let
    concurrent producers both observe an unset flag and both publish, so the
    check and the set have to happen in one step.

    The claim's token travels with the published message so that only that
    message can release it; a late or redelivered message cannot release a newer
    claim. With a JetStream context, a claim that no message will release is
    taken over instead of suppressing the device until it expires.
    """
    claim = QueueClaim(token=uuid.uuid4().hex, claimed_at=int(time.time()))
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, never dedup
        return claim

    try:
        queue_key = _queue_key(device_uuid)
        current = await _eval(
            client, _CLAIM_SCRIPT, queue_key, claim.encode(), QUEUED_FLAG_TTL_SECONDS
        )
        holder = QueueClaim.decode(current if isinstance(current, (bytes, str)) else None)
        if holder == claim:
            return claim
        if holder is None or jetstream is None:
            return None
        if not await _claim_is_abandoned(holder, jetstream):
            return None
        replaced = await _eval(
            client,
            _REPLACE_CLAIM_SCRIPT,
            queue_key,
            holder.encode(),
            claim.encode(),
            QUEUED_FLAG_TTL_SECONDS,
        )
        if replaced != 1:
            return None
        get_logger(__name__, category=LogCategory.RENDER_EVENT).warning(
            "Took over the render queue claim for %s; its message will never release it",
            device_uuid,
        )
        return claim
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def record_published(
    device_uuid: str,
    claim: QueueClaim,
    stream_seq: int,
    client: RedisClient | None = None,
) -> None:
    """Store the stream sequence of the message a claim published."""
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        return

    try:
        await _eval(
            client,
            _REPLACE_CLAIM_SCRIPT,
            _queue_key(device_uuid),
            claim.encode(),
            replace(claim, stream_seq=stream_seq).encode(),
            QUEUED_FLAG_TTL_SECONDS,
        )
    finally:
        if owned_client:
            await _close_queue_redis_client(client)


async def clear_queued(
    device_uuid: str,
    client: RedisClient | None = None,
    token: str | None = None,
) -> None:
    """Release the queued flag for a device while it still holds this claim.

    Without a token there is nothing to release: messages published before
    claims carried a token came from producers whose flags expire within a minute.
    """
    if token is None:
        return
    owned_client = client is None
    client = client or _get_queue_redis_client()
    if client is None:
        # Local environment, skip Redis operations
        return

    try:
        await _eval(client, _CLEAR_OWN_CLAIM_SCRIPT, _queue_key(device_uuid), token.encode())
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
    claim: QueueClaim | None = None
    try:
        # Look the device up before claiming. A claim held across the lookup makes
        # a newer change skip itself as a duplicate, and if this lookup then reads
        # the device as not yet enabled, neither request publishes.
        if not await should_run(device_uuid, dcim_client):
            return {
                "device_uuid": device_uuid,
                "error": f"{device_uuid} is not enabled for configuration renders.",
            }

        claim = await claim_queued(device_uuid, queue_client, jetstream)
        if claim is None:
            logger.info(
                "Device %s already has a pending render, skipping duplicate queue request",
                device_uuid,
            )
            return None

        # Publish message using provided jetstream connection
        message = {
            "device_id": device_uuid,
            "commit_message": commit_message,
            "user": user,
            "@timestamp": timestamp,
            QUEUE_CLAIM_FIELD: claim.token,
        }

        stream, subject = nats_render_change_config()
        ack = await jetstream.publish(
            subject=subject,
            payload=json.dumps(message).encode("utf-8"),
            stream=stream,
        )

    except Exception as exc:
        # Release the claim if we won it but publishing failed
        if claim is not None:
            await clear_queued(device_uuid, queue_client, token=claim.token)
        error_msg = str(exc)
        logger.warning(f"Failed to queue device {device_uuid}: {error_msg}")
        return {"device_uuid": device_uuid, "error": error_msg}

    try:
        await record_published(device_uuid, claim, ack.seq, queue_client)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Published a render for %s but could not record it on the queue claim; "
            "the claim will be treated as abandoned after %ss: %s",
            device_uuid,
            CLAIM_PUBLISH_GRACE_SECONDS,
            exc,
        )
    logger.debug(f"Successfully queued device {device_uuid}")
    return None


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
