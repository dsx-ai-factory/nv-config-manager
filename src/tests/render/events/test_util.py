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
"""Tests for provider-backed render event utility functions."""

import json
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager.dcim import RenderDeviceStatus
from nv_config_manager.render.events import util
from nv_config_manager.render.events.util import (
    CLAIM_PUBLISH_GRACE_SECONDS,
    QUEUE_CLAIM_FIELD,
    DeviceNotEnabledError,
    QueueClaim,
    claim_queued,
    clear_queued,
    queue_render,
    should_run,
)

ENABLED = RenderDeviceStatus(render_enabled=True, is_aggregate_managed=False)
KEY = "device_queued"


class FakeClaimRedis:
    """In-memory stand-in for the queue Redis, following the claim scripts' semantics."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.redis = self
        self.eval = AsyncMock(side_effect=self._eval)

    async def _eval(self, script: str, numkeys: int, key: str, *args: bytes | int) -> object:
        assert numkeys == 1
        current = self.store.get(key)
        if script == util._CLAIM_SCRIPT:
            value = args[0]
            assert isinstance(value, bytes)
            if current is None:
                self.store[key] = value
                return value
            return current
        if script == util._REPLACE_CLAIM_SCRIPT:
            expected, new = args[0], args[1]
            assert isinstance(new, bytes)
            if current == expected:
                self.store[key] = new
                return 1
            return 1 if current == new else 0
        if script == util._CLEAR_OWN_CLAIM_SCRIPT:
            token = args[0]
            assert isinstance(token, bytes)
            if current is not None and current.startswith(token + b":"):
                del self.store[key]
                return 1
            return 0
        raise AssertionError("unexpected script")


@pytest.fixture
def fake_redis() -> Iterator[FakeClaimRedis]:
    redis = FakeClaimRedis()
    with patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=redis):
        yield redis


@pytest.fixture(autouse=True)
def _fresh_consumer_progress() -> Iterator[None]:
    util._consumer_progress_cache.clear()
    yield
    util._consumer_progress_cache.clear()


@pytest.fixture
def nats_ini(custom_ini: Callable[[str], None]) -> None:
    custom_ini("""
[aggregate]
is_aggregate_environment=false

[nats]
""")


def _jetstream(ack_floor: int = 0) -> MagicMock:
    """Return a JetStream context reporting the render consumer's ack floor."""
    jetstream = MagicMock()
    jetstream.consumer_info = AsyncMock(
        return_value=MagicMock(ack_floor=MagicMock(stream_seq=ack_floor))
    )
    return jetstream


@pytest.fixture
def mock_dcim_client():
    """Provide the selected DCIM client through its public lifecycle seam."""
    client = AsyncMock()
    client.get_render_device_status.return_value = None

    @asynccontextmanager
    async def session():
        yield client

    with patch("nv_config_manager.render.events.util.dcim_client_session", session):
        yield client


@pytest.fixture
def mock_nats_connection():
    """Mock a newly-created NATS connection and JetStream publisher."""
    with patch("nv_config_manager.render.events.util.nats_connection") as mock:
        connection = AsyncMock()
        jetstream = _jetstream()
        jetstream.publish = AsyncMock(return_value=MagicMock(seq=42))
        connection.jetstream = MagicMock(return_value=jetstream)
        connection.is_closed = False
        mock.return_value = connection
        yield connection


