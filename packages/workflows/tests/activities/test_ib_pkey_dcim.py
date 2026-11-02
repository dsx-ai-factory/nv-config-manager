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
"""Provider-neutral tests for InfiniBand PKey DCIM activities."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_dcim import (
    DCIMClient,
    IBHostSite,
    IBInterfaceGuid,
    IBPKeyAssignment,
    IBPKeyCleanup,
    IBPKeyContext,
    IBPKeyPartition,
)
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_pkey import (
    IB_PKEY_ACTIVITIES,
    CleanupEmptyPartitionInput,
    CreatePartitionInDCIMInput,
    FetchPKeyAssignmentsInput,
    InterfaceRef,
    RecordIBPKeyInDCIMInput,
    RecordPKeyAssignmentsInput,
    RemovePKeyAssignmentsInput,
    ResolvedInterface,
    ResolveGuidsToInterfacesInput,
    ResolveIBContextInput,
    ResolveIBSiteForHostInput,
    ResolveInterfaceGuidsInput,
    SyncPKeyAssignmentsInput,
    cleanup_empty_pkey_partition,
    create_partition_in_dcim,
    create_partition_in_nautobot,
    fetch_pkey_assignments,
    record_ib_pkey_in_dcim,
    record_ib_pkey_in_nautobot,
    record_pkey_assignments,
    remove_pkey_assignments,
    resolve_guids_to_interfaces,
    resolve_ib_context,
    resolve_ib_context_for_add,
    resolve_ib_site_for_host,
    resolve_interface_guids,
    sync_pkey_assignments,
)
from nv_config_manager_workflows.activities.ib_pkey.resolution import (
    _is_auto_created_overlay_name,
    _normalize_pkey,
    _select_pkey_match,
)
from nv_config_manager_workflows.mixins.ib_pkey import (
    UFMHostLockMixin,
    UFMHostSiteValidationMixin,
)
from nv_config_manager_workflows.runtime import configure_dcim_client

_DEVICE_NAME = "ufm01"
_DEVICE_IP = "10.0.0.5"
_SITE_ID = "354dae20-64ef-4a7f-b1ca-2b584d20fa94"
_SITE_NAME = "site-a"


def test_pkey_catalog_preserves_dcim_activity_order() -> None:
    """The combined catalog registers all fourteen DCIM activities in order."""
    assert len(IB_PKEY_ACTIVITIES) == 23
    assert IB_PKEY_ACTIVITIES[9:] == (
        record_ib_pkey_in_dcim,
        record_ib_pkey_in_nautobot,
        create_partition_in_dcim,
        create_partition_in_nautobot,
        resolve_interface_guids,
        resolve_guids_to_interfaces,
        resolve_ib_context,
        resolve_ib_context_for_add,
        resolve_ib_site_for_host,
        record_pkey_assignments,
        fetch_pkey_assignments,
        sync_pkey_assignments,
        remove_pkey_assignments,
        cleanup_empty_pkey_partition,
    )


class _HostInput(BaseModel):
    host: str


class _HostSiteInput(BaseModel):
    host: str
    site: str | None = None


class StubIBDCIMClient:
    """Minimal runtime-injected client for canonicalization tests."""

    def __init__(self, *, primary_ip: str | None = _DEVICE_IP) -> None:
        self.primary_ip = primary_ip
        self.canonicalized_hosts: list[str] = []
        self.resolved_hosts: list[str] = []

    async def __aenter__(self) -> StubIBDCIMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    async def canonicalize_ib_host(self, host: str) -> str:
        self.canonicalized_hosts.append(host)
        return self.primary_ip or _DEVICE_NAME

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        self.resolved_hosts.append(host)
        return IBHostSite(
            device_id="device-1",
            device_name=_DEVICE_NAME,
            device_primary_ip=self.primary_ip,
            site_id=_SITE_ID,
            site_name=_SITE_NAME,
        )


def _configure_client(*, primary_ip: str | None = _DEVICE_IP) -> StubIBDCIMClient:
    client = StubIBDCIMClient(primary_ip=primary_ip)
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


def _mock_client() -> MagicMock:
    client = MagicMock(spec=DCIMClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


def _host_site() -> IBHostSite:
    return IBHostSite(
        device_id="device-1",
        device_name=_DEVICE_NAME,
        device_primary_ip=_DEVICE_IP,
        site_id=_SITE_ID,
        site_name=_SITE_NAME,
    )


def _resolved(
    interface_id: str = "interface-1",
    guid: str = "0002c903000e0b72",
    membership: str = "full",
) -> ResolvedInterface:
    return ResolvedInterface(
        device="host-1",
        interface="mlx5_0",
        interface_id=interface_id,
        guid=guid,
        membership=membership,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0x100", "0x0100"),
        ("0x0100", "0x0100"),
        ("0x1", "0x0001"),
        ("0X100", "0x0100"),
        ("0xFFF", "0x0fff"),
        ("0x8001", "0x8001"),
        ("0xfffe", "0xfffe"),
    ],
)
def test_normalize_pkey_canonicalizes_supported_forms(raw: str, expected: str) -> None:
    assert _normalize_pkey(raw) == expected


@pytest.mark.parametrize("raw", ["", "100", "0x", "0x12345", "0xZZZZ", "not-hex", "0x-1"])
def test_normalize_pkey_rejects_invalid_forms(raw: str) -> None:
    with pytest.raises(ApplicationError, match="does not match required format") as exc_info:
        _normalize_pkey(raw)

    assert exc_info.value.non_retryable is True


def _pkey_device(
    *,
    pkeys: list[dict[str, str]] | None = None,
    child_overlays: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    overlay = {
        "id": "overlay-1",
        "name": "ib-pkey-0x0100",
        "pkeys": pkeys if pkeys is not None else [{"id": "pkey-1", "pkey": "0x0100"}],
    }
    return {
        "location": {
            "id": "datahall-1",
            "name": "datahall-a",
            "overlays": child_overlays or [],
            "parent": {
                "id": "site-1",
                "name": "site-a",
                "overlays": [overlay],
                "parent": {"id": "region-1", "name": "region-a", "overlays": []},
            },
        }
    }


def test_select_pkey_match_walks_location_chain_and_detects_ambiguity() -> None:
    match = {"id": "pkey-1", "pkey": "0x100"}
    device: dict[str, Any] = {
        "location": {
            "id": "datahall-1",
            "name": "datahall-a",
            "overlays": [],
            "parent": {
                "id": "site-1",
                "name": "site-a",
                "overlays": [{"id": "overlay-1", "name": "ib", "pkeys": [match]}],
            },
        }
    }

    location, overlay, pkey = _select_pkey_match(device, "0x0100")

    assert location["id"] == "site-1"
    assert overlay["id"] == "overlay-1"
    assert pkey is match

    device["location"]["overlays"] = [{"id": "overlay-2", "name": "duplicate", "pkeys": [match]}]
    with pytest.raises(ApplicationError, match="ambiguous near location") as exc_info:
        _select_pkey_match(device, "0x0100")

    assert exc_info.value.non_retryable is True


def test_select_pkey_match_accepts_format_variants_and_ignores_malformed_records() -> None:
    device = _pkey_device(
        pkeys=[
            {"id": "malformed", "pkey": "junk"},
            {"id": "pkey-1", "pkey": "0x100"},
        ]
    )

    location, overlay, pkey = _select_pkey_match(device, "0x0100")

    assert location["id"] == "site-1"
    assert overlay["id"] == "overlay-1"
    assert pkey["id"] == "pkey-1"


def test_select_pkey_match_preserves_not_found_error_context() -> None:
    device = _pkey_device(pkeys=[{"id": "pkey-2", "pkey": "0x8001"}])

    with pytest.raises(ApplicationError, match="not found at or above location") as exc_info:
        _select_pkey_match(device, "0x0100")

    assert "datahall-a" in exc_info.value.message
    assert exc_info.value.non_retryable is True


def test_select_pkey_match_rejects_duplicate_matches_at_one_location() -> None:
    device = _pkey_device()
    device["location"]["parent"]["overlays"].append(
        {
            "id": "overlay-2",
            "name": "duplicate",
            "pkeys": [{"id": "pkey-2", "pkey": "0x100"}],
        }
    )

    with pytest.raises(ApplicationError, match="ambiguous near location") as exc_info:
        _select_pkey_match(device, "0x0100")

    assert exc_info.value.non_retryable is True


@pytest.mark.parametrize(
    ("overlay_name", "pkey", "expected"),
    [
        ("ib-pkey-overlay-0x8001", "0x8001", True),
        ("my-vpc", "0x8001", False),
        ("ib-pkey-overlay-0x8001", "0x8002", False),
    ],
)
def test_auto_created_overlay_name_uses_the_owned_naming_scheme(
    overlay_name: str, pkey: str, expected: bool
) -> None:
    assert _is_auto_created_overlay_name(overlay_name, pkey) is expected


async def test_hostname_and_ip_canonicalize_to_the_same_identifier() -> None:
    """Equivalent UFM identifiers collapse before workflow lock construction."""
    client = _configure_client()

    first = _HostInput(host=_DEVICE_NAME)
    second = _HostInput(host=_DEVICE_IP)

    assert await UFMHostLockMixin.canonicalize_input(first) is first
    assert await UFMHostLockMixin.canonicalize_input(second) is second
    assert (first.host, second.host) == (_DEVICE_IP, _DEVICE_IP)
    assert client.canonicalized_hosts == [_DEVICE_NAME, _DEVICE_IP]


async def test_site_canonicalization_falls_back_to_device_name_without_primary_ip() -> None:
    """A managed UFM without a primary IP still has a stable identifier."""
    client = _configure_client(primary_ip=None)

    body = _HostSiteInput(host=_DEVICE_NAME)

    assert await UFMHostSiteValidationMixin.canonicalize_input(body) is body
    assert body.host == _DEVICE_NAME
    assert client.resolved_hosts == [_DEVICE_NAME]


@pytest.mark.parametrize("site_reference", [_SITE_ID, _SITE_NAME, None])
async def test_site_canonicalization_accepts_site_references(
    site_reference: str | None,
) -> None:
    """Site IDs, names, and an omitted override validate against the provider result."""
    _configure_client()

    body = _HostSiteInput(host=_DEVICE_NAME, site=site_reference)

    assert await UFMHostSiteValidationMixin.canonicalize_input(body) is body
    assert body.host == _DEVICE_IP


async def test_site_canonicalization_preserves_mismatch_error_contract() -> None:
    """Site mismatches remain permanent failures with the existing message."""
    _configure_client()

    with pytest.raises(ApplicationError) as exc_info:
        await UFMHostSiteValidationMixin.canonicalize_input(
            _HostSiteInput(host=_DEVICE_NAME, site="site-b")
        )

    assert exc_info.value.message == ("UFM device 'ufm01' belongs to Site 'site-a', not 'site-b'")
    assert exc_info.value.non_retryable is True


async def test_partition_activities_preserve_provider_arguments_and_outputs() -> None:
    client = _mock_client()
    client.ensure_ib_pkey_partition = AsyncMock(
        return_value=IBPKeyPartition(
            partition_id="overlay-1",
            partition_name="tenant-a",
            pkey_id="pkey-1",
            pkey="0x0005",
        )
    )
    client.ensure_orphan_ib_pkey = AsyncMock(
        return_value=IBPKeyPartition(pkey_id="pkey-2", pkey="0x0006")
    )

    created = await create_partition_in_dcim(
        CreatePartitionInDCIMInput(
            pkey="0x0005",
            partition_name="tenant-a",
            location_name="site-a",
            tenant_name="tenant-a",
            membership_type="limited",
        )
    )
    recorded = await record_ib_pkey_in_dcim(RecordIBPKeyInDCIMInput(pkey="0x0006"))

    client.ensure_ib_pkey_partition.assert_awaited_once_with(
        "0x0005", "tenant-a", "site-a", "tenant-a", "limited"
    )
    client.ensure_orphan_ib_pkey.assert_awaited_once_with("0x0006")
    assert created.model_dump() == {
        "display": "Partition 'tenant-a' and PKey 0x0005 recorded in DCIM",
        "partition_id": "overlay-1",
        "partition_name": "tenant-a",
        "pkey_id": "pkey-1",
        "pkey": "0x0005",
    }
    assert recorded.pkey_id == "pkey-2"


async def test_resolve_interface_guids_preserves_order_and_membership() -> None:
    client = _mock_client()
    client.get_ib_interface_records = AsyncMock(
        return_value=[
            IBInterfaceGuid(
                interface_id="interface-2",
                device_name="host-2",
                interface_name="mlx5_1",
                guid="0002c903000e0b73",
            ),
            IBInterfaceGuid(
                interface_id="interface-1",
                device_name="host-1",
                interface_name="mlx5_0",
                guid="0002c903000e0b72",
            ),
        ]
    )
    input = ResolveInterfaceGuidsInput(
        interfaces=[
            InterfaceRef(device="host-1", interface="mlx5_0", membership="limited"),
            InterfaceRef(device="host-2", interface="mlx5_1"),
        ],
        default_membership="full",
    )

    output = await resolve_interface_guids(input)

    assert [item.interface_id for item in output.resolved] == ["interface-1", "interface-2"]
    assert [item.membership for item in output.resolved] == ["limited", "full"]
    client.get_ib_interface_records.assert_awaited_once_with(
        [("host-1", "mlx5_0"), ("host-2", "mlx5_1")]
    )


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([], "Interface 'mlx5_0' on device 'host-1' not found in DCIM"),
        (
            [
                IBInterfaceGuid(
                    interface_id="interface-1",
                    device_name="host-1",
                    interface_name="mlx5_0",
                    guid="",
                )
            ],
            "Interface 'mlx5_0' on device 'host-1' has no IB GUID set in DCIM",
        ),
    ],
)
async def test_resolve_interface_guids_preserves_permanent_failures(
    records: list[IBInterfaceGuid], message: str
) -> None:
    client = _mock_client()
    client.get_ib_interface_records = AsyncMock(return_value=records)

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_interface_guids(
            ResolveInterfaceGuidsInput(
                interfaces=[InterfaceRef(device="host-1", interface="mlx5_0")]
            )
        )

    assert exc_info.value.message == message
    assert exc_info.value.non_retryable is True


async def test_reverse_guid_resolution_normalizes_deduplicates_and_preserves_membership() -> None:
    client = _mock_client()
    client.find_ib_interfaces_by_guids = AsyncMock(
        return_value=[
            IBInterfaceGuid(
                interface_id="interface-1",
                device_name="host-1",
                interface_name="mlx5_0",
                guid="0002C903000E0B72",
            )
        ]
    )

    output = await resolve_guids_to_interfaces(
        ResolveGuidsToInterfacesInput(
            guids=["0x0002C903000E0B72", "0002c903000e0b72"],
            guid_memberships=["limited", "limited"],
        )
    )

    client.find_ib_interfaces_by_guids.assert_awaited_once_with(["0002c903000e0b72"])
    assert output.resolved == [_resolved(guid="0002C903000E0B72", membership="limited")]


@pytest.mark.parametrize(
    ("input", "message"),
    [
        (
            ResolveGuidsToInterfacesInput(guids=["", "  "]),
            "All provided GUIDs were empty",
        ),
        (
            ResolveGuidsToInterfacesInput(guids=["guid-1"], guid_memberships=[]),
            "guid_memberships length (0) must match guids length (1)",
        ),
    ],
)
async def test_reverse_guid_resolution_rejects_invalid_inputs(
    input: ResolveGuidsToInterfacesInput, message: str
) -> None:
    _mock_client()

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_guids_to_interfaces(input)

    assert exc_info.value.message == message
    assert exc_info.value.non_retryable is True


async def test_reverse_guid_resolution_reports_missing_provider_match() -> None:
    client = _mock_client()
    client.find_ib_interfaces_by_guids = AsyncMock(return_value=[])

    with pytest.raises(ApplicationError, match="No DCIM interface found") as exc_info:
        await resolve_guids_to_interfaces(ResolveGuidsToInterfacesInput(guids=["GUID-1"]))

    assert exc_info.value.non_retryable is True


async def test_assignment_activities_preserve_provider_arguments_and_results() -> None:
    client = _mock_client()
    client.ensure_ib_pkey_assignments = AsyncMock(return_value=["assignment-1"])
    client.remove_ib_pkey_assignments = AsyncMock(return_value=(["assignment-1"], ["interface-2"]))
    client.get_ib_pkey_assignments = AsyncMock(
        return_value=[
            IBPKeyAssignment(
                assignment_id="assignment-1",
                interface_id="interface-1",
                guid="0002c903000e0b72",
                membership_type="FULL",
            )
        ]
    )
    client.sync_ib_pkey_assignments = AsyncMock(
        return_value=(["assignment-2"], ["assignment-1"], ["assignment-3"])
    )
    desired = _resolved(membership="limited")

    recorded = await record_pkey_assignments(
        RecordPKeyAssignmentsInput(overlay_id="overlay-1", resolved=[desired])
    )
    removed = await remove_pkey_assignments(
        RemovePKeyAssignmentsInput(
            overlay_id="overlay-1", interface_ids=["interface-1", "interface-2"]
        )
    )
    fetched = await fetch_pkey_assignments(FetchPKeyAssignmentsInput(overlay_id="overlay-1"))
    synced = await sync_pkey_assignments(
        SyncPKeyAssignmentsInput(overlay_id="overlay-1", desired=[desired])
    )

    assignment = [("interface-1", "0002c903000e0b72", "limited")]
    client.ensure_ib_pkey_assignments.assert_awaited_once_with("overlay-1", assignment)
    client.remove_ib_pkey_assignments.assert_awaited_once_with(
        "overlay-1", ["interface-1", "interface-2"]
    )
    client.get_ib_pkey_assignments.assert_awaited_once_with("overlay-1")
    client.sync_ib_pkey_assignments.assert_awaited_once_with("overlay-1", assignment)
    assert recorded.assignment_ids == ["assignment-1"]
    assert removed.assignment_ids_removed == ["assignment-1"]
    assert removed.interface_ids_not_assigned == ["interface-2"]
    assert fetched.assignments[0].membership_type == "full"
    assert synced.added == ["assignment-2"]
    assert synced.removed == ["assignment-1"]
    assert synced.unchanged == ["assignment-3"]


@pytest.mark.parametrize(
    ("cleanup", "display"),
    [
        (
            IBPKeyCleanup(
                partition_empty=False,
                pkey_deleted=False,
                overlay_deleted=False,
                remaining_assignments=2,
            ),
            "Overlay overlay-1 still has 2 member(s); leaving PKey and Overlay in place",
        ),
        (
            IBPKeyCleanup(
                partition_empty=False,
                pkey_deleted=False,
                overlay_deleted=False,
            ),
            "PKey 0x0005 still has untracked members on UFM; leaving PKey and Overlay in place",
        ),
        (
            IBPKeyCleanup(
                partition_empty=True,
                pkey_deleted=True,
                overlay_deleted=True,
            ),
            "Empty PKey partition reconciled; deleted InfiniBandPKey + Overlay",
        ),
    ],
)
async def test_cleanup_preserves_provider_result_and_display(
    cleanup: IBPKeyCleanup, display: str
) -> None:
    client = _mock_client()
    client.cleanup_ib_pkey_partition = AsyncMock(return_value=cleanup)
    input = CleanupEmptyPartitionInput(
        overlay_id="overlay-1",
        overlay_name="ib-pkey-overlay-0x0005",
        pkey_id="pkey-1",
        pkey="0x0005",
        ufm_partition_empty=True,
    )

    output = await cleanup_empty_pkey_partition(input)

    client.cleanup_ib_pkey_partition.assert_awaited_once_with(
        "overlay-1", "ib-pkey-overlay-0x0005", "pkey-1", "0x0005", True
    )
    assert output.display == display
    assert output.partition_empty is cleanup.partition_empty
    assert output.pkey_deleted is cleanup.pkey_deleted
    assert output.overlay_deleted is cleanup.overlay_deleted


async def test_context_activities_preserve_provider_models_and_lazy_create_flag() -> None:
    client = _mock_client()
    host_site = _host_site()
    context = IBPKeyContext(
        host_site=host_site,
        overlay_id="overlay-1",
        overlay_name="ib-pkey-0x0005",
        pkey_id="pkey-1",
        pkey="0x0005",
    )
    client.resolve_ib_host_site = AsyncMock(return_value=host_site)
    client.resolve_ib_pkey_context = AsyncMock(return_value=context)

    site = await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=_DEVICE_NAME))
    resolved = await resolve_ib_context(ResolveIBContextInput(host=_DEVICE_NAME, pkey="0x5"))
    resolved_for_add = await resolve_ib_context_for_add(
        ResolveIBContextInput(host=_DEVICE_NAME, pkey="0x5")
    )

    client.resolve_ib_host_site.assert_awaited_once_with(_DEVICE_NAME)
    assert client.resolve_ib_pkey_context.await_args_list[0].args == (_DEVICE_NAME, "0x5")
    assert client.resolve_ib_pkey_context.await_args_list[1].args == (_DEVICE_NAME, "0x5")
    assert client.resolve_ib_pkey_context.await_args_list[1].kwargs == {
        "create_overlay_for_orphan": True
    }
    assert site.location_name == _SITE_NAME
    assert resolved.overlay_id == "overlay-1"
    assert resolved_for_add.pkey == "0x0005"
