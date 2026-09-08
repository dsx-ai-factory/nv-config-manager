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
"""Unit tests for SpX Overlay activity logic."""

import re
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient, NautobotException
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.ngc.activities.nautobot import (
    DeleteOverlayInput,
    GetAvailableRouteDistinguishersInput,
    ProvisionVrfInput,
    ReconcileSpXOverlayAssignmentsInput,
    RemoveUnmappedDeviceVrfsInput,
    VrfDeletionActivityInput,
    _vni_from_rd,
    delete_overlay,
    delete_vrf,
    get_available_route_distinguishers,
    provision_vrf,
    reconcile_spx_overlay_assignments,
    remove_unmapped_device_vrfs,
)

NAUTOBOT = "https://nautobot.example.com"
OVERLAYS_BASE = f"{NAUTOBOT}/api/plugins/overlays"

# Shared UUIDs
STATUS_ID = "aaaa0000-0000-0000-0000-000000000001"
LOCATION_ID = "bbbb0000-0000-0000-0000-000000000001"
TENANT_ID = "cccc0000-0000-0000-0000-000000000001"
OVERLAY_ID = "dddd0000-0000-0000-0000-000000000001"
VRF_ID = "eeee0000-0000-0000-0000-000000000001"
VXLAN_ID = "ffff0000-0000-0000-0000-000000000001"
ASSIGNMENT_ID = "99990000-0000-0000-0000-000000000001"
NS_ID = "11110000-0000-0000-0000-000000000001"


def _r(url):
    """Regex that matches url as a prefix, ignoring query params."""
    return re.compile(re.escape(url))


def _lookup(id_):
    return {"results": [{"id": id_}]}


def _request_json(mocked, method, url):
    for (request_method, request_url), calls in mocked.requests.items():
        if request_method.lower() == method.lower() and str(request_url) == url:
            return calls[0].kwargs["json"]
    raise AssertionError(f"No {method.upper()} request found for {url}")


