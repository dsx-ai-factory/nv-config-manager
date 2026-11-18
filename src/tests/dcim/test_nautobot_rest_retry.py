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
"""REST reads recover transient failures without repeating mutations."""

import asyncio

import pytest
from aiohttp import ClientConnectionError, ClientResponse
from aioresponses import aioresponses
from nv_config_manager_dcim_nautobot_2x.client import NautobotClient, NautobotException

URL = "https://nautobot.example/api/dcim/cables/cable-1/"


@pytest.fixture
def retry_delays(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("aiohttp_retry.client.asyncio.sleep", sleep)
    return delays


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), ClientConnectionError("reset")])
async def test_get_retries_transport_failure(error: Exception, retry_delays: list[float]) -> None:
    with aioresponses() as mocked:
        mocked.get(URL, exception=error)
        mocked.get(URL, payload={"id": "cable-1"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            assert await client.get("dcim/cables/cable-1/") == {"id": "cable-1"}
    assert len(retry_delays) == 1
    assert 2 <= retry_delays[0] <= 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_get_releases_gateway_response_before_retry(
    status: int, retry_delays: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    released: list[int] = []
    original_release = ClientResponse.release

    def release(response: ClientResponse) -> None:
        released.append(response.status)
        original_release(response)

    monkeypatch.setattr(ClientResponse, "release", release)
    with aioresponses() as mocked:
        mocked.get(URL, status=status)
        mocked.get(URL, payload={"id": "cable-1"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            assert await client.get("dcim/cables/cable-1/") == {"id": "cable-1"}
    assert released[0] == status
    assert len(retry_delays) == 1


@pytest.mark.asyncio
async def test_get_stops_after_one_retry_and_preserves_error_body(
    retry_delays: list[float],
) -> None:
    with aioresponses() as mocked:
        mocked.get(URL, status=503)
        mocked.get(URL, status=503, payload={"detail": "still unavailable"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            with pytest.raises(NautobotException, match="still unavailable") as error:
                await client.get("dcim/cables/cable-1/")
    assert error.value.status_code == 503
    assert len(retry_delays) == 1


@pytest.mark.asyncio
async def test_get_retries_a_body_read_timeout(
    retry_delays: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read = ClientResponse.read
    reads = 0

    async def read(response: ClientResponse) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 1:
            raise TimeoutError()
        return await original_read(response)

    monkeypatch.setattr(ClientResponse, "read", read)
    with aioresponses() as mocked:
        mocked.get(URL, payload={"id": "incomplete-response"})
        mocked.get(URL, payload={"id": "cable-1"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            assert await client.get("dcim/cables/cable-1/") == {"id": "cable-1"}
    assert len(retry_delays) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
async def test_get_does_not_retry_permanent_http_errors(
    status: int, retry_delays: list[float]
) -> None:
    with aioresponses() as mocked:
        mocked.get(URL, status=status, payload={"detail": "request rejected"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            with pytest.raises(NautobotException) as error:
                await client.get("dcim/cables/cable-1/")
    assert error.value.status_code == status
    assert retry_delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["patch", "post"])
async def test_mutations_are_not_automatically_retried(
    method: str, retry_delays: list[float]
) -> None:
    with aioresponses() as mocked:
        getattr(mocked, method)(URL, status=503, payload={"detail": "unavailable"})
        async with NautobotClient("https://nautobot.example", "token") as client:
            with pytest.raises(NautobotException):
                await getattr(client, method)("dcim/cables/cable-1/", {"status": "Connected"})
    assert retry_delays == []


@pytest.mark.asyncio
async def test_get_cancellation_is_not_retried(retry_delays: list[float]) -> None:
    with aioresponses() as mocked:
        mocked.get(URL, exception=asyncio.CancelledError())
        async with NautobotClient("https://nautobot.example", "token") as client:
            with pytest.raises(asyncio.CancelledError):
                await client.get("dcim/cables/cable-1/")
    assert retry_delays == []
