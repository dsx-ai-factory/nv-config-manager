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
"""Package-owned behavior tests for deleting InfiniBand PKey members."""

import uuid
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio.worker import Worker

from nv_config_manager_workflows.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteInput,
    IBPKeyMemberDeleteOutput,
    IBPKeyMemberDeleteWorkflow,
    InterfaceRef,
)

from .ib_pkey_fakes import (
    GUID_1,
    GUID_2,
    INTERFACE_ID_1,
    INTERFACE_ID_2,
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
    return install_pkey_runtime()


async def _execute(env: Any, workflow_input: IBPKeyMemberDeleteInput) -> IBPKeyMemberDeleteOutput:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[IBPKeyMemberDeleteWorkflow],
        activities=cast(Sequence[Callable[..., Any]], PKEY_WORKFLOW_ACTIVITIES),
    ):
        return cast(
            IBPKeyMemberDeleteOutput,
            await env.client.execute_workflow(
                IBPKeyMemberDeleteWorkflow.run,
                workflow_input,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            ),
        )


@pytest.mark.asyncio
async def test_delete_members_full_workflow(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """Removing every tracked member reconciles both providers."""
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full", GUID_2: "full"})
    first = pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)
    second = pkey_runtime.dcim.seed_assignment(INTERFACE_ID_2, GUID_2)

    result = await _execute(
        env,
        IBPKeyMemberDeleteInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[
                InterfaceRef(device="hca01", interface="mlx5_0"),
                InterfaceRef(device="hca01", interface="mlx5_1"),
            ],
        ),
    )

    assert result == IBPKeyMemberDeleteOutput(
        pkey=PKEY,
        overlay_id=OVERLAY_ID,
        overlay_name=OVERLAY_NAME,
        members_removed=2,
        verified=True,
        assignment_ids_removed=[first, second],
        interface_ids_not_assigned=[],
        partition_empty=True,
        pkey_deleted=True,
        overlay_deleted=True,
    )


@pytest.mark.asyncio
async def test_delete_is_idempotent_when_assignment_is_missing(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """An absent DCIM assignment is reported without failing the UFM removal."""
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"})

    result = await _execute(
        env,
        IBPKeyMemberDeleteInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
    )

    assert result.verified is True
    assert result.assignment_ids_removed == []
    assert result.interface_ids_not_assigned == [INTERFACE_ID_1]


@pytest.mark.asyncio
async def test_delete_guids_only_reverse_resolves_provider_records(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """GUID-only input resolves the interface before removing its assignment."""
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full"})
    assignment_id = pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)

    result = await _execute(
        env,
        IBPKeyMemberDeleteInput(host="ufm.example.com", pkey=PKEY, guids=[GUID_1]),
    )

    assert result.assignment_ids_removed == [assignment_id]
    assert result.members_removed == 1


@pytest.mark.asyncio
async def test_untracked_ufm_member_blocks_partition_cleanup(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """A remaining UFM-only member prevents deletion of the DCIM PKey."""
    pkey_runtime.ufm.set_pkey(PKEY, members={GUID_1: "full", GUID_2: "full"})
    pkey_runtime.dcim.seed_assignment(INTERFACE_ID_1, GUID_1)

    result = await _execute(
        env,
        IBPKeyMemberDeleteInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        ),
    )

    assert result.verified is True
    assert result.partition_empty is False
    assert result.pkey_deleted is False
    assert result.overlay_deleted is False
    assert pkey_runtime.ufm.pkeys[PKEY].members == {GUID_2: "full"}


def test_input_rejects_neither_interfaces_nor_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberDeleteInput(host="ufm.example.com", pkey=PKEY)


def test_input_rejects_both_interfaces_and_guids() -> None:
    with pytest.raises(ValueError, match="One of 'interfaces' or 'guids' must be provided"):
        IBPKeyMemberDeleteInput(
            host="ufm.example.com",
            pkey=PKEY,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
            guids=[GUID_1],
        )


@pytest.mark.parametrize("bad_pkey", ["", "5", "0x", "0xZZZZ", "0x12345"])
def test_input_rejects_bad_pkey_format(bad_pkey: str) -> None:
    with pytest.raises(ValueError, match="pkey must be hex"):
        IBPKeyMemberDeleteInput(
            host="ufm.example.com",
            pkey=bad_pkey,
            interfaces=[InterfaceRef(device="hca01", interface="mlx5_0")],
        )