def _namespace_graphql_response(*rds, namespace_id=NS_ID, namespace_name="spectrumx_rno1"):
    return {
        "data": {
            "namespaces": [
                {
                    "id": namespace_id,
                    "name": namespace_name,
                    "vrfs": [{"rd": rd} for rd in rds],
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# reconcile_spx_overlay_assignments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_moves_port_between_overlays():
    device_id = "22220000-0000-0000-0000-000000000001"
    interface_id = "33330000-0000-0000-0000-000000000001"
    existing_interface_id = "33330000-0000-0000-0000-000000000002"
    old_overlay_id = "44440000-0000-0000-0000-000000000001"
    ib_overlay_id = "55550000-0000-0000-0000-000000000001"
    old_assignment_id = "66660000-0000-0000-0000-000000000001"

    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={
                "results": [
                    {
                        "id": OVERLAY_ID,
                        "name": "Panda",
                        "isolation_type": "spectrum_x_vrf",
                    }
                ]
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": "device-assignment",
                        "overlay": {
                            "id": OVERLAY_ID,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": "ib-assignment",
                        "overlay": {
                            "id": ib_overlay_id,
                            "isolation_type": "ib_pkey",
                        },
                    },
                ],
                "next": (
                    f"{OVERLAYS_BASE}/overlay-assignments/"
                    f"?assigned_object_id={interface_id}&depth=1&limit=50&offset=50"
                ),
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": old_assignment_id,
                        "overlay": {
                            "id": old_overlay_id,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ],
                "next": None,
            },
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{old_assignment_id}/",
            status=204,
        )
        m.get(
            _r(f"{NAUTOBOT}/api/extras/statuses/"),
            payload={"results": [{"id": STATUS_ID}]},
        )
        m.post(
            f"{OVERLAYS_BASE}/overlay-assignments/",
            payload={"id": "new-interface-assignment"},
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": "existing-interface-assignment",
                        "overlay": {
                            "id": OVERLAY_ID,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )

        result = await reconcile_spx_overlay_assignments(
            ReconcileSpXOverlayAssignmentsInput(
                overlay_id="Panda",
                site=LOCATION_ID,
                device_id=device_id,
                interface_ids=[interface_id, existing_interface_id],
                device_interface_ids=[interface_id, existing_interface_id],
            )
        )

    assert result.created == 1
    assert result.removed == 1
    assert _request_json(m, "post", f"{OVERLAYS_BASE}/overlay-assignments/") == {
        "overlay": OVERLAY_ID,
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": interface_id,
        "status": STATUS_ID,
    }
    assignment_mutations = [
        request_method.lower()
        for (request_method, request_url) in m.requests
        if str(request_url).startswith(f"{OVERLAYS_BASE}/overlay-assignments/")
        and request_method.lower() in {"post", "delete"}
    ]
    assert assignment_mutations == ["post", "delete"]


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_rejects_non_spx_overlay():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={
                "results": [
                    {
                        "id": OVERLAY_ID,
                        "name": "Panda",
                        "isolation_type": "ib_pkey",
                    }
                ]
            },
        )

        with pytest.raises(ApplicationError, match="is not a spectrum_x_vrf overlay"):
            await reconcile_spx_overlay_assignments(
                ReconcileSpXOverlayAssignmentsInput(
                    overlay_id="Panda",
                    site=LOCATION_ID,
                    device_id="22220000-0000-0000-0000-000000000001",
                    interface_ids=["33330000-0000-0000-0000-000000000001"],
                    device_interface_ids=["33330000-0000-0000-0000-000000000001"],
                )
            )

    assert all("overlay-assignments" not in str(request_url) for _, request_url in m.requests)


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_keeps_old_assignment_if_create_fails():
    device_id = "22220000-0000-0000-0000-000000000001"
    interface_id = "33330000-0000-0000-0000-000000000001"
    old_assignment_id = "66660000-0000-0000-0000-000000000001"

    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={
                "results": [
                    {
                        "id": OVERLAY_ID,
                        "name": "Panda",
                        "isolation_type": "spectrum_x_vrf",
                    }
                ]
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": "device-assignment",
                        "overlay": {
                            "id": OVERLAY_ID,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": old_assignment_id,
                        "overlay": {
                            "id": "44440000-0000-0000-0000-000000000001",
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )
        m.get(
            _r(f"{NAUTOBOT}/api/extras/statuses/"),
            payload={"results": [{"id": STATUS_ID}]},
        )
        m.post(
            f"{OVERLAYS_BASE}/overlay-assignments/",
            status=400,
            payload={"detail": "replacement rejected"},
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{old_assignment_id}/",
            status=204,
        )

        with pytest.raises(ApplicationError, match="replacement rejected"):
            await reconcile_spx_overlay_assignments(
                ReconcileSpXOverlayAssignmentsInput(
                    overlay_id="Panda",
                    site=LOCATION_ID,
                    device_id=device_id,
                    interface_ids=[interface_id],
                    device_interface_ids=[interface_id],
                )
            )

    assert all(request_method.lower() != "delete" for request_method, _ in m.requests)


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_removes_port_without_replacement():
    device_id = "22220000-0000-0000-0000-000000000001"
    interface_id = "33330000-0000-0000-0000-000000000001"
    other_interface_id = "33330000-0000-0000-0000-000000000002"
    old_overlay_id = "44440000-0000-0000-0000-000000000001"
    interface_assignment_id = "66660000-0000-0000-0000-000000000001"
    device_assignment_id = "77770000-0000-0000-0000-000000000001"

    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": device_assignment_id,
                        "overlay": {
                            "id": old_overlay_id,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={
                "results": [
                    {
                        "id": interface_assignment_id,
                        "overlay": {
                            "id": old_overlay_id,
                            "isolation_type": "spectrum_x_vrf",
                        },
                    }
                ]
            },
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{interface_assignment_id}/",
            status=204,
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={"results": []},
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{device_assignment_id}/",
            status=204,
        )

        result = await reconcile_spx_overlay_assignments(
            ReconcileSpXOverlayAssignmentsInput(
                overlay_id=None,
                site=LOCATION_ID,
                device_id=device_id,
                interface_ids=[interface_id],
                device_interface_ids=[interface_id, other_interface_id],
            )
        )

    assert result.created == 0
    assert result.removed == 2
    assert all(
        not str(request_url).startswith(f"{OVERLAYS_BASE}/overlays/")
        for _, request_url in m.requests
    )


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_retry_removes_remaining_device_assignment():
    """A retry derives device cleanup from current interface state after a partial delete."""
    device_id = "22220000-0000-0000-0000-000000000001"
    interface_id = "33330000-0000-0000-0000-000000000001"
    other_interface_id = "33330000-0000-0000-0000-000000000002"
    old_overlay_id = "44440000-0000-0000-0000-000000000001"
    interface_assignment_id = "66660000-0000-0000-0000-000000000001"
    device_assignment_id = "77770000-0000-0000-0000-000000000001"
    device_assignment = {
        "id": device_assignment_id,
        "overlay": {
            "id": old_overlay_id,
            "isolation_type": "spectrum_x_vrf",
        },
    }
    interface_assignment = {
        "id": interface_assignment_id,
        "overlay": {
            "id": old_overlay_id,
            "isolation_type": "spectrum_x_vrf",
        },
    }
    activity_input = ReconcileSpXOverlayAssignmentsInput(
        overlay_id=None,
        site=LOCATION_ID,
        device_id=device_id,
        interface_ids=[interface_id],
        device_interface_ids=[interface_id, other_interface_id],
    )

    with aioresponses() as m:
        for assignments in (
            [device_assignment],  # Attempt 1: device assignments.
            [interface_assignment],  # Attempt 1: selected interface assignments.
            [],  # Attempt 1: other device interface assignments.
            [device_assignment],  # Attempt 2: device assignments.
            [],  # Attempt 2: selected interface after its assignment was deleted.
            [],  # Attempt 2: other device interface assignments.
        ):
            m.get(
                _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
                payload={"results": assignments},
            )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{interface_assignment_id}/",
            status=204,
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{device_assignment_id}/",
            status=500,
            payload={"detail": "temporary failure"},
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{device_assignment_id}/",
            status=204,
        )

        with pytest.raises(ApplicationError, match="temporary failure"):
            await reconcile_spx_overlay_assignments(activity_input)

        result = await reconcile_spx_overlay_assignments(activity_input)

    assert result.created == 0
    assert result.removed == 1


@pytest.mark.asyncio
async def test_reconcile_spx_overlay_assignments_retry_retains_completed_change_signal():
    """A retry reports a prior mutation even when its final attempt is a no-op."""
    device_id = "22220000-0000-0000-0000-000000000001"
    interface_id = "33330000-0000-0000-0000-000000000001"
    other_interface_id = "33330000-0000-0000-0000-000000000002"
    old_overlay_id = "44440000-0000-0000-0000-000000000001"
    interface_assignment_id = "66660000-0000-0000-0000-000000000001"
    activity_input = ReconcileSpXOverlayAssignmentsInput(
        overlay_id=None,
        site=LOCATION_ID,
        device_id=device_id,
        interface_ids=[interface_id],
        device_interface_ids=[interface_id, other_interface_id],
    )

    with aioresponses() as m:
        for assignments in (
            [],  # Attempt 1: device assignments.
            [
                {
                    "id": interface_assignment_id,
                    "overlay": {
                        "id": old_overlay_id,
                        "isolation_type": "spectrum_x_vrf",
                    },
                }
            ],  # Attempt 1: selected interface assignments.
        ):
            m.get(
                _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
                payload={"results": assignments},
            )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{interface_assignment_id}/",
            status=204,
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            status=500,
            payload={"detail": "temporary failure"},
        )
        for _ in range(3):  # Attempt 2: device, selected, and other interface reads.
            m.get(
                _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
                payload={"results": []},
            )

        with pytest.raises(ApplicationError, match="temporary failure"):
            await reconcile_spx_overlay_assignments(activity_input)

        with patch(
            "nv_config_manager.temporal.ngc.activities.nautobot.activity.info"
        ) as mock_activity_info:
            mock_activity_info.return_value.attempt = 2
            result = await reconcile_spx_overlay_assignments(activity_input)

    assert result.created == 0
    assert result.removed == 0
    assert result.reconciliation_changed is True


@pytest.mark.asyncio
async def test_remove_unmapped_device_vrfs_removes_only_unused_associations():
    device_id = "22220000-0000-0000-0000-000000000001"
    mapped_vrf_id = "eeee0000-0000-0000-0000-000000000001"
    unused_vrf_id = "eeee0000-0000-0000-0000-000000000002"
    assignment_id = "88880000-0000-0000-0000-000000000001"

    with aioresponses() as m:
        m.post(
            f"{NAUTOBOT}/api/graphql/",
            payload={
                "data": {
                    "interfaces": [
                        {
                            "id": "33330000-0000-0000-0000-000000000001",
                            "device": {"name": "leaf-1"},
                            "mac_address": None,
                            "name": "swp1",
                            "ip_addresses": [],
                            "vrf": {"id": mapped_vrf_id, "name": "mapped"},
                        }
                    ]
                }
            },
        )
        m.get(
            _r(f"{NAUTOBOT}/api/ipam/vrf-device-assignments/"),
            payload={
                "results": [
                    {
                        "id": assignment_id,
                        "device": {"id": device_id},
                        "vrf": {"id": unused_vrf_id},
                    }
                ]
            },
        )
        m.delete(
            f"{NAUTOBOT}/api/ipam/vrf-device-assignments/{assignment_id}/",
            status=204,
        )

        result = await remove_unmapped_device_vrfs(
            RemoveUnmappedDeviceVrfsInput(
                device_id=device_id,
                vrf_ids=[mapped_vrf_id, unused_vrf_id],
            )
        )

    assert result.removed_vrf_ids == [unused_vrf_id]


@pytest.mark.asyncio
async def test_remove_unmapped_device_vrfs_retry_retains_prior_removed_ids():
    """A retry reports every requested unmapped VRF after a partial failure."""
    device_id = "22220000-0000-0000-0000-000000000001"
    first_vrf_id = "eeee0000-0000-0000-0000-000000000001"
    second_vrf_id = "eeee0000-0000-0000-0000-000000000002"
    first_assignment_id = "88880000-0000-0000-0000-000000000001"
    second_assignment_id = "88880000-0000-0000-0000-000000000002"
    empty_interfaces = {"data": {"interfaces": []}}
    activity_input = RemoveUnmappedDeviceVrfsInput(
        device_id=device_id,
        vrf_ids=[first_vrf_id, second_vrf_id],
    )

    with aioresponses() as m:
        m.post(f"{NAUTOBOT}/api/graphql/", payload=empty_interfaces)
        m.post(f"{NAUTOBOT}/api/graphql/", payload=empty_interfaces)
        for assignments in (
            [
                {
                    "id": first_assignment_id,
                    "device": {"id": device_id},
                    "vrf": {"id": first_vrf_id},
                }
            ],  # Attempt 1: first VRF assignment.
            [
                {
                    "id": second_assignment_id,
                    "device": {"id": device_id},
                    "vrf": {"id": second_vrf_id},
                }
            ],  # Attempt 1: second VRF assignment.
            [],  # Attempt 2: first VRF after its assignment was deleted.
            [
                {
                    "id": second_assignment_id,
                    "device": {"id": device_id},
                    "vrf": {"id": second_vrf_id},
                }
            ],  # Attempt 2: second VRF assignment.
        ):
            m.get(
                _r(f"{NAUTOBOT}/api/ipam/vrf-device-assignments/"),
                payload={"results": assignments},
            )
        m.delete(
            f"{NAUTOBOT}/api/ipam/vrf-device-assignments/{first_assignment_id}/",
            status=204,
        )
        m.delete(
            f"{NAUTOBOT}/api/ipam/vrf-device-assignments/{second_assignment_id}/",
            status=500,
            payload={"detail": "temporary failure"},
        )
        m.delete(
            f"{NAUTOBOT}/api/ipam/vrf-device-assignments/{second_assignment_id}/",
            status=204,
        )

        with pytest.raises(ApplicationError, match="temporary failure"):
            await remove_unmapped_device_vrfs(activity_input)

        result = await remove_unmapped_device_vrfs(activity_input)

    assert result.removed_vrf_ids == [first_vrf_id, second_vrf_id]


# ---------------------------------------------------------------------------
# _vni_from_rd
# ---------------------------------------------------------------------------


def test_vni_from_rd_valid():
    assert _vni_from_rd("*:60004") == 60004


def test_vni_from_rd_missing_colon():
    with pytest.raises(ValueError, match="Invalid route distinguisher"):
        _vni_from_rd("60004")


def test_vni_from_rd_non_numeric():
    with pytest.raises(ValueError, match="Invalid route distinguisher"):
        _vni_from_rd("*:abc")


def test_vni_from_rd_extra_colons():
    with pytest.raises(ValueError, match="Invalid route distinguisher"):
        _vni_from_rd("1:2:3")


# ---------------------------------------------------------------------------
# get_available_route_distinguishers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_available_route_distinguishers_returns_namespace_ids():
    with aioresponses() as m:
        m.post(
            f"{NAUTOBOT}/api/graphql/",
            payload=_namespace_graphql_response("*:60000"),
        )

        result = await get_available_route_distinguishers(
            GetAvailableRouteDistinguishersInput(
                site=LOCATION_ID,
                namespace_tag="spectrumx",
                rd_min=60000,
                rd_max=65000,
            )
        )

    assert result.namespaces == [NS_ID]
    assert result.route_distinguisher == "*:60001"


@pytest.mark.asyncio
async def test_get_available_route_distinguishers_reuses_gap_below_max():
    with aioresponses() as m:
        m.post(
            f"{NAUTOBOT}/api/graphql/",
            payload=_namespace_graphql_response("*:60000", "*:60002", "*:65000"),
        )

        result = await get_available_route_distinguishers(
            GetAvailableRouteDistinguishersInput(
                site=LOCATION_ID,
                namespace_tag="tenant-a",
                rd_min=60000,
                rd_max=65000,
            )
        )

    assert result.route_distinguisher == "*:60001"
    assert result.namespaces == [NS_ID]


@pytest.mark.asyncio
async def test_get_available_route_distinguishers_raises_when_range_full():
    with aioresponses() as m:
        m.post(
            f"{NAUTOBOT}/api/graphql/",
            payload=_namespace_graphql_response("*:60000", "*:60001", "*:60002"),
        )

        with pytest.raises(ApplicationError, match="out of space for new RDs"):
            await get_available_route_distinguishers(
                GetAvailableRouteDistinguishersInput(
                    site=LOCATION_ID,
                    namespace_tag="tenant-a",
                    rd_min=60000,
                    rd_max=60002,
                )
            )


# ---------------------------------------------------------------------------
# provision_vrf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_vrf_creates_overlay_vrf_assignment_and_vxlan():
    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/extras/statuses/"), payload=_lookup(STATUS_ID))
        m.get(_r(f"{NAUTOBOT}/api/tenancy/tenants/"), payload=_lookup(TENANT_ID))
        m.get(_r(f"{OVERLAYS_BASE}/overlays/"), payload={"results": []})
        m.post(f"{OVERLAYS_BASE}/overlays/", payload={"id": OVERLAY_ID})
        m.post(f"{NAUTOBOT}/api/ipam/vrfs/", payload={"id": VRF_ID})
        m.post(
            f"{OVERLAYS_BASE}/overlay-assignments/",
            payload={"id": ASSIGNMENT_ID},
        )
        m.post(f"{OVERLAYS_BASE}/vxlans/", payload={"id": VXLAN_ID})

        await provision_vrf(
            ProvisionVrfInput(
                namespaces=[NS_ID],
                route_distinguisher="*:60004",
                overlay_id="test-overlay-001",
                site=LOCATION_ID,
                tenant="Public Demo",
            )
        )

        assert _request_json(m, "post", f"{NAUTOBOT}/api/ipam/vrfs/") == {
            "name": "SpXTenant60004",
            "rd": "*:60004",
            "namespace": NS_ID,
            "tenant": TENANT_ID,
        }
        assert _request_json(m, "post", f"{OVERLAYS_BASE}/overlay-assignments/") == {
            "overlay": OVERLAY_ID,
            "assigned_object_type": "ipam.vrf",
            "assigned_object_id": VRF_ID,
            "status": STATUS_ID,
        }


@pytest.mark.asyncio
async def test_provision_vrf_reuses_existing_overlay():
    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/extras/statuses/"), payload=_lookup(STATUS_ID))
        m.get(_r(f"{NAUTOBOT}/api/tenancy/tenants/"), payload=_lookup(TENANT_ID))
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={"results": [{"id": OVERLAY_ID, "tenant": {"id": TENANT_ID}}]},
        )
        # No create_overlay call — overlay already exists
        m.post(f"{NAUTOBOT}/api/ipam/vrfs/", payload={"id": VRF_ID})
        m.post(
            f"{OVERLAYS_BASE}/overlay-assignments/",
            payload={"id": ASSIGNMENT_ID},
        )
        m.post(f"{OVERLAYS_BASE}/vxlans/", payload={"id": VXLAN_ID})

        await provision_vrf(
            ProvisionVrfInput(
                namespaces=[NS_ID],
                route_distinguisher="*:60004",
                overlay_id="test-overlay-001",
                site=LOCATION_ID,
                tenant="Public Demo",
            )
        )


@pytest.mark.asyncio
async def test_provision_vrf_rolls_back_on_vxlan_failure():
    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/extras/statuses/"), payload=_lookup(STATUS_ID))
        m.get(_r(f"{NAUTOBOT}/api/tenancy/tenants/"), payload=_lookup(TENANT_ID))
        m.get(_r(f"{OVERLAYS_BASE}/overlays/"), payload={"results": []})
        m.post(f"{OVERLAYS_BASE}/overlays/", payload={"id": OVERLAY_ID})
        m.post(f"{NAUTOBOT}/api/ipam/vrfs/", payload={"id": VRF_ID})
        m.post(
            f"{OVERLAYS_BASE}/overlay-assignments/",
            payload={"id": ASSIGNMENT_ID},
        )
        # vxlan creation fails
        m.post(f"{OVERLAYS_BASE}/vxlans/", status=400, payload={"detail": "bad"})
        # rollback: delete assignment and VRF (no VXLANs were created)
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{ASSIGNMENT_ID}/",
            status=204,
        )
        m.delete(f"{NAUTOBOT}/api/ipam/vrfs/{VRF_ID}/", status=204)

        with pytest.raises(ApplicationError, match="Failed to provision VPC"):
            await provision_vrf(
                ProvisionVrfInput(
                    namespaces=[NS_ID],
                    route_distinguisher="*:60004",
                    overlay_id="test-overlay-001",
                    site=LOCATION_ID,
                    tenant="Public Demo",
                )
            )


