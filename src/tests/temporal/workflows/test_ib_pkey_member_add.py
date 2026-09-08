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
"""Tests for IBPKey Member Add Workflow.

Uses a registered Temporal time-skipping test environment with mocked HTTP responses
for both UFM (via aioresponses) and Nautobot (via aioresponses).
"""

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
    record_pkey_assignments,
    resolve_guids_to_interfaces,
    resolve_ib_context_for_add,
    resolve_interface_guids,
)
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    add_guids_to_pkey,
    verify_pkey_members,
)
from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddOutput,
    IBPKeyMemberAddWorkflow,
    InterfaceRef,
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
IFACE_CT_UUID = "fff-666"
IFACE_UUID_1 = "iface-001"
IFACE_UUID_2 = "iface-002"
ASSIGNMENT_UUID_1 = "asgn-001"
ASSIGNMENT_UUID_2 = "asgn-002"

GUID_1 = "0002c903000e0b72"
GUID_2 = "0002c903000e0b73"


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


@pytest.fixture(autouse=True)
def _mock_nats():
    """Prevent real NATS connections from ArchiveMixin."""
    mock_producer = AsyncMock()
    mock_producer.__aenter__ = AsyncMock(return_value=mock_producer)
    mock_producer.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "nv_config_manager.temporal.ngc.activities.nats.NatsProducer",
        return_value=mock_producer,
    ):
        yield


@pytest.fixture()
def mock_all_configs():
    """Mock both UFM and Nautobot config loading."""
    with (
        patch("nv_config_manager.temporal.client.ufm.load_config", return_value=_ufm_config()),
        patch("nv_config_manager.common.config.load_config", return_value=_nb_config()),
    ):
        yield


_ALL_ACTIVITIES = [
    resolve_ib_context_for_add,
    resolve_interface_guids,
    resolve_guids_to_interfaces,
    add_guids_to_pkey,
    verify_pkey_members,
    record_pkey_assignments,
    publish_nats,
    *REGISTERED_COMMON_ACTIVITIES,
]


_NB_INTERFACES = re.compile(rf"{re.escape(NB_API)}/dcim/interfaces/.*")
_NB_STATUSES = re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*")
_NB_CONTENT_TYPES = re.compile(rf"{re.escape(NB_API)}/extras/content-types/.*")
_NB_ASSIGNMENTS = re.compile(rf"{re.escape(PLUGIN)}/overlay-assignments/.*")


def _pkey_verify_url(pkey: str) -> str:
    """Build UFM verify URL with guids_data query param."""
    return f"{UFM_BASE}/resources/pkeys/{pkey}?guids_data=true"


def _stub_full_run(m: aioresponses) -> None:
    """Register all mocked responses for a successful two-interface run."""
    # Stage 0: resolve_ib_context (always runs first)
    stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)

    # Stage 1: resolve GUIDs
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

    # Stage 2: add GUIDs to PKey (read current members, then PUT the merged set)
    m.get(_pkey_verify_url("0x0005"), payload={"guids": []})
    m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

    # Stage 3: verify members (exact URL with query param)
    m.get(
        _pkey_verify_url("0x0005"),
        payload={
            "guids": [
                {"guid": GUID_1, "membership": "full"},
                {"guid": GUID_2, "membership": "full"},
            ]
        },
    )

    # Stage 4: record assignments
    m.get(_NB_STATUSES, payload={"results": [{"id": STATUS_UUID, "name": "Active"}]})
    m.get(
        _NB_CONTENT_TYPES,
        payload={"results": [{"id": IFACE_CT_UUID, "app_label": "dcim", "model": "interface"}]},
    )
    m.get(_NB_ASSIGNMENTS, payload={"results": []})
    m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_1})
    m.get(_NB_ASSIGNMENTS, payload={"results": []})
    m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})


