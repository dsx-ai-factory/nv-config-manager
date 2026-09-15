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
"""Tests for InfiniBand PKey member management activities."""

import re
from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import CallbackResult, aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.ufm import UFMClientError
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    InterfaceRef,
    RecordPKeyAssignmentsInput,
    ResolvedInterface,
    ResolveInterfaceGuidsInput,
    record_pkey_assignments,
    resolve_interface_guids,
)
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    AddGuidsInput,
    VerifyPKeyMembersAbsentInput,
    VerifyPKeyMembersAbsentOutput,
    VerifyPKeyMembersInput,
    add_guids_to_pkey,
    verify_pkey_members,
    verify_pkey_members_absent,
)

UFM_BASE = "https://ufm.example.com/ufmRest"
NB_URL = "https://nautobot.example.com"
NB_API = f"{NB_URL}/api"
PLUGIN = f"{NB_API}/plugins/overlays"

OVERLAY_UUID = "ddd-444"
STATUS_UUID = "ccc-333"
IFACE_UUID_1 = "iface-001"
IFACE_UUID_2 = "iface-002"
ASSIGNMENT_UUID_1 = "asgn-001"
ASSIGNMENT_UUID_2 = "asgn-002"

GUID_1 = "0002c903000e0b72"
GUID_2 = "0002c903000e0b73"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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
    with patch("nv_config_manager.temporal.client.ufm.load_config") as mock:
        mock.return_value = _ufm_config()
        yield mock


@pytest.fixture()
def mock_nb_config():
    with patch("nv_config_manager.common.config.load_config") as mock:
        mock.return_value = _nb_config()
        yield mock


# ---------------------------------------------------------------------------
# add_guids_to_pkey
# ---------------------------------------------------------------------------


class TestAddGuidsToPKey:
    @pytest.mark.asyncio
    async def test_adds_guids_successfully(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": []})
            m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

            result = await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1, GUID_2],
                    memberships=["full", "full"],
                )
            )

        assert result.pkey == "0x0005"
        assert result.guids_added == [GUID_1, GUID_2]

    @pytest.mark.asyncio
    async def test_mixed_memberships_sent_in_single_put(self, mock_ufm_config):
        """A mixed add resolves to one PUT whose plural `memberships` is index-aligned.

        The add reads current members and issues a single atomic PUT, which UFM honors
        per-GUID via the plural `memberships` array -- no partial multi-call state.
        """
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": []})
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            result = await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1, GUID_2],
                    memberships=["full", "limited"],
                )
            )

        assert result.guids_added == [GUID_1, GUID_2]
        assert captured["guids"] == [GUID_1, GUID_2]
        assert captured["memberships"] == ["full", "limited"]
        assert "membership" not in captured

    @pytest.mark.asyncio
    async def test_merges_with_existing_members(self, mock_ufm_config):
        """New GUIDs merge onto the partition's current members instead of replacing them."""
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
            )
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_2],
                    memberships=["limited"],
                )
            )

        by_guid = dict(zip(captured["guids"], captured["memberships"], strict=True))
        assert by_guid == {GUID_1: "full", GUID_2: "limited"}

    @pytest.mark.asyncio
    async def test_requested_membership_wins_over_existing(self, mock_ufm_config):
        """Re-adding an existing GUID with a new membership updates it in the merged PUT."""
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
            )
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1],
                    memberships=["limited"],
                )
            )

        by_guid = dict(zip(captured["guids"], captured["memberships"], strict=True))
        assert by_guid == {GUID_1: "limited"}

    @pytest.mark.asyncio
    async def test_single_limited_member_sent_via_plural_memberships(self, mock_ufm_config):
        """A lone limited member goes out under the plural `memberships` UFM's PUT honors.

        UFM defaults members to "full" when the type is sent under a key it ignores;
        asserting the plural `memberships` on the PUT guards that regression.
        """
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": []})
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1],
                    memberships=["limited"],
                )
            )

        assert captured["guids"] == [GUID_1]
        assert captured["memberships"] == ["limited"]
        assert "membership" not in captured

    @pytest.mark.asyncio
    async def test_preserves_existing_ip_over_ib(self, mock_ufm_config):
        """The merged PUT reuses the partition's existing ip_over_ib, not the input default."""
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={})

        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": [], "ip_over_ib": False})
            m.put(f"{UFM_BASE}/resources/pkeys/", callback=_capture)

            await add_guids_to_pkey(
                AddGuidsInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    guids=[GUID_1],
                    memberships=["full"],
                    ip_over_ib=True,
                )
            )

        assert captured["ip_over_ib"] is False

    @pytest.mark.asyncio
    async def test_misaligned_memberships_rejected_without_http(self, mock_ufm_config):
        with aioresponses() as m:
            with pytest.raises(ApplicationError):
                await add_guids_to_pkey(
                    AddGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1, GUID_2],
                        memberships=["full"],
                    )
                )

            assert len(m.requests) == 0

    @pytest.mark.asyncio
    async def test_ufm_error_raises(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": []})
            m.put(f"{UFM_BASE}/resources/pkeys/", status=400, payload={"error": "bad"})

            with pytest.raises(UFMClientError):
                await add_guids_to_pkey(
                    AddGuidsInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1],
                        memberships=["full"],
                    )
                )


