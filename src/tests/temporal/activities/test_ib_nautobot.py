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
"""Tests for InfiniBand Nautobot overlay management activities."""

import re
from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    CleanupEmptyPartitionInput,
    CreatePartitionInNautobotInput,
    ResolveGuidsToInterfacesInput,
    _is_auto_created_overlay_name,
    cleanup_empty_pkey_partition,
    create_partition_in_nautobot,
    resolve_guids_to_interfaces,
)

NB_URL = "https://nautobot.example.com"
NB_API = f"{NB_URL}/api"
PLUGIN = f"{NB_API}/plugins/overlays"

LOCATION_UUID = "aaa-111"
TENANT_UUID = "bbb-222"
STATUS_UUID = "ccc-333"
OVERLAY_UUID = "ddd-444"
PKEY_UUID = "eee-555"

_NB_LOCATIONS = re.compile(rf"{re.escape(NB_API)}/dcim/locations/.*")
_NB_TENANTS = re.compile(rf"{re.escape(NB_API)}/tenancy/tenants/.*")
_NB_STATUSES = re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*")
_NB_OVERLAYS = re.compile(rf"{re.escape(PLUGIN)}/overlays/.*")
_NB_PKEYS = re.compile(rf"{re.escape(PLUGIN)}/pkeys/.*")
_NB_ASSIGNMENTS = re.compile(rf"{re.escape(PLUGIN)}/overlay-assignments/.*")


def _nb_config() -> ConfigParser:
    config = ConfigParser()
    config.add_section("dcim")
    config.set("dcim", "provider", "nautobot-2x")
    config.set("dcim", "server", NB_URL)
    config.set("dcim", "token", "test-token")
    config.set("dcim", "verify", "false")
    config.add_section("nats")
    return config


@pytest.fixture(autouse=True)
def mock_nb_config():
    with patch("nv_config_manager.common.config.load_config") as mock:
        mock.return_value = _nb_config()
        yield mock


def _stub_lookups(m: aioresponses, *, with_tenant: bool = False) -> None:
    m.get(_NB_LOCATIONS, payload={"results": [{"id": LOCATION_UUID, "name": "UFM Lab"}]})
    if with_tenant:
        m.get(_NB_TENANTS, payload={"results": [{"id": TENANT_UUID, "name": "tenant-a"}]})
    m.get(_NB_STATUSES, payload={"results": [{"id": STATUS_UUID, "name": "Active"}]})