@pytest.mark.asyncio
async def test_provision_vrf_missing_status_raises():
    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/extras/statuses/"), payload={"results": []})

        with pytest.raises(ApplicationError, match="Status 'Active' not found"):
            await provision_vrf(
                ProvisionVrfInput(
                    namespaces=[NS_ID],
                    route_distinguisher="*:60004",
                    overlay_id="test-overlay-001",
                    site=LOCATION_ID,
                    tenant="Public Demo",
                )
            )


@pytest.mark.asyncio
async def test_provision_vrf_missing_tenant_raises():
    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/extras/statuses/"), payload=_lookup(STATUS_ID))
        m.get(_r(f"{NAUTOBOT}/api/tenancy/tenants/"), payload={"results": []})

        with pytest.raises(ApplicationError, match="Tenant 'Public Demo' not found"):
            await provision_vrf(
                ProvisionVrfInput(
                    namespaces=[NS_ID],
                    route_distinguisher="*:60004",
                    overlay_id="test-overlay-001",
                    site=LOCATION_ID,
                    tenant="Public Demo",
                )
            )


# ---------------------------------------------------------------------------
# delete_vrf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_vrf_deletes_vxlan_assignment_then_vrf():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/vxlans/"),
            payload={"results": [{"id": VXLAN_ID, "vrf": {"id": VRF_ID}}]},
        )
        m.delete(f"{OVERLAYS_BASE}/vxlans/{VXLAN_ID}/", status=204)
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={"results": [{"id": ASSIGNMENT_ID}]},
        )
        m.delete(
            f"{OVERLAYS_BASE}/overlay-assignments/{ASSIGNMENT_ID}/",
            status=204,
        )
        m.delete(f"{NAUTOBOT}/api/ipam/vrfs/{VRF_ID}/", status=204)

        await delete_vrf(VrfDeletionActivityInput(vrf_id=VRF_ID, vnid=60004))