# ---------------------------------------------------------------------------
# verify_pkey_members
# ---------------------------------------------------------------------------


def _pkey_url(pkey: str) -> str:
    return f"{UFM_BASE}/resources/pkeys/{pkey}?guids_data=true"


class TestVerifyPKeyMembers:
    @pytest.mark.asyncio
    async def test_all_guids_present(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "partition": "api_pkey_0x5",
                    "ip_over_ib": True,
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "full"},
                    ],
                },
            )

            result = await verify_pkey_members(
                VerifyPKeyMembersInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    expected_guids=[GUID_1, GUID_2],
                )
            )

        assert result.verified is True
        assert result.missing_guids == []

    @pytest.mark.asyncio
    async def test_per_guid_membership_verified(self, mock_ufm_config):
        """When expected_memberships is given, each GUID's membership is checked."""
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "limited"},
                    ],
                },
            )

            result = await verify_pkey_members(
                VerifyPKeyMembersInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    expected_guids=[GUID_1, GUID_2],
                    expected_memberships=["full", "limited"],
                )
            )

        assert result.verified is True

    @pytest.mark.asyncio
    async def test_membership_mismatch_raises(self, mock_ufm_config):
        """A GUID present but with the wrong membership fails verification."""
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [{"guid": GUID_1, "membership": "full"}],
                },
            )

            with pytest.raises(ApplicationError, match="membership mismatch"):
                await verify_pkey_members(
                    VerifyPKeyMembersInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        expected_guids=[GUID_1],
                        expected_memberships=["limited"],
                    )
                )

    @pytest.mark.asyncio
    async def test_exact_rejects_unexpected_member(self, mock_ufm_config):
        """In exact mode, a member present on UFM but not expected fails verification."""
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "full"},
                    ],
                },
            )

            with pytest.raises(ApplicationError, match="unexpected GUID"):
                await verify_pkey_members(
                    VerifyPKeyMembersInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        expected_guids=[GUID_1],
                        exact=True,
                    )
                )

    @pytest.mark.asyncio
    async def test_extra_member_tolerated_without_exact(self, mock_ufm_config):
        """Without exact mode (add workflow), other members may coexist."""
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [
                        {"guid": GUID_1, "membership": "full"},
                        {"guid": GUID_2, "membership": "full"},
                    ],
                },
            )

            result = await verify_pkey_members(
                VerifyPKeyMembersInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    expected_guids=[GUID_1],
                )
            )

        assert result.verified is True

    @pytest.mark.asyncio
    async def test_guid_case_insensitive(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [{"guid": GUID_1.upper(), "membership": "full"}],
                },
            )

            result = await verify_pkey_members(
                VerifyPKeyMembersInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    expected_guids=[GUID_1.lower()],
                )
            )

        assert result.verified is True

    @pytest.mark.asyncio
    async def test_missing_guid_raises_retryable_error(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={
                    "guids": [{"guid": GUID_1, "membership": "full"}],
                },
            )

            with pytest.raises(ApplicationError) as exc_info:
                await verify_pkey_members(
                    VerifyPKeyMembersInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        expected_guids=[GUID_1, GUID_2],
                    )
                )

        assert exc_info.value.non_retryable is False
        assert GUID_2 in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pkey_not_found_raises(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload=None)

            with pytest.raises(ApplicationError, match="not found"):
                await verify_pkey_members(
                    VerifyPKeyMembersInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        expected_guids=[GUID_1],
                    )
                )


