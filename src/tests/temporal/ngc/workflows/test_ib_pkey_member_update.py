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
"""Tests for IBPKeyMemberUpdateWorkflow."""

import asyncio
import re
import uuid
from configparser import ConfigParser
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import CallbackResult, aioresponses
from temporalio.worker import Worker

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    ResolvedInterface,
    fetch_pkey_assignments,
    resolve_guids_to_interfaces,
    resolve_ib_context,
    resolve_interface_guids,
    sync_pkey_assignments,
)
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    fetch_pkey_members,
    set_pkey_members,
    verify_pkey_members,
)
from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateInput,
    IBPKeyMemberUpdateOutput,
    IBPKeyMemberUpdateWorkflow,
    InterfaceRef,
    _unresolved_guid_values,
)
from tests.temporal.ib_helpers import (
    stub_graphql_resolve_guids,
    stub_graphql_resolve_ib_context,
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

_NB_INTERFACES = re.compile(rf"{re.escape(NB_API)}/dcim/interfaces/.*")
_NB_STATUSES = re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*")
_NB_ASSIGNMENTS = re.compile(rf"{re.escape(PLUGIN)}/overlay-assignments/.*")
_UFM_DELETE_GUIDS = re.compile(rf"{re.escape(UFM_BASE)}/resources/pkeys/.+/guids/.+")


def test_unresolved_guid_values_detects_missing_removal_resolution():
    """Defensive removal checks catch GUIDs dropped by reverse-resolution."""
    resolved = [
        ResolvedInterface(
            device="hca01",
            interface="mlx5_0",
            interface_id=IFACE_UUID_1,
            guid=GUID_1.upper(),
        )
    ]

    assert _unresolved_guid_values([GUID_1, GUID_2], resolved) == [GUID_2]


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
def mock_all_configs():
    mock_producer = AsyncMock()
    mock_producer.publish = AsyncMock(return_value=None)
    with (
        patch("nv_config_manager.temporal.client.ufm.load_config", return_value=_ufm_config()),
        patch("nv_config_manager.common.config.load_config", return_value=_nb_config()),
        patch(
            "nv_config_manager.temporal.ngc.activities.nats.NatsProducer",
            return_value=mock_producer,
        ),
    ):
        yield


_ALL_ACTIVITIES = [
    resolve_ib_context,
    resolve_interface_guids,
    resolve_guids_to_interfaces,
    fetch_pkey_assignments,
    sync_pkey_assignments,
    fetch_pkey_members,
    set_pkey_members,
    verify_pkey_members,
    publish_nats,
    *REGISTERED_COMMON_ACTIVITIES,
]


def _stub_ufm_current(m: aioresponses, pkey: str, payload: dict) -> None:
    """Stub the query_current UFM read (fetch_pkey_members) for a pkey.

    Registered before the verify_ufm GET so aioresponses serves it to the
    earlier query_current request (FIFO per URL).
    """
    m.get(_pkey_verify_url(pkey), payload=payload)


def _pkey_verify_url(pkey: str) -> str:
    return f"{UFM_BASE}/resources/pkeys/{pkey}?guids_data=true"


def _stub_resolve_interfaces(m: aioresponses, iface_guid_pairs: list[tuple]) -> None:
    """Register Nautobot interface resolution responses."""
    for iface_uuid, guid in iface_guid_pairs:
        m.get(
            _NB_INTERFACES,
            payload={
                "results": [
                    {
                        "id": iface_uuid,
                        "name": "mlx5_0",
                        "custom_fields": {"ib_guid": guid},
                    }
                ]
            },
        )


def _stub_status(m: aioresponses) -> None:
    m.get(
        _NB_STATUSES,
        payload={"results": [{"id": STATUS_UUID, "name": "Active"}]},
    )


async def _wait_for_pending_approval(handle, timeout: float = 10.0) -> None:
    """Poll until the workflow reports pending_approval, bounded by ``timeout``."""

    async def _poll() -> None:
        while await handle.query("pending_approval") is False:
            await asyncio.sleep(0.1)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_additions_only_auto_approved(mock_all_configs, time_skipping_env):
    """Workflow completes without approval gate when only adding members."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                # Stage 0: resolve_ib_context
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)

                # Stage 1: resolve desired
                _stub_resolve_interfaces(
                    m,
                    [(IFACE_UUID_1, GUID_1), (IFACE_UUID_2, GUID_2)],
                )

                # Stage 2: query current
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                _stub_ufm_current(m, "0x0005", {"guids": []})

                # Stage 3: validate_diff auto-approved

                # Stage 4: update_nautobot
                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(
                    f"{PLUGIN}/overlay-assignments/",
                    payload={"id": ASSIGNMENT_UUID_1},
                )
                m.post(
                    f"{PLUGIN}/overlay-assignments/",
                    payload={"id": ASSIGNMENT_UUID_2},
                )

                # Stage 5: update_ufm
                m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

                # Stage 6: verify_ufm
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={
                        "guids": [
                            {"guid": GUID_1, "membership": "full"},
                            {"guid": GUID_2, "membership": "full"},
                        ]
                    },
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[
                            InterfaceRef(device="hca01", interface="mlx5_0"),
                            InterfaceRef(device="hca01", interface="mlx5_1"),
                        ],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert isinstance(result, IBPKeyMemberUpdateOutput)
    assert result.pkey == "0x0005"
    assert result.overlay_id == OVERLAY_UUID
    assert result.overlay_name == "ib-pkey-overlay"
    assert result.members_added == 2
    assert result.members_removed == 0
    assert result.members_unchanged == 0
    assert result.verified is True
    assert len(result.assignment_ids_added) == 2


@pytest.mark.asyncio
async def test_per_interface_membership_sent_to_ufm(mock_all_configs, time_skipping_env):
    """A per-interface membership override flows into the UFM."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                _stub_resolve_interfaces(
                    m,
                    [(IFACE_UUID_1, GUID_1), (IFACE_UUID_2, GUID_2)],
                )
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                _stub_ufm_current(m, "0x0005", {"guids": []})

                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_1})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})

                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)

                # verify_ufm: UFM reflects the per-port memberships we set
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={
                        "guids": [
                            {"guid": GUID_1, "membership": "full"},
                            {"guid": GUID_2, "membership": "limited"},
                        ]
                    },
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[
                            InterfaceRef(device="hca01", interface="mlx5_0"),
                            InterfaceRef(device="hca01", interface="mlx5_1", membership="limited"),
                        ],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.verified is True
    assert put_bodies[0].get("guids") == [GUID_1, GUID_2]
    # mlx5_0 inherits the default (full); mlx5_1 overrides to limited.
    assert put_bodies[0].get("memberships") == ["full", "limited"]
    assert "membership" not in put_bodies[0]