class TestCreatePartitionInNautobot:
    @pytest.mark.asyncio
    async def test_creates_overlay_and_pkey(self, mock_nb_config):
        with aioresponses() as m:
            _stub_lookups(m)
            m.get(_NB_OVERLAYS, payload={"results": []})
            m.post(f"{PLUGIN}/overlays/", payload={"id": OVERLAY_UUID, "name": "ib-pkey-0x0005"})
            m.get(_NB_PKEYS, payload={"results": []})
            m.post(f"{PLUGIN}/pkeys/", payload={"id": PKEY_UUID, "pkey": "0x0005"})

            result = await create_partition_in_nautobot(
                CreatePartitionInNautobotInput(pkey="0x0005", location_name="UFM Lab")
            )

            assert result.partition_id == OVERLAY_UUID
            assert result.partition_name == "ib-pkey-0x0005"
            assert result.pkey_id == PKEY_UUID
            assert result.pkey == "0x0005"

    @pytest.mark.asyncio
    async def test_custom_partition_name(self, mock_nb_config):
        with aioresponses() as m:
            _stub_lookups(m)
            m.get(_NB_OVERLAYS, payload={"results": []})
            m.post(f"{PLUGIN}/overlays/", payload={"id": OVERLAY_UUID, "name": "my-vpc"})
            m.get(_NB_PKEYS, payload={"results": []})
            m.post(f"{PLUGIN}/pkeys/", payload={"id": PKEY_UUID, "pkey": "0x0005"})

            result = await create_partition_in_nautobot(
                CreatePartitionInNautobotInput(
                    pkey="0x0005",
                    partition_name="my-vpc",
                    location_name="UFM Lab",
                )
            )

            assert result.partition_name == "my-vpc"

    @pytest.mark.asyncio
    async def test_with_tenant(self, mock_nb_config):
        with aioresponses() as m:
            _stub_lookups(m, with_tenant=True)
            m.get(_NB_OVERLAYS, payload={"results": []})
            m.post(f"{PLUGIN}/overlays/", payload={"id": OVERLAY_UUID, "name": "ib-pkey-0x0005"})
            m.get(_NB_PKEYS, payload={"results": []})
            m.post(f"{PLUGIN}/pkeys/", payload={"id": PKEY_UUID, "pkey": "0x0005"})

            result = await create_partition_in_nautobot(
                CreatePartitionInNautobotInput(
                    pkey="0x0005",
                    location_name="UFM Lab",
                    tenant_name="tenant-a",
                )
            )

            assert result.partition_id == OVERLAY_UUID

    @pytest.mark.asyncio
    async def test_idempotent_existing_overlay_and_pkey(self, mock_nb_config):
        """Reuses existing objects instead of creating duplicates."""
        with aioresponses() as m:
            _stub_lookups(m)
            m.get(
                _NB_OVERLAYS, payload={"results": [{"id": OVERLAY_UUID, "name": "ib-pkey-0x0005"}]}
            )
            m.get(_NB_PKEYS, payload={"results": [{"id": PKEY_UUID, "pkey": "0x0005"}]})

            result = await create_partition_in_nautobot(
                CreatePartitionInNautobotInput(pkey="0x0005", location_name="UFM Lab")
            )

            assert result.partition_id == OVERLAY_UUID
            assert result.pkey_id == PKEY_UUID

    @pytest.mark.asyncio
    async def test_location_not_found(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_LOCATIONS, payload={"results": []})

            with pytest.raises(ApplicationError, match="Location.*not found"):
                await create_partition_in_nautobot(
                    CreatePartitionInNautobotInput(pkey="0x0005", location_name="nonexistent")
                )

    @pytest.mark.asyncio
    async def test_tenant_not_found(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_LOCATIONS, payload={"results": [{"id": LOCATION_UUID, "name": "UFM Lab"}]})
            m.get(_NB_TENANTS, payload={"results": []})

            with pytest.raises(ApplicationError, match="Tenant.*not found"):
                await create_partition_in_nautobot(
                    CreatePartitionInNautobotInput(
                        pkey="0x0005",
                        location_name="UFM Lab",
                        tenant_name="ghost-tenant",
                    )
                )

    @pytest.mark.asyncio
    async def test_status_not_found(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_LOCATIONS, payload={"results": [{"id": LOCATION_UUID, "name": "UFM Lab"}]})
            m.get(_NB_STATUSES, payload={"results": []})

            with pytest.raises(ApplicationError, match="Status.*not found"):
                await create_partition_in_nautobot(
                    CreatePartitionInNautobotInput(pkey="0x0005", location_name="UFM Lab")
                )

    @pytest.mark.asyncio
    async def test_idempotent_retry_picks_up_existing_overlay(self, mock_nb_config):
        """On retry, existing overlay is reused and only PKey is created."""
        with aioresponses() as m:
            _stub_lookups(m)
            m.get(
                _NB_OVERLAYS, payload={"results": [{"id": OVERLAY_UUID, "name": "ib-pkey-0x0005"}]}
            )
            m.get(_NB_PKEYS, payload={"results": []})
            m.post(f"{PLUGIN}/pkeys/", payload={"id": PKEY_UUID, "pkey": "0x0005"})

            result = await create_partition_in_nautobot(
                CreatePartitionInNautobotInput(pkey="0x0005", location_name="UFM Lab")
            )

            assert result.partition_id == OVERLAY_UUID
            assert result.pkey_id == PKEY_UUID


_NB_GRAPHQL = f"{NB_API}/graphql/"


def _graphql_payload(interfaces: list[dict]) -> dict:
    return {"data": {"interfaces": interfaces}}


