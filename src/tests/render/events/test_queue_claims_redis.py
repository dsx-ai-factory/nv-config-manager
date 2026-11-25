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
"""Run the queued-claim scripts and the published-device record against a real Redis.

The unit tests in ``test_util.py`` mock Redis, so they cannot show that the Lua
scripts leave another producer's claim alone.

Opt-in, like ``src/tests/common/test_lock_redis.py``: runs against ``REDIS_HOST``
or a throwaway testcontainers Redis when ``QUEUE_REDIS_TEST`` is set, and skips
otherwise.
"""

import contextlib
import os
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
from nv_config_manager_infrastructure.redis import RedisClient
from testcontainers.redis import RedisContainer

from nv_config_manager.render.events.util import (
    PUBLISHED_DEVICES_TTL_SECONDS,
    QUEUED_FLAG_TTL_SECONDS,
    _load_published,
    _record_published,
    claim_queued,
    clear_queued,
)

pytestmark = pytest.mark.timeout(120)


@pytest.fixture
async def queue_redis() -> AsyncIterator[RedisClient]:
    """Yield a client on an empty database of a real Redis, or skip when none is available."""
    host = os.environ.get("REDIS_HOST")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    container = None

    if not host:
        if not os.environ.get("QUEUE_REDIS_TEST"):
            pytest.skip("Set REDIS_HOST or QUEUE_REDIS_TEST=1 to run the Redis-backed claim tests")
        try:
            container = RedisContainer("redis:7-alpine")
            container.start()
            host = container.get_container_host_ip()
            port = int(container.get_exposed_port(6379))
        except Exception as exc:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.stop()
            pytest.skip(f"No Redis available for claim tests: {exc}")

    client = RedisClient(host=host, port=port)
    try:
        await client.ping()
        await client.redis.flushdb()
        yield client
    finally:
        await client.redis.aclose()
        if container is not None:
            container.stop()


@pytest.mark.asyncio
async def test_retake_renews_a_claim_this_request_left_behind(queue_redis: RedisClient) -> None:
    """A retry takes over its own leftover flag and gives it a full lifetime again."""
    await queue_redis.redis.set("device_queued", "event", ex=5)

    assert await claim_queued("device", queue_redis, token="event", retake=True) is True

    assert await queue_redis.redis.get("device_queued") == b"event"
    assert await queue_redis.redis.ttl("device_queued") > 5


@pytest.mark.asyncio
async def test_retake_claims_a_flag_that_expired_since_the_check(queue_redis: RedisClient) -> None:
    """A leftover flag that is gone by the takeover is claimed as a new one."""
    assert await claim_queued("device", queue_redis, token="event", retake=True) is True

    assert await queue_redis.redis.get("device_queued") == b"event"
    assert 0 < await queue_redis.redis.ttl("device_queued") <= QUEUED_FLAG_TTL_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["another-event", "1"])
async def test_retake_leaves_another_producers_claim(queue_redis: RedisClient, holder: str) -> None:
    """A claim another producer made since the check is not overwritten."""
    await queue_redis.redis.set("device_queued", holder, ex=30)

    assert await claim_queued("device", queue_redis, token="event", retake=True) is False

    assert await queue_redis.redis.get("device_queued") == holder.encode()
    assert await queue_redis.redis.ttl("device_queued") <= 30


@pytest.mark.asyncio
async def test_release_clears_only_this_requests_claim(queue_redis: RedisClient) -> None:
    """A failed publish clears its own flag and leaves a newer producer's flag alone."""
    await queue_redis.redis.set("mine_queued", "event", ex=30)
    await queue_redis.redis.set("theirs_queued", "another-event", ex=30)

    await clear_queued("mine", queue_redis, token="event")
    await clear_queued("theirs", queue_redis, token="event")

    assert await queue_redis.redis.get("mine_queued") is None
    assert await queue_redis.redis.get("theirs_queued") == b"another-event"


@pytest.mark.asyncio
async def test_clear_without_a_token_removes_any_claim(queue_redis: RedisClient) -> None:
    """The device consumer clears the flag whoever set it, once it holds the device lock."""
    await queue_redis.redis.set("device_queued", "event", ex=30)

    await clear_queued("device", queue_redis)

    assert await queue_redis.redis.get("device_queued") is None


@pytest.mark.asyncio
async def test_published_record_round_trips_and_expires(queue_redis: RedisClient) -> None:
    """Recorded devices are read back, and a renewal without new devices keeps them."""
    logger = MagicMock()
    await _record_published("event", ["device-a", "device-b"], queue_redis, logger)
    await queue_redis.redis.expire("render_event_published:event", 5)

    await _record_published("event", [], queue_redis, logger)

    assert await _load_published("event", queue_redis, logger) == {"device-a", "device-b"}
    ttl = await queue_redis.redis.ttl("render_event_published:event")
    assert 5 < ttl <= PUBLISHED_DEVICES_TTL_SECONDS
    assert await _load_published("another-event", queue_redis, logger) == set()
    logger.warning.assert_not_called()
