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
"""Tests for Nautobot cable-status persistence."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from nv_config_manager_dcim import CableStatus, CableStatusUpdate, DCIMCableStatusClient
from nv_config_manager_dcim.errors import DCIMConflictError, DCIMInvalidDataError, DCIMNotFoundError
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient


def _client() -> NautobotDCIMClient:
    return NautobotDCIMClient("https://nautobot.example", "token")


def _update(status: CableStatus = CableStatus.INVALID) -> CableStatusUpdate:
    return CableStatusUpdate(
        device_id="device-1",
        interface_name="Ethernet1/1",
        status=status,
        workflow_id="cable-validation-1",
    )


@pytest.mark.asyncio
async def test_update_cable_status_patches_attached_cable() -> None:
    client = _client()
    client.get_all = AsyncMock(return_value=[{"name": "Ethernet1/1", "cable": {"id": "cable-1"}}])
    client.get = AsyncMock(
        side_effect=[
            {"id": "cable-1", "status": {"name": "Connected"}},
            {"results": []},
        ]
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()

    await client.update_cable_status(_update())

    client.get_all.assert_awaited_once_with(
        "dcim/interfaces/",
        params={"device_id": ["device-1"]},
    )
    client.patch.assert_awaited_once_with(
        "dcim/cables/cable-1/",
        {"status": "Invalid"},
    )
    client.post.assert_awaited_once_with(
        "extras/notes/",
        {
            "assigned_object_type": "dcim.cable",
            "assigned_object_id": "cable-1",
            "note": "Cable validation workflow cable-validation-1 set status to Invalid.",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(CableStatus))
async def test_unchanged_cable_status_skips_notes(status: CableStatus) -> None:
    client = _client()
    client.get_all = AsyncMock(return_value=[{"name": "Ethernet1/1", "cable": {"id": "cable-1"}}])
    client.get = AsyncMock(
        side_effect=[
            {"id": "cable-1", "status": {"name": status.value}},
        ]
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()

    await client.update_cable_status(_update(status))

    client.patch.assert_not_awaited()
    client.get.assert_any_await("dcim/cables/cable-1/", params={"depth": 1})
    assert client.get.await_count == 1
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_cable_status_rejects_interface_without_cable() -> None:
    client = _client()
    client.get_all = AsyncMock(return_value=[{"name": "Ethernet1/1", "cable": None}])

    with pytest.raises(DCIMNotFoundError, match="has no cable"):
        await client.update_cable_status(_update())


def test_nautobot_exposes_cable_status_capability() -> None:
    assert isinstance(_client(), DCIMCableStatusClient)


@pytest.mark.asyncio
async def test_concurrent_updates_share_interface_lookup_and_close_clears_it() -> None:
    client = _client()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def lookup(*args: object, **kwargs: object) -> list[dict[str, object]]:
        entered.set()
        await release.wait()
        return [
            {"name": f"Ethernet1/{index}", "cable": {"id": f"cable-{index}"}}
            for index in range(1, 21)
        ]

    client.get_all = AsyncMock(side_effect=lookup)
    client.get = AsyncMock(return_value={"status": {"name": "Connected"}})
    async with client:
        tasks = [
            asyncio.create_task(
                client.update_cable_status(
                    CableStatusUpdate(
                        device_id="device-1",
                        interface_name=f"Ethernet1/{index}",
                        status=CableStatus.CONNECTED,
                        workflow_id="validation-1",
                    )
                )
            )
            for index in range(1, 21)
        ]
        await entered.wait()
        release.set()
        await asyncio.gather(*tasks)
    client.get_all.assert_awaited_once()

    async with client:
        await client.update_cable_status(_update(CableStatus.CONNECTED))
    assert client.get_all.await_count == 2


@pytest.mark.asyncio
async def test_failed_lookup_is_shared_until_the_next_client_context() -> None:
    client = _client()
    client.get_all = AsyncMock(
        side_effect=[TimeoutError(), [{"name": "Ethernet1/1", "cable": {"id": "cable-1"}}]]
    )
    client.get = AsyncMock(return_value={"status": {"name": "Connected"}})
    async with client:
        errors = await asyncio.gather(
            *(client.update_cable_status(_update(CableStatus.CONNECTED)) for _ in range(20)),
            return_exceptions=True,
        )
        assert all(isinstance(error, TimeoutError) for error in errors)
        assert client.get_all.await_count == 1
    async with client:
        await client.update_cable_status(_update(CableStatus.CONNECTED))
    assert client.get_all.await_count == 2


@pytest.mark.asyncio
async def test_canceling_one_update_does_not_cancel_the_shared_lookup() -> None:
    client = _client()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def lookup(*args: object, **kwargs: object) -> list[dict[str, object]]:
        entered.set()
        await release.wait()
        return [{"name": "Ethernet1/1", "cable": {"id": "cable-1"}}]

    client.get_all = AsyncMock(side_effect=lookup)
    client.get = AsyncMock(return_value={"status": {"name": "Connected"}})
    async with client:
        first = asyncio.create_task(client.update_cable_status(_update(CableStatus.CONNECTED)))
        second = asyncio.create_task(client.update_cable_status(_update(CableStatus.CONNECTED)))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        await second
    client.get_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_cancels_an_incomplete_interface_lookup() -> None:
    client = _client()
    entered = asyncio.Event()
    canceled = asyncio.Event()

    async def lookup(*args: object, **kwargs: object) -> list[dict[str, object]]:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
        return []

    client.get_all = AsyncMock(side_effect=lookup)
    update = asyncio.create_task(client.update_cable_status(_update()))
    await entered.wait()
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await update
    assert canceled.is_set()


@pytest.mark.asyncio
async def test_cable_interface_lookup_reads_all_pages_once() -> None:
    client = _client()
    offsets: list[object] = []

    async def get(
        path: str, params: dict[str, object], timeout: int | None = None
    ) -> dict[str, object]:
        if path == "dcim/interfaces/":
            offsets.append(params["offset"])
            if params["offset"] == 0:
                return {
                    "results": [{"name": "Ethernet1/1", "cable": {"id": "cable-1"}}],
                    "next": "page-2",
                }
            return {"results": [{"name": "Ethernet1/2", "cable": {"id": "cable-2"}}], "next": None}
        return {"status": {"name": "Connected"}}

    client.get = AsyncMock(side_effect=get)
    await client.update_cable_status(
        CableStatusUpdate(
            device_id="device-1",
            interface_name="Ethernet1/2",
            status=CableStatus.CONNECTED,
            workflow_id="validation-1",
        )
    )
    await client.update_cable_status(_update(CableStatus.CONNECTED))
    lookups = [call for call in client.get.await_args_list if call.args[0] == "dcim/interfaces/"]
    assert len(lookups) == 2
    assert offsets == [0, 1]
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interfaces", "error", "message"),
    [
        ([], DCIMNotFoundError, "No interface named"),
        ([{"name": "Ethernet1/1"}] * 2, DCIMConflictError, "Multiple interfaces named"),
        ([{"id": "no-name"}], DCIMInvalidDataError, "invalid interface lookup"),
    ],
)
async def test_interface_lookup_preserves_validation_errors(
    interfaces: list[dict[str, object]], error: type[Exception], message: str
) -> None:
    client = _client()
    client.get_all = AsyncMock(return_value=interfaces)
    with pytest.raises(error, match=message):
        await client.update_cable_status(_update())


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [None, [], {}, {"results": None}, {"results": {}}])
@pytest.mark.parametrize("later_page", [False, True])
async def test_cable_lookup_rejects_malformed_pagination_pages(
    page: object, later_page: bool
) -> None:
    client = _client()
    pages = [{"results": [{"name": "unrelated"}], "next": "page-2"}] if later_page else []
    client.get = AsyncMock(side_effect=[*pages, page])
    async with client:
        with pytest.raises(DCIMInvalidDataError, match="invalid pagination data"):
            await client.update_cable_status(_update())


@pytest.mark.asyncio
async def test_cable_lookup_preserves_not_found_for_valid_empty_page() -> None:
    client = _client()
    client.get = AsyncMock(return_value={"results": [], "next": None})
    async with client:
        with pytest.raises(DCIMNotFoundError, match="No interface named"):
            await client.update_cable_status(_update())
