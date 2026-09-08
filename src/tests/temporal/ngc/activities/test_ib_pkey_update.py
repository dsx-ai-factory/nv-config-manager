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
"""Tests for new IB PKey update activities.

Covers:
- fetch_pkey_members
- remove_guids_from_pkey
- set_pkey_members
- fetch_pkey_assignments
- sync_pkey_assignments
"""

import re
from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import CallbackResult, aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.ufm import UFMClientError
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    FetchPKeyAssignmentsInput,
    ResolvedInterface,
    SyncPKeyAssignmentsInput,
    fetch_pkey_assignments,
    sync_pkey_assignments,
)
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    FetchPKeyMembersInput,
    RemoveGuidsInput,
    SetGuidsInput,
    fetch_pkey_members,
    remove_guids_from_pkey,
    set_pkey_members,
)

UFM_BASE = "https://ufm.example.com/ufmRest"
NB_URL = "https://nautobot.example.com"
NB_API = f"{NB_URL}/api"
PLUGIN = f"{NB_API}/plugins/overlays"

OVERLAY_UUID = "ddd-444"
STATUS_UUID = "ccc-333"
IFACE_UUID_1 = "iface-001"
IFACE_UUID_2 = "iface-002"
IFACE_UUID_3 = "iface-003"
ASSIGNMENT_UUID_1 = "asgn-001"
ASSIGNMENT_UUID_2 = "asgn-002"
ASSIGNMENT_UUID_3 = "asgn-003"

GUID_1 = "0002c903000e0b72"
GUID_2 = "0002c903000e0b73"
GUID_3 = "0002c903000e0b74"

_NB_STATUSES = re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*")
_NB_ASSIGNMENTS = re.compile(rf"{re.escape(PLUGIN)}/overlay-assignments/.*")


def _ufm_config() -> ConfigParser:
    config = ConfigParser()
    config.add_section("ufm")
    config.set("ufm", "ufm_api_user", "admin")
    config.set("ufm", "ufm_api_token_r1", "password")
    return config


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
def reset_secrets_cache():
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.fixture()
def mock_ufm_config():
    with patch("nv_config_manager.temporal.client.ufm.load_config", return_value=_ufm_config()):
        yield


@pytest.fixture()
def mock_nb_config():
    with patch("nv_config_manager.common.config.load_config", return_value=_nb_config()):
        yield


def _pkey_members_url(pkey: str) -> str:
    return f"{UFM_BASE}/resources/pkeys/{pkey}?guids_data=true"


# ---------------------------------------------------------------------------
# fetch_pkey_members
# ---------------------------------------------------------------------------


