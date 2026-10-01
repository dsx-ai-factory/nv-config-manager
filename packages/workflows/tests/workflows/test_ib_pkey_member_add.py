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
"""Package-owned behavior tests for adding InfiniBand PKey members."""

import uuid
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio.worker import Worker

from nv_config_manager_workflows.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddOutput,
    IBPKeyMemberAddWorkflow,
    InterfaceRef,
)

from .ib_pkey_fakes import (
    GUID_1,
    GUID_2,
    INTERFACE_ID_1,
    OVERLAY_ID,
    OVERLAY_NAME,
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


async def _execute(env: Any, workflow_input: IBPKeyMemberAddInput) -> IBPKeyMemberAddOutput:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[IBPKeyMemberAddWorkflow],
        activities=cast(Sequence[Callable[..., Any]], PKEY_WORKFLOW_ACTIVITIES),
    ):
        return cast(
            IBPKeyMemberAddOutput,
            await env.client.execute_workflow(
                IBPKeyMemberAddWorkflow.run,
                workflow_input,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            ),
        )


@pytest.mark.asyncio
async def test_add_members_full_workflow(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """Two provider-resolved interfaces are added and recorded."""
    result = await _execute(
        env,
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[
                InterfaceRef(device="hca01", interface="mlx5_0"),
                InterfaceRef(device="hca01", interface="mlx5_1"),
            ],
        ),
    )

    assert result == IBPKeyMemberAddOutput(
        pkey=PKEY,
        overlay_id=OVERLAY_ID,
        overlay_name=OVERLAY_NAME,
        members_added=2,
        verified=True,
        assignment_ids=["assignment-001", "assignment-002"],
    )
    assert pkey_runtime.ufm.pkeys[PKEY].members == {GUID_1: "full", GUID_2: "full"}


@pytest.mark.asyncio
async def test_add_preserves_per_interface_membership(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """Per-interface membership is sent in the atomic UFM update."""
    result = await _execute(
        env,
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[
                InterfaceRef(device="hca01", interface="mlx5_0"),
                InterfaceRef(device="hca01", interface="mlx5_1", membership="limited"),
            ],
        ),
    )

    assert result.verified is True
    assert pkey_runtime.ufm.put_payloads == [
        {
            "pkey": PKEY,
            "guids": [GUID_1, GUID_2],
            "memberships": ["full", "limited"],
            "ip_over_ib": True,
        }
    ]


@pytest.mark.asyncio
async def test_add_guids_only_preserves_index_aligned_membership(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """GUID input reverse-resolves through the fake DCIM provider."""
    result = await _execute(
        env,
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=PKEY,
            guids=[GUID_1, GUID_2],
            guid_memberships=["limited", "full"],
        ),
    )

    assert result.verified is True
    assert pkey_runtime.ufm.pkeys[PKEY].members == {
        GUID_1: "limited",
        GUID_2: "full",
    }


@pytest.mark.asyncio
async def test_add_reuses_existing_assignment(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """An existing provider assignment is returned without duplication."""
    pkey_runtime.dcim.seed_assignment(
        INTERFACE_ID_1,
        GUID_1,
        assignment_id="existing-assignment",
    )

    result = await _execute(
        env,
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
    )

    assert result.assignment_ids == ["existing-assignment"]
    assert len(pkey_runtime.dcim.assignments) == 1


def _add_input(**overrides: Any) -> IBPKeyMemberAddInput:
    params: dict[str, Any] = {
        "host": "ufm.example.com",
        "pkey": PKEY,
        "interfaces": [InterfaceRef(device="hca01", interface="mlx5_0")],
    }
    params.update(overrides)
    return IBPKeyMemberAddInput(**params)


def test_input_rejects_neither_interfaces_nor_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberAddInput(host="ufm.example.com", pkey=PKEY)


def test_input_rejects_both_interfaces_and_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberAddInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guids=[GUID_1],
        )


@pytest.mark.parametrize("bad_pkey", ["", "5", "0x", "0xZZZZ", "0x12345"])
def test_input_rejects_bad_pkey_format(bad_pkey: str) -> None:
    with pytest.raises(ValueError, match="pkey must be hex"):
        _add_input(pkey=bad_pkey)


def test_membership_type_defaults_to_full() -> None:
    assert _add_input().membership_type == "full"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_membership_type_blank_defaults_to_full(blank: object) -> None:
    assert _add_input(membership_type=blank).membership_type == "full"


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [("limited", "limited"), ("LIMITED", "limited"), ("Full", "full")],
)
def test_membership_type_override_is_honored(supplied: str, expected: str) -> None:
    assert _add_input(membership_type=supplied).membership_type == expected


@pytest.mark.parametrize("bad", ["partial", 1, True, 1.5])
def test_membership_type_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        _add_input(membership_type=bad)
