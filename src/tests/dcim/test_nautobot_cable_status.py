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
    DCIMCableStatusBulkClient,
    DCIMCableStatusClient,
)
from nv_config_manager_dcim.errors import DCIMConflictError, DCIMInvalidDataError, DCIMNotFoundError
from nv_config_manager_dcim_nautobot_2x.client import NautobotException
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
    assert isinstance(_client(), DCIMCableStatusBulkClient)


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
            client._prepare_cable_status_updates(
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
    await client._prepare_cable_status_updates("device-1", ["Ethernet1/1", "Ethernet1/2"])
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
@pytest.mark.parametrize("during_lookup", [False, True])
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(400, DCIMInvalidDataError), (404, DCIMNotFoundError), (409, DCIMConflictError)],
)
async def test_batch_lookup_and_write_errors_retain_dcim_type_and_target_context(
    monkeypatch: pytest.MonkeyPatch, during_lookup: bool, status: int, error_type: type[Exception]
) -> None:
    client = _client()
    backend_error = NautobotException("request rejected", status_code=status)
    client.get_all = AsyncMock(
        side_effect=backend_error if during_lookup else None,
        return_value=[
            {"name": "Ethernet1/1", "cable": {"id": "cable-1"}},
            {"name": "Ethernet1/2", "cable": {"id": "cable-2"}},
        ],
    )
    client.get = AsyncMock(side_effect=backend_error)
    client.patch = AsyncMock()
    monkeypatch.setattr(
        "nv_config_manager_workflows.activities.cable_validation.activities.get_dcim_client",
        lambda: client,
    )
    with pytest.raises(error_type) as raised:
        await update_cable_statuses(
            UpdateCableStatusesInput(
                device_id="device-1",
                cable_statuses={
                    "Ethernet1/1": CableStatus.INVALID,
                    "Ethernet1/2": CableStatus.INVALID,
                },
                workflow_id="validation-1",
            )
        )
    assert "device-1" in str(raised.value)
    assert "Ethernet1/1" in str(raised.value)
    if during_lookup:
        assert "Ethernet1/2" in str(raised.value)
        client.get.assert_not_awaited()
    assert raised.value.__cause__ is backend_error
    client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_invalid_interface_raises_a_fresh_exception_each_time() -> None:
    client = _client()
    client.get_all = AsyncMock(return_value=[{"id": "missing-name"}])
    errors: list[DCIMInvalidDataError] = []
    async with client:
        await client._prepare_cable_status_updates("device-1", ["Ethernet1/1"])
        for _ in range(3):
            with pytest.raises(DCIMInvalidDataError, match="invalid interface lookup") as raised:
                await client.update_cable_status(_update())
            errors.append(raised.value)
    assert len({id(error) for error in errors}) == 3
    assert all(str(error) == str(errors[0]) for error in errors)
    client.get_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_large_inventory_lookup_reads_only_requested_batch_names(
    monkeypatch: pytest.MonkeyPatch,
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
    await update_cable_statuses(
        UpdateCableStatusesInput(
            device_id="device-1",
            cable_statuses=dict.fromkeys(requested, CableStatus.INVALID),
            workflow_id="validation-1",
        )
    )

    assert len(lookups) == 20
    assert sum(map(len, lookups)) == 500
    assert {name for names in lookups for name in names} == set(requested)
    assert sum(client.patch.await_count for client in clients) == 500
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_nautobot_bulk_writes_bound_concurrency_in_the_plugin() -> None:
    client = _client()
    client._prepare_cable_status_updates = AsyncMock()
    active = 0
    maximum_active = 0

    async def update_one(update: CableStatusUpdate) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    client.update_cable_status = AsyncMock(side_effect=update_one)
    await client.update_cable_statuses(
        [
            CableStatusUpdate(
                device_id="device-1",
                interface_name=f"Ethernet1/{index}",
                status=CableStatus.CONNECTED,
                workflow_id="validation-1",
            )
            for index in range(51)
        ]
    )
    assert maximum_active == 2
    assert client.update_cable_status.await_count == 51
    assert [
        len(invocation.args[1])
        for invocation in client._prepare_cable_status_updates.await_args_list
    ] == [25, 25, 1]


@pytest.mark.asyncio
async def test_invalid_interface_does_not_prevent_later_nautobot_batches() -> None:
    client = _client()

    async def lookup(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"id": "malformed"}
            if name == "Ethernet1/0"
            else {"name": name, "cable": {"id": name.replace("/", "-")}}
            for name in params["name"]
        ]

    client.get_all = AsyncMock(side_effect=lookup)
    client.get = AsyncMock(
        side_effect=lambda path, **kwargs: (
            {"results": []} if path == "extras/notes/" else {"status": {"name": "Connected"}}
        )
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()
    async with client:
        with pytest.raises(DCIMInvalidDataError, match="Ethernet1/0"):
            await client.update_cable_statuses(
                [
                    CableStatusUpdate(
                        device_id="device-1",
                        interface_name=f"Ethernet1/{index}",
                        status=CableStatus.INVALID,
                        workflow_id="validation-1",
                    )
                    for index in range(51)
                ]
            )
    assert client.patch.await_count == 50
    client.patch.assert_any_await("dcim/cables/Ethernet1-50/", {"status": "Invalid"})


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