@pytest.mark.asyncio
async def test_full_workflow_happy_path(mock_all_configs, time_skipping_env):
    """Complete four-stage run with two interfaces."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberAddWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                _stub_full_run(m)

                result = await env.client.execute_workflow(
                    IBPKeyMemberAddWorkflow.run,
                    IBPKeyMemberAddInput(
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

    assert isinstance(result, IBPKeyMemberAddOutput)
    assert result.pkey == "0x0005"
    assert result.overlay_id == OVERLAY_UUID
    assert result.overlay_name == "ib-pkey-overlay"
    assert result.members_added == 2
    assert result.verified is True
    assert result.assignment_ids == [ASSIGNMENT_UUID_1, ASSIGNMENT_UUID_2]


@pytest.mark.asyncio
async def test_per_interface_membership_sent_to_ufm(mock_all_configs, time_skipping_env):
    """A per-interface membership override rides the single merged PUT to UFM."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_add(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberAddWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
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
                m.get(_pkey_verify_url("0x0005"), payload={"guids": []})
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_add)
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={
                        "guids": [
                            {"guid": GUID_1, "membership": "full"},
                            {"guid": GUID_2, "membership": "limited"},
                        ]
                    },
                )
                m.get(_NB_STATUSES, payload={"results": [{"id": STATUS_UUID, "name": "Active"}]})
                m.get(
                    _NB_CONTENT_TYPES,
                    payload={
                        "results": [
                            {"id": IFACE_CT_UUID, "app_label": "dcim", "model": "interface"}
                        ]
                    },
                )
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_1})
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})

                result = await env.client.execute_workflow(
                    IBPKeyMemberAddWorkflow.run,
                    IBPKeyMemberAddInput(
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
    assert len(put_bodies) == 1
    assert put_bodies[0].get("guids") == [GUID_1, GUID_2]
    assert put_bodies[0].get("memberships") == ["full", "limited"]
    assert "membership" not in put_bodies[0]


@pytest.mark.asyncio
async def test_per_guid_membership_sent_to_ufm(mock_all_configs, time_skipping_env):
    """Per-GUID memberships ride the single merged PUT, index-aligned with guids."""
    task_queue = str(uuid.uuid4())
    put_bodies: list[dict] = []

    def _record_add(url, **kwargs):
        put_bodies.append(kwargs.get("json") or {})
        return CallbackResult(status=200, payload={})

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberAddWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                stub_graphql_resolve_guids(m, [(GUID_1, IFACE_UUID_1), (GUID_2, IFACE_UUID_2)])

                m.get(_pkey_verify_url("0x0005"), payload={"guids": []})
                m.put(f"{UFM_BASE}/resources/pkeys/", callback=_record_add)
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={
                        "guids": [
                            {"guid": GUID_1, "membership": "limited"},
                            {"guid": GUID_2, "membership": "full"},
                        ]
                    },
                )
                m.get(_NB_STATUSES, payload={"results": [{"id": STATUS_UUID, "name": "Active"}]})
                m.get(
                    _NB_CONTENT_TYPES,
                    payload={
                        "results": [
                            {"id": IFACE_CT_UUID, "app_label": "dcim", "model": "interface"}
                        ]
                    },
                )
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_1})
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(f"{PLUGIN}/overlay-assignments/", payload={"id": ASSIGNMENT_UUID_2})

                result = await env.client.execute_workflow(
                    IBPKeyMemberAddWorkflow.run,
                    IBPKeyMemberAddInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1, GUID_2],
                        guid_memberships=["limited", "full"],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.verified is True
    assert len(put_bodies) == 1
    assert put_bodies[0].get("guids") == [GUID_1, GUID_2]
    assert put_bodies[0].get("memberships") == ["limited", "full"]
    assert "membership" not in put_bodies[0]


