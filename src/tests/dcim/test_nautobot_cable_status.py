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
from nv_config_manager_dcim_nautobot_2x.client import (
    NautobotException,
    NautobotReadCancelledError,
)
from nv_config_manager_dcim_nautobot_2x.provider import (
    _CABLE_STATUS_INTERFACES_QUERY,
    NautobotDCIMClient,
)

from nv_config_manager_workflows.activities.cable_validation import (
    UpdateCableStatusesInput,
    update_cable_statuses,
)


def _rest_read(path: str, **kwargs: Any) -> dict[str, Any]:
    if path.startswith("dcim/cables/"):
        return {"status": {"name": "Connected"}}
    if path == "extras/notes/":
        return {"results": []}
    raise AssertionError(f"Unexpected REST request: {path}")


def _client() -> NautobotDCIMClient:
    client = NautobotDCIMClient("https://nautobot.example", "token")
    client.get = AsyncMock(side_effect=_rest_read)
    return client


def _interface(
    name: str = "Ethernet1/1", cable_id: str = "cable-1", status: str = "Connected"
) -> dict[str, Any]:
    return {"name": name, "cable": {"id": cable_id, "status": {"name": status}}}


def _snapshot(interfaces: object, device_id: str = "device-1") -> dict[str, Any]:
    return {"data": {"devices": [{"id": device_id}], "interfaces": interfaces}}


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
    client.graphql_query = AsyncMock(return_value=_snapshot([_interface()]))
    current_status = "Connected"

    def read(path: str, **kwargs: Any) -> dict[str, Any]:
        return (
            {"status": {"name": current_status}}
            if path.startswith("dcim/cables/")
            else _rest_read(path, **kwargs)
        )

    async def patch(path: str, data: dict[str, Any]) -> None:
        nonlocal current_status
        current_status = data["status"]

    client.get = AsyncMock(side_effect=read)
    client.patch = AsyncMock(side_effect=patch)
    client.post = AsyncMock()

    await client.update_cable_status(_update())
    await client.update_cable_status(_update())

    client.graphql_query.assert_awaited_once_with(
        _CABLE_STATUS_INTERFACES_QUERY,
        {"device_id": ["device-1"], "names": ["Ethernet1/1"]},
    )
    assert sum(call.args[0] == "extras/notes/" for call in client.get.await_args_list) == 1
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
    # Returning to the original status must use the live result of our first write.
    await client.update_cable_status(_update(CableStatus.CONNECTED))
    assert client.patch.await_count == 2
    client.patch.assert_awaited_with("dcim/cables/cable-1/", {"status": "Connected"})
    client.graphql_query.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(CableStatus))
@pytest.mark.parametrize("changed_after_lookup", [False, True])
async def test_skipping_cable_status_updates_requires_a_live_match(
    status: CableStatus, changed_after_lookup: bool
) -> None:
    client = _client()
    client.graphql_query = AsyncMock(return_value=_snapshot([_interface(status=status.value)]))
    live_status = (
        next(item for item in CableStatus if item != status) if changed_after_lookup else status
    )
    client.get = AsyncMock(
        side_effect=lambda path, **kwargs: (
            {"status": {"name": live_status.value}}
            if path.startswith("dcim/cables/")
            else _rest_read(path, **kwargs)
        )
    )
    client.patch = AsyncMock()
    client.post = AsyncMock()

    await client.update_cable_status(_update(status))

    client.get.assert_any_await("dcim/cables/cable-1/", params={"depth": 1})
    if changed_after_lookup:
        client.patch.assert_awaited_once_with("dcim/cables/cable-1/", {"status": status.value})
        client.post.assert_awaited_once()
    else:
        client.patch.assert_not_awaited()
        client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_cable_status_rejects_interface_without_cable() -> None:
    client = _client()
    client.graphql_query = AsyncMock(
        return_value=_snapshot([{"name": "Ethernet1/1", "cable": None}])
    )

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

    async def lookup(*args: object, **kwargs: object) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return _snapshot(
            [_interface(f"Ethernet1/{index}", f"cable-{index}") for index in range(1, 21)]
        )

    client.graphql_query = AsyncMock(side_effect=lookup)
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
    client.graphql_query.assert_awaited_once()
    assert client.get.await_count == 20

    async with client:
        await client.update_cable_status(_update(CableStatus.CONNECTED))
    assert client.graphql_query.await_count == 2


@pytest.mark.asyncio
async def test_failed_lookup_is_shared_until_the_next_client_context() -> None:
    client = _client()
    client.graphql_query = AsyncMock(side_effect=[TimeoutError(), _snapshot([_interface()])])
    async with client:
        errors = await asyncio.gather(
            *(client.update_cable_status(_update(CableStatus.CONNECTED)) for _ in range(20)),
            return_exceptions=True,
        )
        assert all(isinstance(error, TimeoutError) for error in errors)
        assert client.graphql_query.await_count == 1
    async with client:
        await client.update_cable_status(_update(CableStatus.CONNECTED))
    assert client.graphql_query.await_count == 2