@pytest.mark.asyncio
async def test_should_run_checks_provider_render_status(mock_dcim_client, custom_ini):
    """Render eligibility is provider-neutral and respects environment scope."""
    custom_ini("""
[aggregate]
is_aggregate_environment=false
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )

    assert await should_run("test-device", mock_dcim_client) is True
    mock_dcim_client.get_render_device_status.assert_awaited_once_with("test-device")


@pytest.mark.asyncio
async def test_should_run_rejects_disabled_or_missing_device(mock_dcim_client):
    """Missing and disabled managed-device states do not queue a render."""
    assert await should_run("test-device", mock_dcim_client) is False
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=False, is_aggregate_managed=False
    )
    assert await should_run("test-device", mock_dcim_client) is False


@pytest.mark.asyncio
async def test_should_run_rejects_aggregate_scope_mismatch(mock_dcim_client, custom_ini):
    """Aggregate scoping remains a consumer policy over normalized status."""
    custom_ini("""
[aggregate]
is_aggregate_environment=true
""")
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=True, is_aggregate_managed=False
    )

    assert await should_run("test-device", mock_dcim_client) is False


@pytest.mark.asyncio
async def test_queue_render_publishes_under_a_claim_that_records_the_message(
    mock_dcim_client: AsyncMock,
    mock_nats_connection: AsyncMock,
    fake_redis: FakeClaimRedis,
    nats_ini: None,
) -> None:
    """The message carries its claim's token, and the claim records the message's sequence."""
    mock_dcim_client.get_render_device_status.return_value = ENABLED

    await queue_render("device", "message", "user", "2024-01-16T21:46:05Z")

    publish = mock_nats_connection.jetstream.return_value.publish
    publish.assert_awaited_once()
    payload = json.loads(publish.call_args.kwargs["payload"])
    claim = QueueClaim.decode(fake_redis.store[KEY])
    assert claim is not None
    assert payload[QUEUE_CLAIM_FIELD] == claim.token
    assert claim.stream_seq == 42
    mock_dcim_client.get_render_device_status.assert_awaited_once_with("device")


@pytest.mark.asyncio
async def test_disabled_device_is_rejected_without_touching_the_slot(
    mock_dcim_client: AsyncMock,
    mock_nats_connection: AsyncMock,
    fake_redis: FakeClaimRedis,
    nats_ini: None,
) -> None:
    """The lookup runs before the claim, so a stale disabled read cannot hide a newer change."""
    mock_dcim_client.get_render_device_status.return_value = RenderDeviceStatus(
        render_enabled=False, is_aggregate_managed=False
    )

    with pytest.raises(DeviceNotEnabledError, match="not enabled for configuration renders"):
        await queue_render("device", "message", "user", "2024-01-16T21:46:05Z")

    fake_redis.eval.assert_not_awaited()
    mock_nats_connection.jetstream.return_value.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_render_releases_its_claim_when_publish_fails(
    mock_dcim_client: AsyncMock,
    mock_nats_connection: AsyncMock,
    fake_redis: FakeClaimRedis,
    nats_ini: None,
) -> None:
    """A failed publish frees the slot so the next change can queue the device."""
    mock_dcim_client.get_render_device_status.return_value = ENABLED
    mock_nats_connection.jetstream.return_value.publish.side_effect = RuntimeError("NATS error")

    with pytest.raises(Exception, match="NATS error"):
        await queue_render("device", "message", "user", "2024-01-16T21:46:05Z")

    assert KEY not in fake_redis.store


@pytest.mark.asyncio
async def test_published_render_succeeds_even_if_its_sequence_cannot_be_recorded(
    mock_dcim_client: AsyncMock,
    mock_nats_connection: AsyncMock,
    fake_redis: FakeClaimRedis,
    nats_ini: None,
) -> None:
    """The render is queued either way; the unrecorded claim is reclaimed after the grace period."""
    mock_dcim_client.get_render_device_status.return_value = ENABLED

    async def lose_the_record(script: str, *args: object) -> object:
        if script == util._REPLACE_CLAIM_SCRIPT:
            raise ConnectionError("redis down")
        return await FakeClaimRedis._eval(fake_redis, script, *args)  # type: ignore[arg-type]

    fake_redis.eval.side_effect = lose_the_record

    await queue_render("device", "message", "user", "2024-01-16T21:46:05Z")

    mock_nats_connection.jetstream.return_value.publish.assert_awaited_once()
    claim = QueueClaim.decode(fake_redis.store[KEY])
    assert claim is not None and claim.stream_seq is None


@pytest.mark.asyncio
async def test_only_one_producer_wins_a_pending_slot(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """While a claim's message is still waiting, later requests are duplicates."""
    jetstream = _jetstream(ack_floor=10)
    fake_redis.store[KEY] = QueueClaim("aaa", 1, stream_seq=11).encode()

    assert await claim_queued("device", jetstream=jetstream) is None
    assert QueueClaim.decode(fake_redis.store[KEY]) == QueueClaim("aaa", 1, stream_seq=11)


@pytest.mark.asyncio
async def test_each_claim_gets_its_own_token(fake_redis: FakeClaimRedis) -> None:
    """Two claims of one device, such as before and after a release, are distinguishable."""
    first = await claim_queued("device")
    fake_redis.store.clear()
    second = await claim_queued("device")

    assert first is not None and second is not None
    assert first.token != second.token


@pytest.mark.asyncio
async def test_a_retried_claim_still_knows_it_won(fake_redis: FakeClaimRedis) -> None:
    """If Redis stored the claim but the reply was lost, the retry finds its own value."""
    with (
        patch("nv_config_manager.render.events.util.uuid.uuid4", return_value=MagicMock(hex="aaa")),
        patch("nv_config_manager.render.events.util.time.time", return_value=1000),
    ):
        fake_redis.store[KEY] = QueueClaim("aaa", 1000).encode()

        claim = await claim_queued("device")

    assert claim == QueueClaim("aaa", 1000)


@pytest.mark.asyncio
async def test_a_claim_that_never_published_is_taken_over_after_the_grace_period(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """A producer that died between claiming and publishing does not block the device."""
    with patch("nv_config_manager.render.events.util.time.time", return_value=10_000):
        fake_redis.store[KEY] = QueueClaim("aaa", 10_000 - CLAIM_PUBLISH_GRACE_SECONDS - 1).encode()

        claim = await claim_queued("device", jetstream=_jetstream())

    assert claim is not None and claim.token != "aaa"
    assert QueueClaim.decode(fake_redis.store[KEY]) == claim


@pytest.mark.asyncio
async def test_a_claim_still_publishing_is_respected(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """Within the grace period an unrecorded claim is presumed to be publishing."""
    with patch("nv_config_manager.render.events.util.time.time", return_value=10_000):
        fake_redis.store[KEY] = QueueClaim("aaa", 10_000 - 5).encode()

        assert await claim_queued("device", jetstream=_jetstream()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_floor", [11, 500])
async def test_a_claim_whose_message_is_behind_the_ack_floor_is_taken_over(
    fake_redis: FakeClaimRedis, nats_ini: None, ack_floor: int
) -> None:
    """A claim no message will release would otherwise suppress the device for its TTL."""
    fake_redis.store[KEY] = QueueClaim("aaa", 1, stream_seq=11).encode()

    claim = await claim_queued("device", jetstream=_jetstream(ack_floor))

    assert claim is not None and claim.token != "aaa"
    assert QueueClaim.decode(fake_redis.store[KEY]) == claim


@pytest.mark.asyncio
async def test_a_claim_is_kept_when_consumer_progress_cannot_be_read(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """Without evidence the message is gone, the claim stands and the request is a duplicate."""
    jetstream = _jetstream()
    jetstream.consumer_info.side_effect = PermissionError("denied")
    fake_redis.store[KEY] = QueueClaim("aaa", 1, stream_seq=11).encode()

    assert await claim_queued("device", jetstream=jetstream) is None


@pytest.mark.asyncio
async def test_a_slot_written_before_claims_carried_tokens_is_left_to_expire(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """Older producers' flags hold no token or sequence; they expire on their own."""
    fake_redis.store[KEY] = b"1"

    assert await claim_queued("device", jetstream=_jetstream(ack_floor=1_000)) is None
    assert fake_redis.store[KEY] == b"1"


@pytest.mark.asyncio
async def test_consumer_progress_is_read_once_for_a_burst_of_duplicates(
    fake_redis: FakeClaimRedis, nats_ini: None
) -> None:
    """A fan-out of duplicate requests costs one progress lookup, not one per device."""
    jetstream = _jetstream(ack_floor=0)
    for n in range(5):
        fake_redis.store[f"device-{n}_queued"] = QueueClaim("aaa", 1, stream_seq=11).encode()

    for n in range(5):
        assert await claim_queued(f"device-{n}", jetstream=jetstream) is None

    jetstream.consumer_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_with_a_token_only_deletes_that_claim(fake_redis: FakeClaimRedis) -> None:
    """A late message must not release a newer claim on the same device."""
    fake_redis.store[KEY] = QueueClaim("bbb", 1, stream_seq=12).encode()

    await clear_queued("device", token="aaa")
    assert KEY in fake_redis.store

    await clear_queued("device", token="bbb")
    assert KEY not in fake_redis.store


@pytest.mark.asyncio
async def test_a_message_without_a_token_releases_nothing(fake_redis: FakeClaimRedis) -> None:
    """Messages from older producers cannot delete the claims newer producers create."""
    fake_redis.store[KEY] = QueueClaim("bbb", 1, stream_seq=12).encode()

    await clear_queued("device")

    assert KEY in fake_redis.store
    fake_redis.eval.assert_not_awaited()