class TestFetchPKeyMembers:
    @pytest.mark.asyncio
    async def test_returns_current_guids(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(
                _pkey_members_url("0x0005"),
                payload={
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "full"},
                    ]
                },
            )

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.pkey == "0x0005"
        assert len(result.guids) == 2
        assert GUID_1 in result.guids
        assert GUID_2 in result.guids

    @pytest.mark.asyncio
    async def test_normalises_guid_case(self, mock_ufm_config):
        """GUIDs returned by UFM are normalised to lowercase."""
        with aioresponses() as m:
            m.get(
                _pkey_members_url("0x0005"),
                payload={"guids": [{"guid": GUID_1.upper(), "membership": "full"}]},
            )

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.guids == [GUID_1.lower()]

    @pytest.mark.asyncio
    async def test_empty_pkey_returns_empty_list(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_members_url("0x0005"), payload={"guids": []})

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.guids == []

    @pytest.mark.asyncio
    async def test_unexpected_response_raises(self, mock_ufm_config):
        """A non-dict UFM body (not a 404) is a hard error."""
        with aioresponses() as m:
            m.get(_pkey_members_url("0x0005"), payload=None)

            with pytest.raises(ApplicationError, match="unexpected response"):
                await fetch_pkey_members(
                    FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
                )

    @pytest.mark.asyncio
    async def test_pkey_404_reports_not_exists(self, mock_ufm_config):
        """A 404 is tolerated: the partition is reported as missing and empty."""
        with aioresponses() as m:
            m.get(_pkey_members_url("0x0005"), status=404)

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.exists is False
        assert result.guids == []
        assert result.memberships == []
        assert result.ip_over_ib is None

    @pytest.mark.asyncio
    async def test_non_404_error_propagates(self, mock_ufm_config):
        """Only 404 is tolerated; other UFM errors must surface, not read as empty."""
        with aioresponses() as m:
            m.get(_pkey_members_url("0x0005"), status=500, payload={"error": "boom"})

            with pytest.raises(UFMClientError):
                await fetch_pkey_members(
                    FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
                )

    @pytest.mark.asyncio
    async def test_returns_memberships_and_ip_over_ib(self, mock_ufm_config):
        """Membership per GUID and the partition ip_over_ib flag are surfaced."""
        with aioresponses() as m:
            m.get(
                _pkey_members_url("0x0005"),
                payload={
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "limited"},
                    ],
                    "ip_over_ib": False,
                },
            )

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.exists is True
        assert result.guids == [GUID_1, GUID_2]
        assert result.memberships == ["full", "limited"]
        assert result.ip_over_ib is False

    @pytest.mark.asyncio
    async def test_plain_string_guids_rejected(self, mock_ufm_config):
        """A guids_data read must carry a membership per member; bare strings are malformed."""
        with aioresponses() as m:
            m.get(
                _pkey_members_url("0x0005"),
                payload={"guids": [GUID_1, GUID_2]},
            )

            with pytest.raises(ApplicationError, match="no guid or membership"):
                await fetch_pkey_members(
                    FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
                )

    @pytest.mark.asyncio
    async def test_member_without_membership_rejected(self, mock_ufm_config):
        """A member entry lacking a membership fails loudly rather than defaulting a type."""
        with aioresponses() as m:
            m.get(
                _pkey_members_url("0x0005"),
                payload={"guids": [{"guid": GUID_1}]},
            )

            with pytest.raises(ApplicationError, match="no guid or membership"):
                await fetch_pkey_members(
                    FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
                )

    @pytest.mark.asyncio
    async def test_null_guids_treated_as_empty(self, mock_ufm_config):
        """A null `guids` value is treated as an empty member list, not a crash."""
        with aioresponses() as m:
            m.get(_pkey_members_url("0x0005"), payload={"guids": None})

            result = await fetch_pkey_members(
                FetchPKeyMembersInput(host="ufm.example.com", pkey="0x0005")
            )

        assert result.exists is True
        assert result.guids == []
        assert result.memberships == []


# ---------------------------------------------------------------------------
# remove_guids_from_pkey
# ---------------------------------------------------------------------------


class TestRemoveGuidsFromPKey:
    @pytest.mark.asyncio
    async def test_removes_guids_successfully(self, mock_ufm_config):
        guids_csv = f"{GUID_1},{GUID_2}"
        with aioresponses() as m:
            m.delete(f"{UFM_BASE}/resources/pkeys/0x0005/guids/{guids_csv}", payload={})

            result = await remove_guids_from_pkey(
                RemoveGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1, GUID_2],
                )
            )

        assert result.pkey == "0x0005"
        assert result.guids_removed == [GUID_1, GUID_2]

    @pytest.mark.asyncio
    async def test_empty_guid_list_is_noop(self, mock_ufm_config):
        """No HTTP call should be made when the GUID list is empty."""
        with aioresponses() as m:
            result = await remove_guids_from_pkey(
                RemoveGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[],
                )
            )

            assert result.guids_removed == []
            assert len(m.requests) == 0

    @pytest.mark.asyncio
    async def test_ufm_error_raises(self, mock_ufm_config):
        with aioresponses() as m:
            m.delete(
                f"{UFM_BASE}/resources/pkeys/0x0005/guids/{GUID_1}",
                status=400,
                payload={"error": "bad request"},
            )

            with pytest.raises(Exception):
                await remove_guids_from_pkey(
                    RemoveGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1],
                    )
                )


# ---------------------------------------------------------------------------
# set_pkey_members
# ---------------------------------------------------------------------------