class TestVerifyPKeyMembersAbsent:
    @pytest.mark.asyncio
    async def test_absent_when_empty(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), payload={"guids": []})

            result = await verify_pkey_members_absent(
                VerifyPKeyMembersAbsentInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    forbidden_guids=[GUID_1, GUID_2],
                )
            )

        assert result.verified is True
        assert result.still_present_guids == []
        assert result.partition_exists is True
        assert result.remaining_member_count == 0

    @pytest.mark.asyncio
    async def test_auto_removed_pkey_is_verified_absent(self, mock_ufm_config):
        """A 404 means UFM removed the now-empty partition: members are absent."""
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), status=404, payload={"error": "not found"})

            result = await verify_pkey_members_absent(
                VerifyPKeyMembersAbsentInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    forbidden_guids=[GUID_1],
                )
            )

        assert result.verified is True
        assert result.still_present_guids == []
        assert result.partition_exists is False
        assert result.remaining_member_count == 0

    def test_legacy_payload_defaults_remaining_count_to_unknown(self):
        """A payload missing remaining_member_count loads as None, not a proven 0.

        Guards backward-compat: in-flight outputs serialized before the field
        existed must not be mistaken for an empty partition.
        """
        legacy = VerifyPKeyMembersAbsentOutput(
            pkey="0x0005",
            verified=True,
            still_present_guids=[],
            display="legacy",
        )

        assert legacy.partition_exists is True
        assert legacy.remaining_member_count is None

    @pytest.mark.asyncio
    async def test_reports_remaining_untracked_members(self, mock_ufm_config):
        """Forbidden GUIDs gone, but other members remain: partition is NOT empty."""
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={"guids": [{"guid": GUID_2, "membership": "full"}]},
            )

            result = await verify_pkey_members_absent(
                VerifyPKeyMembersAbsentInput(
                    host="ufm.example.com",
                    pkey="0x0005",
                    forbidden_guids=[GUID_1],
                )
            )

        assert result.verified is True
        assert result.still_present_guids == []
        assert result.partition_exists is True
        assert result.remaining_member_count == 1

    @pytest.mark.asyncio
    async def test_still_present_raises_retryable(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(
                _pkey_url("0x0005"),
                payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
            )

            with pytest.raises(ApplicationError) as exc_info:
                await verify_pkey_members_absent(
                    VerifyPKeyMembersAbsentInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        forbidden_guids=[GUID_1],
                    )
                )

        assert exc_info.value.non_retryable is False
        assert GUID_1 in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_404_error_propagates(self, mock_ufm_config):
        with aioresponses() as m:
            m.get(_pkey_url("0x0005"), status=500, payload={"error": "boom"})

            with pytest.raises(UFMClientError):
                await verify_pkey_members_absent(
                    VerifyPKeyMembersAbsentInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        forbidden_guids=[GUID_1],
                    )
                )


# ---------------------------------------------------------------------------
# resolve_interface_guids
# ---------------------------------------------------------------------------


_NB_INTERFACES = re.compile(rf"{re.escape(NB_API)}/dcim/interfaces/.*")


class TestResolveInterfaceGuids:
    @pytest.mark.asyncio
    async def test_resolves_guids(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                _NB_INTERFACES,
                payload={
                    "results": [
                        {
                            "id": IFACE_UUID_1,
                            "name": "mlx5_0",
                            "custom_fields": {"ib_guid": GUID_1},
                        }
                    ]
                },
            )
            m.get(
                _NB_INTERFACES,
                payload={
                    "results": [
                        {
                            "id": IFACE_UUID_2,
                            "name": "mlx5_1",
                            "custom_fields": {"ib_guid": GUID_2},
                        }
                    ]
                },
            )

            result = await resolve_interface_guids(
                ResolveInterfaceGuidsInput(
                    interfaces=[
                        InterfaceRef(device="hca01", interface="mlx5_0"),
                        InterfaceRef(device="hca01", interface="mlx5_1"),
                    ]
                )
            )

        assert len(result.resolved) == 2
        assert result.resolved[0].guid == GUID_1
        assert result.resolved[0].interface_id == IFACE_UUID_1
        assert result.resolved[1].guid == GUID_2

    @pytest.mark.asyncio
    async def test_interface_not_found_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_INTERFACES, payload={"results": []})

            with pytest.raises(ApplicationError, match="not found"):
                await resolve_interface_guids(
                    ResolveInterfaceGuidsInput(
                        interfaces=[InterfaceRef(device="hca01", interface="ghost")]
                    )
                )

    @pytest.mark.asyncio
    async def test_missing_guid_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                _NB_INTERFACES,
                payload={
                    "results": [
                        {
                            "id": IFACE_UUID_1,
                            "name": "mlx5_0",
                            "custom_fields": {"ib_guid": ""},
                        }
                    ]
                },
            )

            with pytest.raises(ApplicationError, match="no IB GUID"):
                await resolve_interface_guids(
                    ResolveInterfaceGuidsInput(
                        interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")]
                    )
                )

    @pytest.mark.asyncio
    async def test_null_custom_fields_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                _NB_INTERFACES,
                payload={
                    "results": [
                        {
                            "id": IFACE_UUID_1,
                            "name": "mlx5_0",
                            "custom_fields": None,
                        }
                    ]
                },
            )

            with pytest.raises(ApplicationError, match="no IB GUID"):
                await resolve_interface_guids(
                    ResolveInterfaceGuidsInput(
                        interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")]
                    )
                )


