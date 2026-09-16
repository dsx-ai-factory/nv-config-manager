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
"""Tests for Nautobot GraphQL gateway retries."""

import pytest
from aiohttp import ClientResponseError
from aioresponses import aioresponses
from nv_config_manager_dcim_nautobot_2x.client import (
    _GRAPHQL_RETRY_OPTIONS,
    NautobotClient,
    NautobotException,
)

_GRAPHQL_URL = "https://nautobot.example/api/graphql/"


@pytest.fixture
def fast_graphql_retries(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Skip RetryClient backoff so tests do not wait on real sleeps."""
    delays: list[float] = []

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("aiohttp_retry.client.asyncio.sleep", _sleep)
    return delays


def _retry_delays(failures: int) -> list[float]:
    """Return ExponentialRetry waits after ``failures`` retryable responses."""
    return [_GRAPHQL_RETRY_OPTIONS.get_timeout(attempt) for attempt in range(1, failures + 1)]


@pytest.mark.asyncio
async def test_graphql_query_retries_504_then_succeeds(fast_graphql_retries: list[float]) -> None:
    """A single gateway timeout is retried and does not fail the page."""
    with aioresponses() as mocked:
        mocked.post(_GRAPHQL_URL, status=504)
        mocked.post(_GRAPHQL_URL, payload={"data": {"ok": True}})
        async with NautobotClient("https://nautobot.example", token="token") as client:
            result = await client.graphql_query("query { ok }")

    assert result == {"data": {"ok": True}}
    assert fast_graphql_retries == _retry_delays(1)


@pytest.mark.asyncio
async def test_graphql_query_gives_up_after_retryable_504s(
    fast_graphql_retries: list[float],
) -> None:
    """Persistent 504s still fail after the retry budget."""
    with aioresponses() as mocked:
        for _ in range(_GRAPHQL_RETRY_OPTIONS.attempts):
            mocked.post(_GRAPHQL_URL, status=504)
        async with NautobotClient("https://nautobot.example", token="token") as client:
            with pytest.raises(ClientResponseError) as exc_info:
                await client.graphql_query("query { ok }")

    assert exc_info.value.status == 504
    assert fast_graphql_retries == _retry_delays(_GRAPHQL_RETRY_OPTIONS.attempts - 1)


@pytest.mark.asyncio
async def test_graphql_query_does_not_retry_400(fast_graphql_retries: list[float]) -> None:
    """GraphQL syntax/validation failures are not transient."""
    with aioresponses() as mocked:
        mocked.post(
            _GRAPHQL_URL,
            status=400,
            payload={"errors": ["GraphQL syntax error"]},
        )
        async with NautobotClient("https://nautobot.example", token="token") as client:
            with pytest.raises(NautobotException, match="GraphQL error"):
                await client.graphql_query("bad query")

    assert fast_graphql_retries == []


@pytest.mark.asyncio
async def test_graphql_query_retries_timeout_then_succeeds(
    fast_graphql_retries: list[float],
) -> None:
    """Client-side timeouts are retried the same way as gateway 504s."""
    with aioresponses() as mocked:
        mocked.post(_GRAPHQL_URL, exception=TimeoutError())
        mocked.post(_GRAPHQL_URL, payload={"data": {"ok": True}})
        async with NautobotClient("https://nautobot.example", token="token") as client:
            result = await client.graphql_query("query { ok }")

    assert result == {"data": {"ok": True}}
    assert fast_graphql_retries == _retry_delays(1)


@pytest.mark.asyncio
async def test_graphql_query_does_not_retry_http_500(fast_graphql_retries: list[float]) -> None:
    """Persistent Nautobot 500s are not treated as gateway blips."""
    with aioresponses() as mocked:
        mocked.post(_GRAPHQL_URL, status=500)
        async with NautobotClient("https://nautobot.example", token="token") as client:
            with pytest.raises(ClientResponseError) as exc_info:
                await client.graphql_query("query { ok }")

    assert exc_info.value.status == 500
    assert fast_graphql_retries == []