@pytest.mark.asyncio
async def test_delete_vrf_skips_vxlan_bound_to_different_vrf():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/vxlans/"),
            payload={"results": [{"id": VXLAN_ID, "vrf": {"id": "other-vrf-id"}}]},
        )
        # No VXLAN delete — vrf_id doesn't match
        m.get(
            _r(f"{OVERLAYS_BASE}/overlay-assignments/"),
            payload={"results": []},
        )
        m.delete(f"{NAUTOBOT}/api/ipam/vrfs/{VRF_ID}/", status=204)

        await delete_vrf(VrfDeletionActivityInput(vrf_id=VRF_ID, vnid=60004))


# ---------------------------------------------------------------------------
# delete_overlay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_overlay_deletes_when_no_members():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={"results": [{"id": OVERLAY_ID, "name": "test-overlay-001"}]},
        )
        m.get(_r(f"{OVERLAYS_BASE}/vxlans/"), payload={"results": []})
        m.delete(f"{OVERLAYS_BASE}/overlays/{OVERLAY_ID}/", status=204)

        result = await delete_overlay(
            DeleteOverlayInput(overlay_id="test-overlay-001", site=LOCATION_ID)
        )

    assert result.deleted is True
    assert result.overlay_name == "test-overlay-001"


@pytest.mark.asyncio
async def test_delete_overlay_skips_when_vxlans_remain():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={"results": [{"id": OVERLAY_ID, "name": "test-overlay-001"}]},
        )
        m.get(_r(f"{OVERLAYS_BASE}/vxlans/"), payload={"results": [{"id": VXLAN_ID}]})

        result = await delete_overlay(
            DeleteOverlayInput(overlay_id="test-overlay-001", site=LOCATION_ID)
        )

    assert result.deleted is False