@pytest.mark.asyncio
async def test_canceling_one_update_does_not_cancel_the_shared_lookup() -> None:
    client = _client()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def lookup(*args: object, **kwargs: object) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return _snapshot([_interface()])

    client.graphql_query = AsyncMock(side_effect=lookup)
    async with client:
        first = asyncio.create_task(client.update_cable_status(_update(CableStatus.CONNECTED)))
        second = asyncio.create_task(client.update_cable_status(_update(CableStatus.CONNECTED)))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        await second
    client.graphql_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_cancels_an_incomplete_interface_lookup() -> None:
    client = _client()
    entered = asyncio.Event()
    canceled = asyncio.Event()

    async def lookup(*args: object, **kwargs: object) -> dict[str, Any]:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
        return _snapshot([])

    client.graphql_query = AsyncMock(side_effect=lookup)
    update = asyncio.create_task(client.update_cable_status(_update()))
    await entered.wait()
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await update
    assert canceled.is_set()


@pytest.mark.asyncio
async def test_prepared_names_share_a_lookup_and_other_names_get_their_own() -> None:
    client = _client()
    client.graphql_query = AsyncMock(
        side_effect=[
            _snapshot([_interface(), _interface("Ethernet1/2", "cable-2")]),
            _snapshot([_interface("Ethernet1/3", "cable-3")]),
        ]
    )
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
    client.graphql_query.assert_awaited_once_with(
        _CABLE_STATUS_INTERFACES_QUERY,
        {"device_id": ["device-1"], "names": ["Ethernet1/1", "Ethernet1/2"]},
    )
    assert client.get.await_count == 2
    await client.update_cable_status(
        CableStatusUpdate(
            device_id="device-1",
            interface_name="Ethernet1/3",
            status=CableStatus.CONNECTED,
            workflow_id="validation-1",
        )
    )
    assert client.graphql_query.await_count == 2
    client.graphql_query.assert_awaited_with(
        _CABLE_STATUS_INTERFACES_QUERY,
        {"device_id": ["device-1"], "names": ["Ethernet1/3"]},
    )
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interfaces", "error", "message"),
    [
        ([], DCIMNotFoundError, "No interface named"),
        ([{"name": "Ethernet1/1"}] * 2, DCIMConflictError, "Multiple interfaces named"),
        ([{"id": "no-name"}], DCIMInvalidDataError, "invalid interface lookup"),
        (
            [{"name": "Ethernet1/1", "cable": {"id": "cable-1", "status": None}}],
            DCIMInvalidDataError,
            "invalid cable status",
        ),
    ],
)
async def test_interface_lookup_preserves_validation_errors(
    interfaces: list[dict[str, object]], error: type[Exception], message: str
) -> None:
    client = _client()
    client.graphql_query = AsyncMock(return_value=_snapshot(interfaces))
    client.get = AsyncMock(return_value={"status": None})
    with pytest.raises(error, match=message):
        await client.update_cable_status(_update())


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [None, [], {}, {"results": None}, {"results": {}}])
@pytest.mark.parametrize("later_page", [False, True])
async def test_get_all_rejects_malformed_pagination_pages(page: object, later_page: bool) -> None:
    client = _client()
    pages = [{"results": [{"name": "unrelated"}], "next": "page-2"}] if later_page else []
    client.get = AsyncMock(side_effect=[*pages, page])
    async with client:
        with pytest.raises(DCIMInvalidDataError, match="invalid pagination data"):
            await client.get_all("dcim/interfaces/")


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [_snapshot([]), {"data": {"devices": [], "interfaces": []}}])
async def test_cable_snapshot_preserves_not_found_for_missing_interfaces_or_device(
    response: dict[str, Any],
) -> None:
    client = _client()
    client.graphql_query = AsyncMock(return_value=response)
    async with client:
        with pytest.raises(DCIMNotFoundError, match="found"):
            await client.update_cable_status(_update())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {"data": None},
        {"data": {}},
        {"data": {"devices": None, "interfaces": []}},
        _snapshot([], device_id="wrong-device"),
        _snapshot(None),
        _snapshot({}),
    ],
)
async def test_cable_snapshot_rejects_invalid_graphql_data(response: object) -> None:
    client = _client()
    client.graphql_query = AsyncMock(return_value=response)
    client.patch = AsyncMock()
    async with client:
        with pytest.raises(DCIMInvalidDataError, match="invalid interface lookup.*device-1"):
            await client.update_cable_status(_update())
    client.patch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_lookup", [False, True])
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, DCIMInvalidDataError),
        (404, DCIMNotFoundError),
        (409, DCIMConflictError),
        (None, NautobotReadCancelledError),
    ],
)
async def test_batch_lookup_and_write_errors_retain_dcim_type_and_target_context(
    monkeypatch: pytest.MonkeyPatch,
    during_lookup: bool,
    status: int | None,
    error_type: type[Exception],
) -> None:
    client = _client()
    backend_error = (
        NautobotReadCancelledError("read canceled")
        if status is None
        else NautobotException("request rejected", status_code=status)
    )
    client.graphql_query = AsyncMock(
        side_effect=backend_error if during_lookup else None,
        return_value=_snapshot([_interface(), _interface("Ethernet1/2", "cable-2")]),
    )
    client.patch = AsyncMock(side_effect=backend_error)
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
    if during_lookup:
        client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_invalid_interface_raises_a_fresh_exception_each_time() -> None:
    client = _client()
    client.graphql_query = AsyncMock(return_value=_snapshot([{"id": "missing-name"}]))
    errors: list[DCIMInvalidDataError] = []
    async with client:
        await client._prepare_cable_status_updates("device-1", ["Ethernet1/1"])
        for _ in range(3):
            with pytest.raises(DCIMInvalidDataError, match="invalid interface lookup") as raised:
                await client.update_cable_status(_update())
            errors.append(raised.value)
    assert len({id(error) for error in errors}) == 3
    assert all(str(error) == str(errors[0]) for error in errors)
    client.graphql_query.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_count", [3, 500])
