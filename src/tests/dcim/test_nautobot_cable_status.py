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
from typing import Any
from unittest.mock import AsyncMock

import pytest
from nv_config_manager_dcim import (
    CableStatus,
    CableStatusUpdate,
    DCIMCableStatusBatchClient,
    DCIMCableStatusClient,
)
from nv_config_manager_dcim.errors import DCIMConflictError, DCIMInvalidDataError, DCIMNotFoundError
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient

from nv_config_manager_workflows.activities.cable_validation import (
    UpdateCableStatusesInput,
    update_cable_statuses,
)


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
        params={"device_id": ["device-1"], "name": ["Ethernet1/1"]},
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
    assert isinstance(_client(), DCIMCableStatusBatchClient)


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
        preparation = asyncio.create_task(
            client.prepare_cable_status_updates(
                "device-1", [f"Ethernet1/{index}" for index in range(1, 21)]
            )
        )
        await entered.wait()
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
        release.set()
        await asyncio.gather(preparation, *tasks)
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
    await client.prepare_cable_status_updates("device-1", ["Ethernet1/1", "Ethernet1/2"])
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
    assert all(
        lookup.kwargs["params"]["name"] == ["Ethernet1/1", "Ethernet1/2"] for lookup in lookups
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_activity", [False, True])
async def test_large_inventory_lookup_reads_only_requested_batch_names(
    monkeypatch: pytest.MonkeyPatch, legacy_activity: bool
) -> None:
    inventory = {
        f"Ethernet1/{index}": {"name": f"Ethernet1/{index}", "cable": {"id": f"cable-{index}"}}
        for index in range(2000)
    }
    requested = list(inventory)[:500]
    lookups: list[list[str]] = []
    clients: list[NautobotDCIMClient] = []

    async def get(
        path: str, params: dict[str, Any] | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        if path == "dcim/interfaces/":
            assert params is not None
            assert params["device_id"] == ["device-1"]
            names = params["name"]
            assert 0 < len(names) <= 25
            lookups.append(names)
            return {"results": [inventory[name] for name in names], "next": None}
        if path == "extras/notes/":
            return {"results": []}
        return {"status": {"name": "Connected"}}

    def create_client() -> NautobotDCIMClient:
        client = _client()
        client.get = AsyncMock(side_effect=get)
        client.patch = AsyncMock()
        client.post = AsyncMock()
        clients.append(client)
        return client

    monkeypatch.setattr(
        "nv_config_manager_workflows.activities.cable_validation.activities.get_dcim_client",
        create_client,
    )
    batch_size = 500 if legacy_activity else 25
    for start in range(0, len(requested), batch_size):
        await update_cable_statuses(
            UpdateCableStatusesInput(
                device_id="device-1",
                cable_statuses=dict.fromkeys(
                    requested[start : start + batch_size], CableStatus.INVALID
                ),
                workflow_id="validation-1",
            )
        )

    assert len(lookups) == 20
    assert sum(map(len, lookups)) == 500
    assert {name for names in lookups for name in names} == set(requested)
    assert sum(client.patch.await_count for client in clients) == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved_target", [False, True])
async def test_malformed_record_does_not_block_other_interface_writes(
    monkeypatch: pytest.MonkeyPatch, unresolved_target: bool
) -> None:
    client = _client()
    interfaces = [
        {"name": "Ethernet1/1", "cable": {"id": "cable-1"}},
        {"id": "malformed-interface"},
        {"name": "Ethernet1/2", "cable": {"id": "cable-2"}},
    ]

    async def get(
        path: str, params: dict[str, Any] | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        if path == "dcim/interfaces/":
            return {"results": interfaces, "next": None}
        if path == "extras/notes/":
            return {"results": []}
        return {"status": {"name": "Connected"}}

    client.get = AsyncMock(side_effect=get)
    client.patch = AsyncMock()
    client.post = AsyncMock()
    monkeypatch.setattr(
        "nv_config_manager_workflows.activities.cable_validation.activities.get_dcim_client",
        lambda: client,
    )
    statuses = {"Ethernet1/1": CableStatus.INVALID, "Ethernet1/2": CableStatus.INVALID}
    if unresolved_target:
        statuses["Ethernet1/3"] = CableStatus.INVALID
    activity_input = UpdateCableStatusesInput(
        device_id="device-1", cable_statuses=statuses, workflow_id="validation-1"
    )
    if unresolved_target:
        with pytest.raises(DCIMInvalidDataError, match="Ethernet1/3"):
            await update_cable_statuses(activity_input)
    else:
        await update_cable_statuses(activity_input)

    assert {invocation.args[0] for invocation in client.patch.await_args_list} == {
        "dcim/cables/cable-1/",
        "dcim/cables/cable-2/",
    }