@pytest.mark.asyncio
async def test_per_guid_membership_sent_to_ufm(mock_all_configs, time_skipping_env):
    """Per-GUID memberships from the guids input flow into the UFM PUT memberships[]."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                stub_graphql_resolve_guids(m, [(GUID_1, IFACE_UUID_1), (GUID_2, IFACE_UUID_2)])
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                _stub_ufm_current(m, "0x0005", {"guids": []})

                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_1})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})

                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)

                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={
                        "guids": [
                            {"guid": GUID_1, "membership": "limited"},
                            {"guid": GUID_2, "membership": "full"},
                        ]
                    },
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1, GUID_2],
                        guid_memberships=["limited", "full"],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.verified is True
    assert put_bodies[0].get("guids") == [GUID_1, GUID_2]
    assert put_bodies[0].get("memberships") == ["limited", "full"]
    assert "membership" not in put_bodies[0]


@pytest.mark.asyncio
async def test_idempotent_put_when_desired_matches_current(mock_all_configs, time_skipping_env):
    """No Nautobot writes when desired == current, but UFM still gets the reconciling PUT.

    The workflow reconciles UFM to the exact desired set, so even when Nautobot
    already matches it issues the idempotent PUT to correct any UFM drift.
    """
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                # Stage 0: resolve_ib_context
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)

                # Stage 1: resolve desired
                _stub_resolve_interfaces(m, [(IFACE_UUID_1, GUID_1)])

                # Stage 2: query current — same interface already assigned
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
                # UFM already holds exactly the desired member -> no UFM-only removals
                _stub_ufm_current(m, "0x0005", {"guids": [{"guid": GUID_1, "membership": "full"}]})

                # Stage 3: validate_diff auto-approved

                # Stage 4: update_nautobot — unchanged, no writes
                _stub_status(m)
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

                # Stage 5: update_ufm — idempotent PUT still fires to reconcile UFM
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)

                # Stage 6: verify_ufm
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[
                            InterfaceRef(device="hca01", interface="mlx5_0"),
                        ],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.members_added == 0
    assert result.members_removed == 0
    assert result.members_unchanged == 1
    assert result.verified is True
    # UFM reconciliation PUT fires once with the exact desired set, even on a no-op diff.
    assert len(put_bodies) == 1
    assert put_bodies[0].get("guids") == [GUID_1]


@pytest.mark.asyncio
async def test_membership_only_change_sent_to_ufm(mock_all_configs, time_skipping_env):
    """Flipping an existing member's membership (no GUID add/remove) still PUTs to UFM."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    current_assignment = {
        "results": [
            {
                "id": ASSIGNMENT_UUID_1,
                "assigned_object_id": IFACE_UUID_1,
                "guid": GUID_1,
                "membership_type": "full",
            }
        ]
    }

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                _stub_resolve_interfaces(m, [(IFACE_UUID_1, GUID_1)])

                # query_current: member already present with membership "full"
                m.get(_NB_ASSIGNMENTS, payload=current_assignment)
                _stub_ufm_current(m, "0x0005", {"guids": [{"guid": GUID_1, "membership": "full"}]})

                # update_nautobot: same member, membership patched full -> limited
                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload=current_assignment)
                m.patch(
                    f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/",
                    payload={"id": ASSIGNMENT_UUID_1},
                )

                # update_ufm: PUT must still fire even though no GUID was added/removed
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)

                # verify_ufm: UFM now reflects the new membership
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "limited"}]},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[
                            InterfaceRef(device="hca01", interface="mlx5_0", membership="limited"),
                        ],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.verified is True
    assert result.members_added == 0
    assert result.members_removed == 0
    assert result.members_unchanged == 1
    # The membership-only change must still produce one UFM PUT with the new membership.
    # UFM's Set endpoint (PUT) honors the index-aligned plural `memberships`.
    assert len(put_bodies) == 1
    assert put_bodies[0].get("guids") == [GUID_1]
    assert put_bodies[0].get("memberships") == ["limited"]