class TestSetPKeyMembers:
    @pytest.mark.asyncio
    async def test_issues_single_put_with_full_desired_set(self, mock_ufm_config):
        """Membership is replaced via one PUT carrying the entire desired set."""
        with aioresponses() as m:
            m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

            result = await set_pkey_members(
                SetGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1, GUID_2],
                    memberships=["full", "full"],
                )
            )

            requests = [(method, str(url)) for (method, url) in m.requests]

        assert result.pkey == "0x0005"
        assert result.guids_set == [GUID_1, GUID_2]
        assert result.memberships_set == ["full", "full"]
        # Exactly one mutation, and it is a PUT (atomic overwrite).
        assert requests == [("PUT", f"{UFM_BASE}/resources/pkeys/")]

    @pytest.mark.asyncio
    async def test_put_body_carries_per_guid_memberships(self, mock_ufm_config):
        """The PUT payload carries the pkey, guids, and index-aligned memberships."""
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            await set_pkey_members(
                SetGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1, GUID_2],
                    memberships=["full", "limited"],
                )
            )

        assert captured["pkey"] == "0x0005"
        assert captured["guids"] == [GUID_1, GUID_2]
        # UFM's Set endpoint (PUT) honors the index-aligned plural `memberships`.
        assert captured["memberships"] == ["full", "limited"]
        assert "membership" not in captured

    @pytest.mark.asyncio
    async def test_misaligned_memberships_rejected_without_http(self, mock_ufm_config):
        """memberships must be index-aligned with guids."""
        with aioresponses() as m:
            with pytest.raises(ApplicationError):
                await set_pkey_members(
                    SetGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1, GUID_2],
                        memberships=["full"],
                    )
                )

            assert len(m.requests) == 0

    @pytest.mark.asyncio
    async def test_empty_guid_set_rejected_without_http(self, mock_ufm_config):
        """Emptying a partition is the delete workflow's job; reject here."""
        with aioresponses() as m:
            with pytest.raises(ApplicationError):
                await set_pkey_members(
                    SetGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[],
                        memberships=[],
                    )
                )

            assert len(m.requests) == 0

    @pytest.mark.asyncio
    async def test_ufm_error_raises(self, mock_ufm_config):
        with aioresponses() as m:
            m.put(
                f"{UFM_BASE}/resources/pkeys/",
                status=400,
                payload={"error": "bad request"},
            )

            with pytest.raises(Exception):
                await set_pkey_members(
                    SetGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1],
                        memberships=["full"],
                    )
                )


# ---------------------------------------------------------------------------
# fetch_pkey_assignments
# ---------------------------------------------------------------------------


class TestFetchPKeyAssignments:
    @pytest.mark.asyncio
    async def test_returns_current_assignments(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        },
                        {
                            "id": ASSIGNMENT_UUID_2,
                            "assigned_object_id": IFACE_UUID_2,
                            "guid": GUID_2,
                        },
                    ]
                },
            )

            result = await fetch_pkey_assignments(
                FetchPKeyAssignmentsInput(overlay_id=OVERLAY_UUID)
            )

        assert len(result.assignments) == 2
        assert result.assignments[0].assignment_id == ASSIGNMENT_UUID_1
        assert result.assignments[0].interface_id == IFACE_UUID_1
        assert result.assignments[0].guid == GUID_1

    @pytest.mark.asyncio
    async def test_empty_overlay_returns_empty_list(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_ASSIGNMENTS, payload={"results": []})

            result = await fetch_pkey_assignments(
                FetchPKeyAssignmentsInput(overlay_id=OVERLAY_UUID)
            )

        assert result.assignments == []

    @pytest.mark.asyncio
    async def test_follows_pagination(self, mock_nb_config):
        """Assignments spanning multiple Nautobot pages are all collected."""
        with aioresponses() as m:
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "next": "http://nautobot/api/.../overlay-assignments/?offset=1",
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        }
                    ],
                },
            )
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "next": None,
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_2,
                            "assigned_object_id": IFACE_UUID_2,
                            "guid": GUID_2,
                        }
                    ],
                },
            )

            result = await fetch_pkey_assignments(
                FetchPKeyAssignmentsInput(overlay_id=OVERLAY_UUID)
            )

        assert [a.assignment_id for a in result.assignments] == [
            ASSIGNMENT_UUID_1,
            ASSIGNMENT_UUID_2,
        ]


# ---------------------------------------------------------------------------
# sync_pkey_assignments
# ---------------------------------------------------------------------------


def _make_resolved(interface_id: str, guid: str, interface: str = "mlx5_0") -> ResolvedInterface:
    return ResolvedInterface(
        device="hca01",
        interface=interface,
        interface_id=interface_id,
        guid=guid,
    )