@pytest.mark.asyncio
async def test_delete_overlay_cascades_assignments_when_no_vxlans_remain():
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={"results": [{"id": OVERLAY_ID, "name": "test-overlay-001"}]},
        )
        m.get(_r(f"{OVERLAYS_BASE}/vxlans/"), payload={"results": []})
        m.delete(f"{OVERLAYS_BASE}/overlays/{OVERLAY_ID}/", status=204)

        result = await delete_overlay(
            DeleteOverlayInput(overlay_id="test-overlay-001", site=LOCATION_ID)
        )

    assert result.deleted is True


@pytest.mark.asyncio
async def test_delete_overlay_not_found_returns_not_deleted():
    with aioresponses() as m:
        m.get(_r(f"{OVERLAYS_BASE}/overlays/"), payload={"results": []})

        result = await delete_overlay(
            DeleteOverlayInput(overlay_id="test-overlay-001", site=LOCATION_ID)
        )

    assert result.deleted is False
    assert result.overlay_name == "test-overlay-001"


# ---------------------------------------------------------------------------
# NautobotClient.lookup_id_by_name ambiguity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_id_by_name_raises_on_multiple_results():

    with aioresponses() as m:
        m.get(
            _r(f"{NAUTOBOT}/api/dcim/locations/"),
            payload={"results": [{"id": "id-1"}, {"id": "id-2"}]},
        )

        client = NautobotClient()
        async with client:
            with pytest.raises(NautobotException, match="Ambiguous name"):
                await client.lookup_id_by_name("dcim/locations/", "SPO01")