def test_input_rejects_neither_interfaces_nor_guids():
    """Validator rejects an input with neither interfaces nor GUIDs."""
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids'"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey="0x0005",
        )


def test_input_rejects_both_interfaces_and_guids():
    """Validator rejects an input with both interfaces and GUIDs."""
    with pytest.raises(
        ValueError, match=r"One of 'interfaces' or 'guids' must be provided, but not both\."
    ):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey="0x0005",
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guids=[GUID_1],
        )


@pytest.mark.parametrize("bad_pkey", ["", "5", "0x", "0xZZZZ", "0x12345"])
def test_input_rejects_bad_pkey_format(bad_pkey):
    with pytest.raises(ValueError, match="pkey must be hex"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=bad_pkey,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        )


def test_input_normalizes_guid_memberships():
    """Per-GUID membership values are normalized to canonical 'full'/'limited'."""
    parsed = IBPKeyMemberUpdateInput(
        host="ufm.example.com",
        pkey="0x0005",
        guids=[GUID_1, GUID_2],
        guid_memberships=["LIMITED", "Full"],
    )
    assert parsed.guid_memberships == ["limited", "full"]


def test_input_rejects_guid_memberships_length_mismatch():
    """guid_memberships must be index-aligned with guids."""
    with pytest.raises(ValueError, match="same length as guids"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey="0x0005",
            guids=[GUID_1, GUID_2],
            guid_memberships=["full"],
        )