class TestResolveGuidsToInterfaces:
    """Reverse-lookup of IB GUIDs to Nautobot interface records."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_resolved(self, mock_nb_config):
        result = await resolve_guids_to_interfaces(ResolveGuidsToInterfacesInput(guids=[]))
        assert result.resolved == []

    @pytest.mark.asyncio
    async def test_resolves_each_guid_to_interface(self, mock_nb_config):
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002c903000e0b72",
                            "device": {"name": "hca01"},
                        },
                        {
                            "id": "iface-2",
                            "name": "mlx5_1",
                            "cf_ib_guid": "0002c903000e0b73",
                            "device": {"name": "hca01"},
                        },
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(
                    guids=["0002c903000e0b72", "0002c903000e0b73"],
                )
            )

            guids = sorted(r.guid for r in result.resolved)
            assert guids == ["0002c903000e0b72", "0002c903000e0b73"]
            iface_ids = sorted(r.interface_id for r in result.resolved)
            assert iface_ids == ["iface-1", "iface-2"]

    @pytest.mark.asyncio
    async def test_per_guid_membership_applied(self, mock_nb_config):
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002c903000e0b72",
                            "device": {"name": "hca01"},
                        },
                        {
                            "id": "iface-2",
                            "name": "mlx5_1",
                            "cf_ib_guid": "0002c903000e0b73",
                            "device": {"name": "hca01"},
                        },
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(
                    guids=["0002c903000e0b72", "0002c903000e0b73"],
                    guid_memberships=["limited", "full"],
                )
            )

            by_guid = {r.guid: r.membership for r in result.resolved}
            assert by_guid == {
                "0002c903000e0b72": "limited",
                "0002c903000e0b73": "full",
            }

    @pytest.mark.asyncio
    async def test_misaligned_guid_memberships_raises(self, mock_nb_config):
        with pytest.raises(ApplicationError, match="guid_memberships length"):
            await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(
                    guids=["0002c903000e0b72", "0002c903000e0b73"],
                    guid_memberships=["limited"],
                )
            )

    @pytest.mark.asyncio
    async def test_dedupes_input_guids(self, mock_nb_config):
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002c903000e0b72",
                            "device": {"name": "hca01"},
                        }
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(
                    guids=["0002c903000e0b72", "0002c903000e0b72"],
                )
            )

            assert len(result.resolved) == 1

    @pytest.mark.asyncio
    async def test_missing_guid_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.post(_NB_GRAPHQL, payload=_graphql_payload([]))

            with pytest.raises(ApplicationError, match="No DCIM interface found"):
                await resolve_guids_to_interfaces(
                    ResolveGuidsToInterfacesInput(guids=["0002c903000e0b72"])
                )

    @pytest.mark.asyncio
    async def test_duplicate_match_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002c903000e0b72",
                            "device": {"name": "hca01"},
                        },
                        {
                            "id": "iface-2",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002c903000e0b72",
                            "device": {"name": "hca02"},
                        },
                    ]
                ),
            )

            with pytest.raises(ApplicationError, match="matched multiple"):
                await resolve_guids_to_interfaces(
                    ResolveGuidsToInterfacesInput(guids=["0002c903000e0b72"])
                )

    @pytest.mark.asyncio
    async def test_case_insensitive_guid_match(self, mock_nb_config):
        """Nautobot's __ie filter is case-insensitive; activity normalizes."""
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "mlx5_0",
                            "cf_ib_guid": "0002C903000E0B72",
                            "device": {"name": "hca01"},
                        }
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(guids=["0002c903000e0b72"])
            )

            assert len(result.resolved) == 1
            assert result.resolved[0].interface_id == "iface-1"

    @pytest.mark.asyncio
    async def test_normalizes_0x_prefixed_input_guid(self, mock_nb_config):
        """A user-entered ``0x``-prefixed GUID resolves against bare-hex storage."""
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "HCA-7/1",
                            "cf_ib_guid": "946dae0300598000",
                            "device": {"name": "dgx-05"},
                        }
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(guids=["0x946dae0300598000"])
            )

            assert len(result.resolved) == 1
            assert result.resolved[0].interface_id == "iface-1"
            assert result.resolved[0].guid == "946dae0300598000"

            sent = next(iter(m.requests.values()))[0]
            assert sent.kwargs["json"]["variables"]["guids"] == ["946dae0300598000"]

    @pytest.mark.asyncio
    async def test_prefixed_and_bare_guid_dedupe(self, mock_nb_config):
        """``0x``-prefixed and bare forms of the same GUID collapse to one lookup."""
        with aioresponses() as m:
            m.post(
                _NB_GRAPHQL,
                payload=_graphql_payload(
                    [
                        {
                            "id": "iface-1",
                            "name": "HCA-7/1",
                            "cf_ib_guid": "946dae0300598000",
                            "device": {"name": "dgx-05"},
                        }
                    ]
                ),
            )

            result = await resolve_guids_to_interfaces(
                ResolveGuidsToInterfacesInput(
                    guids=["0x946dae0300598000", "946dae0300598000"],
                )
            )

            assert len(result.resolved) == 1
            sent = next(iter(m.requests.values()))[0]
            assert sent.kwargs["json"]["variables"]["guids"] == ["946dae0300598000"]

    @pytest.mark.asyncio
    async def test_all_empty_guids_raises(self, mock_nb_config):
        with pytest.raises(ApplicationError, match="All provided GUIDs were empty"):
            await resolve_guids_to_interfaces(ResolveGuidsToInterfacesInput(guids=["", ""]))


