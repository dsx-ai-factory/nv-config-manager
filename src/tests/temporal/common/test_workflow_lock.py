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
"""End-to-end coverage of the metadata-driven workflow lock against a real worker.

The lock spec, key building, activities and decorator wiring are unit-tested in
the workflow package (``packages/workflows/tests``). What can only be shown here
is the whole chain running under a Temporal worker: acquire before the body,
release after -- even when the body fails.
"""

import uuid

import pytest
from pydantic import BaseModel
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from nv_config_manager_workflows.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager_workflows.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager_workflows.metadata import WorkflowLockSpec, WorkflowMetadataMixin


class _ProbeInput(BaseModel):
    resource: str
    fail: bool = False


@workflow.defn
class _LockedProbeWorkflow(WorkflowMetadataMixin):
    """Minimal workflow that opts into the per-resource lock."""

    workflow_name = "Locked Probe"
    workflow_namespace = "test"
    workflow_lock = WorkflowLockSpec(
        key_fields=["resource"], ttl_seconds=60, renew_interval_seconds=10
    )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        """Return a marker, or blow up when asked to."""
        if workflow_input.fail:
            raise ApplicationError("probe failure", non_retryable=True)
        return f"ran:{workflow_input.resource}"


async def _run_probe(env_factory, probe_input: _ProbeInput) -> str:
    task_queue = str(uuid.uuid4())
    async with env_factory() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[_LockedProbeWorkflow],
            activities=REGISTERED_COMMON_ACTIVITIES,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _LockedProbeWorkflow.run,
                probe_input,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            )


@pytest.mark.asyncio
async def test_lock_acquired_and_released_around_run(time_skipping_env, disable_workflow_lock_io):
    """A locked workflow acquires the keyed lock, runs, then releases it."""
    result = await _run_probe(time_skipping_env, _ProbeInput(resource="r1"))

    assert result == "ran:r1"
    acquire = disable_workflow_lock_io["acquire_lock"]
    release = disable_workflow_lock_io["release_lock"]
    assert acquire.await_count == 1
    assert acquire.await_args.args[0] == "wf-lock:test:resource=r1"
    assert release.await_count == 1
    assert release.await_args.args[0] == "wf-lock:test:resource=r1"


@pytest.mark.asyncio
async def test_lock_released_even_when_run_fails(time_skipping_env, disable_workflow_lock_io):
    """The lock is released in the finally path when the body raises."""
    with pytest.raises(WorkflowFailureError):
        await _run_probe(time_skipping_env, _ProbeInput(resource="r2", fail=True))

    release = disable_workflow_lock_io["release_lock"]
    assert release.await_count == 1
    assert release.await_args.args[0] == "wf-lock:test:resource=r2"