class TestSyncPKeyAssignments:
    def _stub_status(self, m: aioresponses) -> None:
        m.get(
            _NB_STATUSES,
            payload={"results": [{"id": STATUS_UUID, "name": "Active"}]},
        )

    @pytest.mark.asyncio
    async def test_adds_new_members(self, mock_nb_config):
        """When overlay has no assignments, desired members are all created."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.post(
                f"{PLUGIN}/overlay-assignments/",
                payload={"id": ASSIGNMENT_UUID_1},
            )
            m.post(
                f"{PLUGIN}/overlay-assignments/",
                payload={"id": ASSIGNMENT_UUID_2},
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[
                        _make_resolved(IFACE_UUID_1, GUID_1, "mlx5_0"),
                        _make_resolved(IFACE_UUID_2, GUID_2, "mlx5_1"),
                    ],
                )
            )

        assert len(result.added) == 2
        assert len(result.removed) == 0
        assert len(result.unchanged) == 0

    @pytest.mark.asyncio
    async def test_removes_stale_members(self, mock_nb_config):
        """When desired list is empty, all existing assignments are removed."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        }
                    ]
                },
            )
            m.delete(
                f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/",
                status=204,
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[],
                )
            )

        assert len(result.removed) == 1
        assert ASSIGNMENT_UUID_1 in result.removed
        assert len(result.added) == 0

    @pytest.mark.asyncio
    async def test_unchanged_members_not_touched(self, mock_nb_config):
        """Members in both current and desired are left unchanged."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        }
                    ]
                },
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[_make_resolved(IFACE_UUID_1, GUID_1)],
                )
            )

        assert len(result.unchanged) == 1
        assert ASSIGNMENT_UUID_1 in result.unchanged
        assert len(result.added) == 0
        assert len(result.removed) == 0

    @pytest.mark.asyncio
    async def test_membership_change_patches_existing(self, mock_nb_config):
        """An existing member whose desired membership differs is PATCHed."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                            "membership_type": "full",
                        }
                    ]
                },
            )
            m.patch(
                f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/",
                payload={"id": ASSIGNMENT_UUID_1},
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[
                        ResolvedInterface(
                            device="hca01",
                            interface="mlx5_0",
                            interface_id=IFACE_UUID_1,
                            guid=GUID_1,
                            membership="limited",
                        )
                    ],
                )
            )

        assert ASSIGNMENT_UUID_1 in result.unchanged
        # A membership-only change issues a PATCH but no add/remove.
        patch_calls = [(method, str(url)) for (method, url) in m.requests if method == "PATCH"]
        assert patch_calls == [("PATCH", f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/")]

    @pytest.mark.asyncio
    async def test_mixed_add_remove_unchanged(self, mock_nb_config):
        """Simultaneously adds new, removes stale, keeps unchanged."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        # to be kept
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        },
                        # to be removed
                        {
                            "id": ASSIGNMENT_UUID_2,
                            "assigned_object_id": IFACE_UUID_2,
                            "guid": GUID_2,
                        },
                    ]
                },
            )
            m.delete(
                f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_2}/",
                status=204,
            )
            m.post(
                f"{PLUGIN}/overlay-assignments/",
                payload={"id": ASSIGNMENT_UUID_3},
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[
                        _make_resolved(IFACE_UUID_1, GUID_1, "mlx5_0"),  # keep
                        _make_resolved(IFACE_UUID_3, GUID_3, "mlx5_2"),  # add new
                    ],
                )
            )

        assert ASSIGNMENT_UUID_1 in result.unchanged
        assert ASSIGNMENT_UUID_2 in result.removed
        assert ASSIGNMENT_UUID_3 in result.added

    @pytest.mark.asyncio
    async def test_no_op_when_desired_matches_current(self, mock_nb_config):
        """No writes when current state already matches desired."""
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={
                    "results": [
                        {
                            "id": ASSIGNMENT_UUID_1,
                            "assigned_object_id": IFACE_UUID_1,
                            "guid": GUID_1,
                        },
                        {
                            "id": ASSIGNMENT_UUID_2,
                            "assigned_object_id": IFACE_UUID_2,
                            "guid": GUID_2,
                        },
                    ]
                },
            )

            result = await sync_pkey_assignments(
                SyncPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    desired=[
                        _make_resolved(IFACE_UUID_1, GUID_1, "mlx5_0"),
                        _make_resolved(IFACE_UUID_2, GUID_2, "mlx5_1"),
                    ],
                )
            )

        assert len(result.added) == 0
        assert len(result.removed) == 0
        assert len(result.unchanged) == 2