@pytest.mark.asyncio
async def test_lookup_id_by_name_returns_none_when_not_found():

    with aioresponses() as m:
        m.get(_r(f"{NAUTOBOT}/api/dcim/locations/"), payload={"results": []})

        client = NautobotClient()
        async with client:
            result = await client.lookup_id_by_name("dcim/locations/", "nonexistent")

    assert result is None


# ---------------------------------------------------------------------------
# NautobotClient.find_overlay ambiguity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_overlay_raises_on_multiple_results():

    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/overlays/"),
            payload={"results": [{"id": "id-1"}, {"id": "id-2"}]},
        )

        client = NautobotClient()
        async with client:
            with pytest.raises(NautobotException, match="Ambiguous overlay"):
                await client.find_overlay("test-overlay-001", LOCATION_ID)


# ---------------------------------------------------------------------------
# NautobotClient pagination (get_all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vxlans_by_overlay_follows_pagination():
    """All VXLAN pages are collected, not just the first."""
    with aioresponses() as m:
        m.get(
            _r(f"{OVERLAYS_BASE}/vxlans/"),
            payload={"next": f"{OVERLAYS_BASE}/vxlans/?offset=1", "results": [{"id": "v1"}]},
        )
        m.get(
            _r(f"{OVERLAYS_BASE}/vxlans/"),
            payload={"next": None, "results": [{"id": "v2"}]},
        )

        client = NautobotClient()
        async with client:
            result = await client.get_vxlans_by_overlay(OVERLAY_ID)

    assert [v["id"] for v in result] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_lookup_id_by_name_detects_ambiguity_across_pages():
    """Ambiguity is detected from the server count even when results are paginated."""
    with aioresponses() as m:
        m.get(
            _r(f"{NAUTOBOT}/api/dcim/locations/"),
            payload={
                "count": 2,
                "next": f"{NAUTOBOT}/api/dcim/locations/?offset=1",
                "results": [{"id": "id-1"}],
            },
        )

        client = NautobotClient()
        async with client:
            with pytest.raises(NautobotException, match="Ambiguous name"):
                await client.lookup_id_by_name("dcim/locations/", "SPO01")
