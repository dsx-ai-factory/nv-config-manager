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
"""Package-owned behavior tests for the InfiniBand PKey creation workflow."""

import uuid
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio.worker import Worker

from nv_config_manager_workflows.workflows.ib_pkey_creation import (
    IBPKeyCreationInput,
    IBPKeyCreationWorkflow,
    IBPKeyCreationWorkflowOutput,
)

from .ib_pkey_fakes import PKEY_WORKFLOW_ACTIVITIES, PKeyRuntime, install_pkey_runtime


@pytest.fixture
def pkey_runtime(configured_workflow_runtime: None) -> PKeyRuntime:
    """Install provider-neutral in-memory dependencies."""
    return install_pkey_runtime()


async def _execute(env: Any, workflow_input: IBPKeyCreationInput) -> IBPKeyCreationWorkflowOutput:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[IBPKeyCreationWorkflow],
        activities=cast(Sequence[Callable[..., Any]], PKEY_WORKFLOW_ACTIVITIES),
    ):
        return cast(
            IBPKeyCreationWorkflowOutput,
            await env.client.execute_workflow(
                IBPKeyCreationWorkflow.run,
                workflow_input,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            ),
        )


def test_creation_output_accepts_legacy_pkey_identifier() -> None:
    """Results written before the neutral alias remain deserializable."""
    result = IBPKeyCreationWorkflowOutput.model_validate(
        {
            "pkey": "0x1234",
            "auto_assigned": False,
            "created": True,
            "verified": True,
            "pkey_data": {},
            "nautobot_pkey_id": "pkey-1",
        }
    )

    assert result.dcim_pkey_id == "pkey-1"


@pytest.mark.asyncio
async def test_creation_with_specific_pkey(env: Any, pkey_runtime: PKeyRuntime) -> None:
    """A caller-selected PKey is created, verified, and recorded through providers."""
    result = await _execute(
        env,
        IBPKeyCreationInput(host="ufm.example.com", pkey="0x8001"),
    )

    assert result.pkey == "0x8001"
    assert result.auto_assigned is False
    assert result.created is True
    assert result.verified is True
    assert result.dcim_pkey_id == "pkey-1"
    assert result.nautobot_pkey_id == "pkey-1"
    assert "0x8001" in pkey_runtime.ufm.pkeys


@pytest.mark.asyncio
async def test_creation_auto_assigns_first_available_pkey(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """An omitted PKey selects the lowest non-reserved value."""
    pkey_runtime.ufm.set_pkey("0x7fff")

    result = await _execute(env, IBPKeyCreationInput(host="ufm.example.com"))

    assert result.pkey == "0x0001"
    assert result.auto_assigned is True
    assert result.verified is True
    assert "0x0001" in pkey_runtime.ufm.pkeys


@pytest.mark.asyncio
async def test_creation_reuses_existing_orphan_dcim_record(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """Recording an existing orphan PKey preserves its provider identifier."""
    pkey_runtime.dcim.pkey_ids["0x8001"] = "existing-pkey-id"

    result = await _execute(
        env,
        IBPKeyCreationInput(host="ufm.example.com", pkey="0x8001"),
    )

    assert result.dcim_pkey_id == "existing-pkey-id"


@pytest.mark.asyncio
async def test_creation_site_override_skips_dcim_site_resolution(
    env: Any, pkey_runtime: PKeyRuntime
) -> None:
    """An explicit site bypasses the workflow's site-resolution activity."""
    result = await _execute(
        env,
        IBPKeyCreationInput(
            host="ufm.example.com",
            pkey="0x8001",
            site="explicit-site",
        ),
    )

    assert result.verified is True
    assert pkey_runtime.dcim.resolve_site_calls == 0