async def test_large_inventory_uses_one_snapshot_and_only_writes_requested_interfaces(
    monkeypatch: pytest.MonkeyPatch,
    requested_count: int,
) -> None:
    inventory = {
        f"Ethernet1/{index}": _interface(f"Ethernet1/{index}", f"cable-{index}")
        for index in range(2000)
    }
    requested = list(inventory)[:requested_count]
    clients: list[NautobotDCIMClient] = []

    async def lookup(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        assert variables == {"device_id": ["device-1"], "names": sorted(requested)}
        return _snapshot([inventory[name] for name in variables["names"]])

    def create_client() -> NautobotDCIMClient:
        client = _client()
        client.graphql_query = AsyncMock(side_effect=lookup)
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

    assert sum(client.patch.await_count for client in clients) == requested_count
    assert len(clients) == 1
    clients[0].graphql_query.assert_awaited_once_with(
        _CABLE_STATUS_INTERFACES_QUERY,
        {"device_id": ["device-1"], "names": sorted(requested)},
    )
    assert {call.args[0] for call in clients[0].patch.await_args_list} == {
        f"dcim/cables/cable-{index}/" for index in range(requested_count)
    }
    assert (
        sum(call.args[0] == "extras/notes/" for call in clients[0].get.await_args_list)
        == requested_count
    )
    assert (
        sum(call.args[0].startswith("dcim/cables/") for call in clients[0].get.await_args_list)
        == requested_count
    )


@pytest.mark.asyncio
async def test_large_lookup_error_keeps_target_summary_bounded() -> None:
    client = _client()
    client.graphql_query = AsyncMock(
        side_effect=NautobotException("upstream unavailable", status_code=500)
    )
    names = [f"Ethernet1/{index}" for index in range(500)]
    async with client:
        with pytest.raises(NautobotException) as raised:
            await client._prepare_cable_status_updates("device-1", names)
    message = str(raised.value)
    assert "500 interfaces" in message and "device-1" in message
    assert "Ethernet1/0" in message and "Ethernet1/499" not in message
    assert len(message) < 512


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
    client._prepare_cable_status_updates.assert_awaited_once_with(
        "device-1", [f"Ethernet1/{index}" for index in range(51)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_status", [False, True])
async def test_invalid_interface_does_not_prevent_other_nautobot_updates(
    invalid_status: bool,
) -> None:
    client = _client()
    interfaces = [_interface(f"Ethernet1/{index}", f"Ethernet1-{index}") for index in range(51)]
    interfaces[0] = (
        {"name": "Ethernet1/0", "cable": {"id": "Ethernet1-0", "status": None}}
        if invalid_status
        else {"id": "malformed"}
    )
    client.graphql_query = AsyncMock(return_value=_snapshot(interfaces))
    client.get = AsyncMock(
        side_effect=lambda path, **kwargs: (
            {"status": None}
            if invalid_status and path == "dcim/cables/Ethernet1-0/"
            else _rest_read(path, **kwargs)
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
    client.graphql_query.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved_target", [False, True])
async def test_malformed_record_does_not_block_other_interface_writes(
    monkeypatch: pytest.MonkeyPatch, unresolved_target: bool
) -> None:
    client = _client()
    interfaces = [
        _interface(),
        {"id": "malformed-interface"},
        _interface("Ethernet1/2", "cable-2"),
    ]

    client.graphql_query = AsyncMock(return_value=_snapshot(interfaces))
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