@pytest.mark.asyncio
async def test_idempotent_existing_assignments(mock_all_configs, time_skipping_env):
    """Existing OverlayAssignments are reused without creating duplicates."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberAddWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
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
                m.get(_pkey_verify_url("0x0005"), payload={"guids": []})
                m.put(f"{UFM_BASE}/resources/pkeys/", payload={})
                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )
                m.get(
                    _NB_STATUSES,
                    payload={"results": [{"id": STATUS_UUID, "name": "Active"}]},
                )
                m.get(
                    _NB_CONTENT_TYPES,
                    payload={
                        "results": [
                            {
                                "id": IFACE_CT_UUID,
                                "app_label": "dcim",
                                "model": "interface",
                            }
                        ]
                    },
                )
                # Existing assignment returned — no POST should happen
                m.get(
                    _NB_ASSIGNMENTS,
                    payload={"results": [{"id": ASSIGNMENT_UUID_1}]},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberAddWorkflow.run,
                    IBPKeyMemberAddInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        interfaces=[
                            InterfaceRef(device="hca01", interface="mlx5_0"),
                        ],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.assignment_ids == [ASSIGNMENT_UUID_1]
    assert result.members_added == 1


def test_input_rejects_neither():
    """Validator rejects an input with neither interfaces nor GUIDs."""
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey="0x0005",
        )


def test_input_rejects_both():
    """Validator rejects an input with both interfaces and GUIDs populated."""
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey="0x0005",
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guids=[GUID_1],
        )


@pytest.mark.parametrize("bad_pkey", ["", "5", "0x", "0xZZZZ", "0x12345"])
def test_input_rejects_bad_pkey_format(bad_pkey):
    with pytest.raises(ValueError, match="pkey must be hex"):
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=bad_pkey,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        )


def _add_input(**overrides):
    """Build a minimal valid Member Add input, allowing field overrides."""
    params = {
        "host": "ufm.example.com",
        "pkey": "0x0005",
        "interfaces": [InterfaceRef(device="hca01", interface="mlx5_0")],
    }
    params.update(overrides)
    return IBPKeyMemberAddInput(**params)


def test_membership_type_defaults_to_full_when_absent():
    """An absent membership_type uses the PKey default 'full'."""
    assert _add_input().membership_type == "full"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_membership_type_blank_defaults_to_full(blank):
    """A blank/None membership_type no longer crashes; it defaults to 'full'."""
    assert _add_input(membership_type=blank).membership_type == "full"


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [("limited", "limited"), ("LIMITED", "limited"), ("Full", "full")],
)
def test_membership_type_override_is_honored(supplied, expected):
    """A supplied membership_type override is honored (case-insensitive)."""
    assert _add_input(membership_type=supplied).membership_type == expected


def test_membership_type_rejects_invalid():
    """An invalid membership_type is rejected at the input boundary (422)."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _add_input(membership_type="partial")


@pytest.mark.parametrize("bad", [1, True, 1.5])
def test_membership_type_rejects_non_string(bad):
    """Non-string membership_type yields a clean validation error, not a crash."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _add_input(membership_type=bad)


@pytest.mark.asyncio
async def test_guids_only_path(mock_all_configs, time_skipping_env):
    """GUIDs-only input reverse-resolves through Nautobot and completes the workflow."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyMemberAddWorkflow],
            activities=_ALL_ACTIVITIES,
        ):
            with aioresponses() as m:
                stub_graphql_resolve_ib_context(m, pkey="0x0005", overlay_id=OVERLAY_UUID)
                stub_graphql_resolve_guids(m, [(GUID_1, IFACE_UUID_1)])

                m.get(_pkey_verify_url("0x0005"), payload={"guids": []})
                m.put(f"{UFM_BASE}/resources/pkeys/", payload={})

                m.get(
                    _pkey_verify_url("0x0005"),
                    payload={"guids": [{"guid": GUID_1, "membership": "full"}]},
                )

                m.get(
                    _NB_STATUSES,
                    payload={"results": [{"id": STATUS_UUID, "name": "Active"}]},
                )
                m.get(
                    _NB_CONTENT_TYPES,
                    payload={
                        "results": [
                            {"id": IFACE_CT_UUID, "app_label": "dcim", "model": "interface"}
                        ]
                    },
                )
                m.get(_NB_ASSIGNMENTS, payload={"results": []})
                m.post(
                    f"{PLUGIN}/overlay-assignments/",
                    payload={"id": ASSIGNMENT_UUID_1},
                )

                result = await env.client.execute_workflow(
                    IBPKeyMemberAddWorkflow.run,
                    IBPKeyMemberAddInput(
                        host="ufm.example.com",
                        pkey="0x0005",
                        guids=[GUID_1],
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

    assert result.members_added == 1
    assert result.verified is True
    assert result.assignment_ids == [ASSIGNMENT_UUID_1]