class TestIsAutoCreatedOverlayName:
    """Heuristic that flags overlays the delete workflow may remove."""

    def test_matches_auto_created_name(self):
        assert _is_auto_created_overlay_name("ib-pkey-overlay-0x8001", "0x8001") is True

    def test_rejects_operator_named_overlay(self):
        assert _is_auto_created_overlay_name("my-vpc", "0x8001") is False

    def test_rejects_mismatched_pkey(self):
        assert _is_auto_created_overlay_name("ib-pkey-overlay-0x8001", "0x8002") is False


class TestCleanupEmptyPkeyPartition:
    """Post-removal reconciliation of orphaned Nautobot PKey/Overlay records."""

    def _input(
        self,
        *,
        overlay_name: str = "ib-pkey-overlay-0x8001",
        ufm_partition_empty: bool = True,
    ) -> CleanupEmptyPartitionInput:
        return CleanupEmptyPartitionInput(
            overlay_id=OVERLAY_UUID,
            overlay_name=overlay_name,
            pkey_id=PKEY_UUID,
            pkey="0x8001",
            ufm_partition_empty=ufm_partition_empty,
        )

    @pytest.mark.asyncio
    async def test_non_empty_partition_keeps_records(self, mock_nb_config):
        """Members remain on the overlay, so nothing is deleted."""
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": [{"id": "assign-1"}]})

            result = await cleanup_empty_pkey_partition(self._input())

            assert result.partition_empty is False
            assert result.pkey_deleted is False
            assert result.overlay_deleted is False

    @pytest.mark.asyncio
    async def test_nautobot_empty_but_ufm_not_empty_keeps_records(self, mock_nb_config):
        """Nautobot has no assignments, but UFM still holds untracked members.

        The partition is live on UFM, so deleting the Nautobot PKey/Overlay would
        orphan it. Cleanup must leave both in place.
        """
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})

            result = await cleanup_empty_pkey_partition(self._input(ufm_partition_empty=False))

            assert result.partition_empty is False
            assert result.pkey_deleted is False
            assert result.overlay_deleted is False

    @pytest.mark.asyncio
    async def test_empty_auto_created_overlay_deletes_pkey_and_overlay(self, mock_nb_config):
        """Last member gone + auto-created overlay with no other PKeys: delete both."""
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.delete(_NB_PKEYS, status=204)
            m.get(_NB_PKEYS, payload={"results": []})
            m.delete(_NB_OVERLAYS, status=204)

            result = await cleanup_empty_pkey_partition(self._input())

            assert result.partition_empty is True
            assert result.pkey_deleted is True
            assert result.overlay_deleted is True

    @pytest.mark.asyncio
    async def test_empty_operator_overlay_deletes_pkey_only(self, mock_nb_config):
        """Operator-owned overlays are never deleted as a side effect."""
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.delete(_NB_PKEYS, status=204)

            result = await cleanup_empty_pkey_partition(self._input(overlay_name="my-vpc"))

            assert result.partition_empty is True
            assert result.pkey_deleted is True
            assert result.overlay_deleted is False

    @pytest.mark.asyncio
    async def test_empty_auto_created_overlay_with_other_pkeys_kept(self, mock_nb_config):
        """An auto-created overlay sharing other PKeys is kept after the PKey delete."""
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.delete(_NB_PKEYS, status=204)
            m.get(_NB_PKEYS, payload={"results": [{"id": "other-pkey"}]})

            result = await cleanup_empty_pkey_partition(self._input())

            assert result.partition_empty is True
            assert result.pkey_deleted is True
            assert result.overlay_deleted is False

    @pytest.mark.asyncio
    async def test_retry_tolerates_already_deleted_pkey_and_overlay(self, mock_nb_config):
        """A retry after partial cleanup: PKey/overlay already gone (404) still finishes.

        Mirrors the case where a prior attempt deleted the PKey but failed before
        completing overlay cleanup. The retry must treat the 404s as already-cleaned
        and still delete the auto-created overlay rather than aborting.
        """
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.delete(_NB_PKEYS, status=404)
            m.get(_NB_PKEYS, payload={"results": []})
            m.delete(_NB_OVERLAYS, status=404)

            result = await cleanup_empty_pkey_partition(self._input())

            assert result.partition_empty is True
            assert result.pkey_deleted is True
            assert result.overlay_deleted is True

    @pytest.mark.asyncio
    async def test_non_404_delete_error_still_raises(self, mock_nb_config):
        """Non-404 delete failures are not swallowed; the activity surfaces them."""
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.delete(_NB_PKEYS, status=500)

            with pytest.raises(ApplicationError):
                await cleanup_empty_pkey_partition(self._input())