# ---------------------------------------------------------------------------
# record_pkey_assignments
# ---------------------------------------------------------------------------


_NB_STATUSES = re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*")
_NB_ASSIGNMENTS = re.compile(rf"{re.escape(PLUGIN)}/overlay-assignments/.*")


class TestRecordPKeyAssignments:
    def _resolved_pair(self) -> list[ResolvedInterface]:
        return [
            ResolvedInterface(
                device="hca01",
                interface="mlx5_0",
                interface_id=IFACE_UUID_1,
                guid=GUID_1,
            ),
            ResolvedInterface(
                device="hca01",
                interface="mlx5_1",
                interface_id=IFACE_UUID_2,
                guid=GUID_2,
            ),
        ]

    def _stub_status(self, m: aioresponses) -> None:
        m.get(_NB_STATUSES, payload={"results": [{"id": STATUS_UUID, "name": "Active"}]})

    @pytest.mark.asyncio
    async def test_creates_assignments(self, mock_nb_config):
        with aioresponses() as m:
            self._stub_status(m)
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.post(
                f"{PLUGIN}/overlay-assignments/",
                payload={"id": ASSIGNMENT_UUID_1},
            )
            m.get(_NB_ASSIGNMENTS, payload={"results": []})
            m.post(
                f"{PLUGIN}/overlay-assignments/",
                payload={"id": ASSIGNMENT_UUID_2},
            )

            result = await record_pkey_assignments(
                RecordPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    resolved=self._resolved_pair(),
                )
            )

        assert result.assignment_ids == [ASSIGNMENT_UUID_1, ASSIGNMENT_UUID_2]

    @pytest.mark.asyncio
    async def test_idempotent_skips_existing(self, mock_nb_config):
        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={"results": [{"id": ASSIGNMENT_UUID_1}]},
            )
            m.get(
                _NB_ASSIGNMENTS,
                payload={"results": [{"id": ASSIGNMENT_UUID_2}]},
            )

            result = await record_pkey_assignments(
                RecordPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    resolved=self._resolved_pair(),
                )
            )

        assert result.assignment_ids == [ASSIGNMENT_UUID_1, ASSIGNMENT_UUID_2]

    @pytest.mark.asyncio
    async def test_patches_existing_membership_change(self, mock_nb_config):
        """Re-adding an existing member with a new membership patches Nautobot."""
        patched: list[dict] = []

        def _record_patch(url, **kwargs):
            patched.append(kwargs.get("json") or {})
            return CallbackResult(status=200, payload={"id": ASSIGNMENT_UUID_1})

        with aioresponses() as m:
            self._stub_status(m)
            m.get(
                _NB_ASSIGNMENTS,
                payload={"results": [{"id": ASSIGNMENT_UUID_1, "membership_type": "full"}]},
            )
            m.patch(
                f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/",
                callback=_record_patch,
            )

            result = await record_pkey_assignments(
                RecordPKeyAssignmentsInput(
                    overlay_id=OVERLAY_UUID,
                    resolved=[
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

        assert result.assignment_ids == [ASSIGNMENT_UUID_1]
        assert patched == [{"membership_type": "limited"}]

    @pytest.mark.asyncio
    async def test_status_not_found_raises(self, mock_nb_config):
        with aioresponses() as m:
            m.get(_NB_STATUSES, payload={"results": []})

            with pytest.raises(ApplicationError, match="Status.*not found"):
                await record_pkey_assignments(
                    RecordPKeyAssignmentsInput(
                        overlay_id=OVERLAY_UUID,
                        resolved=self._resolved_pair()[:1],
                    )
                )
