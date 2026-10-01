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
"""Package-owned behavior tests for reconciling InfiniBand PKey members."""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio.worker import Worker

from nv_config_manager_workflows.activities.ib_pkey import ResolvedInterface
from nv_config_manager_workflows.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateInput,
    IBPKeyMemberUpdateOutput,
    IBPKeyMemberUpdateWorkflow,
    InterfaceRef,
    _unresolved_guid_values,
)

from .ib_pkey_fakes import (
    GUID_1,
    GUID_2,
    INTERFACE_ID_1,
    PKEY,
    PKEY_WORKFLOW_ACTIVITIES,
    PKeyRuntime,
    install_pkey_runtime,
)


@pytest.fixture
def pkey_runtime(configured_workflow_runtime: None) -> PKeyRuntime:
    """Install provider-neutral in-memory dependencies."""
    runtime = install_pkey_runtime()
    runtime.ufm.set_pkey(PKEY)
    return runtime


async def _wait_for_pending_approval(handle: Any, timeout: float = 10.0) -> None:
    async def _poll() -> None:
        while await handle.query("pending_approval") is False:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def _execute(
    env: Any,
    workflow_input: IBPKeyMemberUpdateInput,
    *,
    approve: bool = False,
) -> IBPKeyMemberUpdateOutput:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[IBPKeyMemberUpdateWorkflow],
        activities=cast(Sequence[Callable[..., Any]], PKEY_WORKFLOW_ACTIVITIES),
    ):
        handle = await env.client.start_workflow(
            IBPKeyMemberUpdateWorkflow.run,
            workflow_input,
            id=str(uuid.uuid4()),
            task_queue=task_queue,
        )
        if approve:
            await _wait_for_pending_approval(handle)
            await handle.signal("approve", {"stage_name": "validate_diff", "user": "Test"})
        return cast(IBPKeyMemberUpdateOutput, await handle.result())


def test_unresolved_guid_values_detects_missing_removal_resolution() -> None:
    resolved = [
        ResolvedInterface(
            device="hca01",
            interface="mlx5_0",
            interface_id=INTERFACE_ID_1,
            guid=GUID_1.upper(),
        )
    ]

    assert _unresolved_guid_values([GUID_1, GUID_2], resolved) == [GUID_2]


@pytest.mark.asyncio
async def test_additions_only_are_auto_approved(env: Any, pkey_runtime: PKeyRuntime) -> None:
    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
    )

    assert result.members_added == 1
    assert result.members_removed == 0
    assert result.verified is True
    assert pkey_runtime.ufm.pkeys[PKEY].members == {GUID_1: "full"}


@pytest.mark.asyncio
async def test_membership_only_change_is_sent_to_ufm(env: Any, pkey_runtime: PKeyRuntime) -> None:
    assignment_id = pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1, "full")
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"})

    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0", membership="limited")],
        ),
    )

    assert result.members_added == 0
    assert result.members_removed == 0
    assert result.assignment_ids_unchanged == [assignment_id]
    assert pkey_runtime.ufm.put_payloads[-1]["memberships"] == ["limited"]


@pytest.mark.asyncio
async def test_idempotent_update_still_reconciles_exact_ufm_state(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"})

    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
    )

    assert result.members_unchanged == 1
    assert result.verified is True
    assert pkey_runtime.ufm.put_payloads[-1]["guids"] == [GUID_1]


@pytest.mark.asyncio
async def test_full_swap_requires_approval_and_uses_one_atomic_put(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"})

    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_1")],
        ),
        approve=True,
    )

    assert result.members_added == 1
    assert result.members_removed == 1
    assert result.verified is True
    assert len(pkey_runtime.ufm.put_payloads) == 1
    assert pkey_runtime.ufm.put_payloads[0]["guids"] == [GUID_2]


@pytest.mark.asyncio
async def test_guids_only_path_reverse_resolves_dcim_interfaces(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(host="ufm.example.com", pkey=PKEY, guids=[GUID_1]),
    )

    assert result.members_added == 1
    assert result.members_removed == 0
    assert result.verified is True


@pytest.mark.asyncio
async def test_ufm_only_member_triggers_approval(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """An untracked UFM member is gated because the exact-set PUT removes it."""
    pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full", GUID_2: "full"})

    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
        approve=True,
    )

    assert result.members_removed == 0
    assert result.members_unchanged == 1
    assert pkey_runtime.ufm.pkeys[PKEY].members == {GUID_1: "full"}


@pytest.mark.asyncio
async def test_update_preserves_existing_ip_over_ib(env: Any, pkey_runtime: PKeyRuntime) -> None:
    pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"}, ip_over_ib=False)

    result = await _execute(
        env,
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            ip_over_ib=True,
        ),
    )

    assert result.verified is True
    assert pkey_runtime.ufm.put_payloads[-1]["ip_over_ib"] is False


def _update_input(**overrides: Any) -> IBPKeyMemberUpdateInput:
    params: dict[str, Any] = {
        "host": "ufm.example.com",
        "pkey": PKEY,
        "interfaces": [InterfaceRef(device="hca01", interface="mlx5_0")],
    }
    params.update(overrides)
    return IBPKeyMemberUpdateInput(**params)


def test_input_rejects_neither_interfaces_nor_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids'"):
        IBPKeyMemberUpdateInput(host="ufm.example.com", pkey=PKEY)


def test_input_rejects_both_interfaces_and_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids'"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guids=[GUID_1],
        )


@pytest.mark.parametrize("bad_pkey", ["", "5", "0x", "0xZZZZ", "0x12345"])
def test_input_rejects_bad_pkey_format(bad_pkey: str) -> None:
    with pytest.raises(ValueError, match="pkey must be hex"):
        _update_input(pkey=bad_pkey)


def test_input_normalizes_guid_memberships() -> None:
    parsed = IBPKeyMemberUpdateInput(
        host="ufm.example.com",
        pkey=PKEY,
        guids=[GUID_1, GUID_2],
        guid_memberships=["LIMITED", "Full"],
    )
    assert parsed.guid_memberships == ["limited", "full"]


def test_input_rejects_guid_memberships_length_mismatch() -> None:
    with pytest.raises(ValueError, match="same length as guids"):
        IBPKeyMemberUpdateInput(
            host="ufm.example.com",
            pkey=PKEY,
            guids=[GUID_1, GUID_2],
            guid_memberships=["full"],
        )


def test_input_rejects_guid_memberships_with_interfaces() -> None:
    with pytest.raises(ValueError, match="only valid with the 'guids' input"):
        _update_input(guid_memberships=["full"])


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_membership_type_blank_defaults_to_full(blank: object) -> None:
    assert _update_input(membership_type=blank).membership_type == "full"


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [("limited", "limited"), ("LIMITED", "limited"), ("Full", "full")],
)
def test_membership_type_override_is_honored(supplied: str, expected: str) -> None:
    assert _update_input(membership_type=supplied).membership_type == expected


@pytest.mark.parametrize("bad", ["partial", 1, True, 1.5])
def test_membership_type_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _update_input(membership_type=bad)