def test_input_rejects_guid_memberships_with_interfaces():
    """guid_memberships is only valid with the GUID input path."""
    with pytest.raises(ValueError, match="only valid with the 'guids' input"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey="0x0005",
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guid_memberships=["full"],
        )


def _update_input(**overrides):
    """Build a minimal valid Member Update input, allowing field overrides."""
    params = {
        "host": "ufm.example.com",
        "pkey": "0x0005",
        "interfaces": [InterfaceRef(device="hca01", interface="mlx5_0")],
    }
    params.update(overrides)
    return IBPKeyMemberUpdateInput(**params)


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_membership_type_blank_defaults_to_full(blank):
    """A blank/None membership_type defaults to 'full' instead of crashing."""
    assert _update_input(membership_type=blank).membership_type == "full"


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [("limited", "limited"), ("LIMITED", "limited"), ("Full", "full")],
)
def test_membership_type_override_is_honored(supplied, expected):
    """A supplied membership_type override is honored (case-insensitive)."""
    assert _update_input(membership_type=supplied).membership_type == expected


def test_membership_type_rejects_invalid():
    """An invalid membership_type is rejected at the input boundary (422)."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _update_input(membership_type="partial")


@pytest.mark.parametrize("bad", [1, True, 1.5])
def test_membership_type_rejects_non_string(bad):
    """Non-string membership_type yields a clean validation error, not a crash."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _update_input(membership_type=bad)


@pytest.mark.asyncio
async def test_full_swap_atomically_replaces_membership(mock_all_configs, time_skipping_env):
    """A full membership swap is applied to UFM as a single atomic PUT."""
    task_queue = str(uuid.uuid4())
    ufm_calls: list[str] = []
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        ufm_calls.append("set")
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    def _record_add(url, **kwargs):
        ufm_calls.append("add")
        return CallbackResult(status=200, payload={})

    def _record_remove(url, **kwargs):
        ufm_calls.append("remove")
        return CallbackResult(status=200, payload={})

    current_assignment = {
        "id": ASSIGNMENT_UUID_1,
        "assigned_object_id": IFACE_UUID_1,
        "guid": GUID_1,
    }

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                # Stage 0: resolve_ib_context
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)

                # Stage 1: resolve desired -> IFACE_UUID_2 / GUID_2
                _stub_resolve_interfaces(m, [(IFACE_UUID_2, GUID_2)])

                # Stage 2: query current -> IFACE_UUID_1 / GUID_1 is the stale member
                m.get(_NB_ASSIGNMENTS, payload={"results": [current_assignment]})
                # query_current reverse-resolves the removal GUID via GraphQL
                stub_graphql_resolve_guids(m, [(GUID_1, IFACE_UUID_1)])
                # UFM holds the tracked stale member; its removal is already in the diff
                _stub_ufm_current(m, "0x0005", {"guids": [{"guid": GUID_1, "membership": "full"}]})

                # Stage 3: validate_diff -> requires approval

                # Stage 4: update_nautobot: status, current, delete stale, post new
                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload={"results": [current_assignment]})
                m.delete(f"{PLUGIN}/overlay-assignments/{ASSIGNMENT_UUID_1}/", payload={})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})

                # Stage 5: update_ufm -> exactly one PUT; any add/remove is a failure
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)
                m.post(f"{UFM_BASE}/resources/pkeys/", callback=_record_add)
                m.delete(_UFM_DELETE_GUIDS, callback=_record_remove)

                # Stage 6: verify_ufm -> only the incoming member remains
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_2, "membership": "full"}]},
                )

                handle = await env.client.start_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[InterfaceRef(device="hca01", interface="mlx5_1")],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                await _wait_for_pending_approval(handle)

                await handle.signal("approve", {"stage_name": "validate_diff", "user": "Test"})

                result = await handle.result()

    assert result.members_added == 1
    assert result.members_removed == 1
    assert result.verified is True
    # Exactly one UFM mutation, and it is the atomic set (no add/remove calls).
    assert ufm_calls == ["set"]
    # The PUT carries the full desired membership (only the incoming GUID).
    assert put_bodies[0].get("guids") == [GUID_2]


@pytest.mark.asyncio
async def test_guids_only_path_resolves_via_graphql(mock_all_configs, time_skipping_env):
    """GUIDs-only input reverse-resolves to interfaces and completes the workflow."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                stub_graphql_resolve_guids(m, [(GUID_1, IFACE_UUID_1)])

                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                _stub_ufm_current(m, "0x0005", {"guids": []})

                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(
                    f"{PLUGIN}/overlay-assignments/",
                    payload={"id": ASSIGNMENT_UUID_1},
                )

                m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.members_added == 1
    assert result.members_removed == 0
    assert result.verified is True


@pytest.mark.asyncio
async def test_ufm_only_member_triggers_approval(mock_all_configs, time_skipping_env):
    """A member present only on UFM (untracked by Nautobot) forces the approval gate.

    The exact-set PUT drops it, so it must surface as a removal for approval
    instead of being silently auto-approved off the Nautobot-only diff.
    """
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    current_assignment = {
        "results": [
            {
                "id": ASSIGNMENT_UUID_1,
                "assigned_object_id": IFACE_UUID_1,
                "guid": GUID_1,
                "membership_type": "full",
            }
        ]
    }

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                _stub_resolve_interfaces(m, [(IFACE_UUID_1, GUID_1)])

                # query_current: Nautobot tracks only GUID_1, so no tracked removals
                m.get(_NB_ASSIGNMENTS, payload=current_assignment)
                # UFM holds an extra untracked member GUID_2 the exact PUT would drop
                _stub_ufm_current(
                    m,
                    "0x0005",
                    {
                        "guids": [
                            {"guid": GUID_1, "membership": "full"},
                            {"guid": GUID_2, "membership": "full"},
                        ]
                    },
                )

                # update_nautobot: unchanged
                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload=current_assignment)

                # update_ufm: exact PUT carries only the desired set
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)

                # verify_ufm: only the desired member remains
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )

                handle = await env.client.start_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Reaching pending_approval proves the UFM-only removal was not
                # auto-approved; an additions-only diff would never gate here.
                await _wait_for_pending_approval(handle)
                await handle.signal("approve", {"stage_name": "validate_diff", "user": "Test"})
                result = await handle.result()

    assert result.verified is True
    assert result.members_removed == 0  # Nautobot tracked no removals
    assert result.members_unchanged == 1
    # The exact PUT carries only the desired set, dropping the untracked UFM member.
    assert len(put_bodies) == 1
    assert put_bodies[0].get("guids") == [GUID_1]


@pytest.mark.asyncio
async def test_preserves_ip_over_ib_from_ufm(mock_all_configs, time_skipping_env):
    """The reconciling PUT preserves the partition's existing ip_over_ib from UFM."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_set(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    current_assignment = {
        "results": [
            {
                "id": ASSIGNMENT_UUID_1,
                "assigned_object_id": IFACE_UUID_1,
                "guid": GUID_1,
                "membership_type": "full",
            }
        ]
    }

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberUpdateWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                _stub_resolve_interfaces(m, [(IFACE_UUID_1, GUID_1)])

                m.get(_NB_ASSIGNMENTS, payload=current_assignment)
                # UFM partition is already configured with ip_over_ib disabled
                _stub_ufm_current(
                    m,
                    "0x0005",
                    {
                        "guids": [{"guid": GUID_1, "membership": "full"}],
                        "ip_over_ib": False,
                    },
                )

                _stub_status(m)
                m.get(_NB_ASSIGNMENTS, payload=current_assignment)
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_set)
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberUpdateWorkflow.run,
                    IBPKeyMemberUpdateInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
                        ip_over_ib=True,  # input default; UFM's False must win
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.verified is True
    # Despite the input requesting ip_over_ib=True, the PUT preserves UFM's False.
    assert put_bodies[0].get("ip_over_ib") is False
