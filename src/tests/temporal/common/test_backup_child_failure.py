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
"""Backup parents must observe failed child results before reporting success."""

import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import SimpleNamespace
from typing import Any, NoReturn
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError, ChildWorkflowError, RetryState
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from nv_config_manager_workflows.decorators import run_nv_config_manager_workflow
from nv_config_manager_workflows.retry_timeout import DEFAULT_FAILED_STAGE_RETRY_TIMEOUT_SECONDS
from nv_config_manager_workflows.stage import StateEnum
from nv_config_manager_workflows.workflows.backup import BACKUP_CHILD_RESULT_PATCH_ID, BackupInput
from nv_config_manager_workflows.workflows.deploy import DeployWorkflow, TenantDeployWorkflow
from nv_config_manager_workflows.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.os_upgrade import SwitchOSUpgradeWorkflow
from nv_config_manager_workflows.workflows.reprovision import ReprovisionWorkflow


@pytest.mark.parametrize(
    "parent",
    [
        DeployWorkflow,
        TenantDeployWorkflow,
        DevicePasswordRotationWorkflow,
        NVLinkSwitchFirmwareUpgradeWorkflow,
        SwitchOSUpgradeWorkflow,
        ReprovisionWorkflow,
    ],
)
@pytest.mark.parametrize("retry_state", [RetryState.MAXIMUM_ATTEMPTS_REACHED, RetryState.TIMEOUT])
async def test_backup_stage_propagates_child_failure(
    monkeypatch: pytest.MonkeyPatch, parent: type[Any], retry_state: RetryState
) -> None:
    """A done child that failed or timed out never becomes a successful backup."""
    error = ChildWorkflowError(
        "backup did not complete",
        namespace="default",
        workflow_id="backup-child",
        run_id="backup-run",
        workflow_type="BackupWorkflow",
        initiated_event_id=1,
        started_event_id=2,
        retry_state=retry_state,
    )

    class FailedHandle:
        id = "backup-child"

        def done(self) -> bool:
            return True

        async def result(self) -> NoReturn:
            raise error

        def __await__(self) -> Generator[Any, None, NoReturn]:
            return self.result().__await__()

    monkeypatch.setattr(workflow, "time", lambda: 0.0)
    monkeypatch.setattr(workflow, "patched", lambda patch: patch == BACKUP_CHILD_RESULT_PATCH_ID)
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="parent-workflow"))
    monkeypatch.setattr(workflow, "start_child_workflow", AsyncMock(return_value=FailedHandle()))
    monkeypatch.setattr(workflow, "execute_activity", AsyncMock(return_value="https://example.com"))
    wait = AsyncMock()
    monkeypatch.setattr(workflow, "wait_condition", wait)
    state = parent()
    monkeypatch.setattr(
        state, "_get_ui_base_url", AsyncMock(return_value="https://example.com"), raising=False
    )
    stage_input = parent.BackupStageInput(device_id="device-1", commit_id="commit-1")
    # Let the shared stage executor handle this error in production; here verify
    # each stage body actually surfaces it rather than only inspecting done().
    with pytest.raises(ChildWorkflowError) as caught:
        await parent.perform_backup.__wrapped__(state, stage_input)
    assert caught.value is error
    wait.assert_not_awaited()


_BACKUP_ATTEMPTS: dict[str, int] = {}


@workflow.defn(name="BackupWorkflow")
class _FailFirstBackup:
    """Stand-in BackupWorkflow: a parent's first backup fails and later ones succeed."""

    @workflow.run
    async def run(self, workflow_input: BackupInput) -> bool:
        attempts = _BACKUP_ATTEMPTS.get(workflow_input.workflow_id, 0) + 1
        _BACKUP_ATTEMPTS[workflow_input.workflow_id] = attempts
        if attempts == 1:
            raise ApplicationError("backup could not reach the device", non_retryable=True)
        return True


@workflow.defn
class _DeployBackupProbe(DeployWorkflow):
    """Run DeployWorkflow's real perform_backup stage through the stage executor."""

    def __init__(self) -> None:
        super().__init__()
        # Only the backup stage runs here, so drop its dependency on the earlier stages.
        self.get_stage_by_name("perform_backup").depends_on = []

    @run_nv_config_manager_workflow
    async def run(self, device_id: str) -> str:  # type: ignore[override]
        await self.perform_backup(
            DeployWorkflow.BackupStageInput(device_id=device_id, commit_id="commit-1")
        )
        return "backed up"


class _ProbeRun:
    """A started probe workflow with its worker running."""

    def __init__(self, handle: WorkflowHandle[Any, Any]) -> None:
        self.handle = handle

    async def wait_for_failed_backup_stage(self) -> None:
        async with asyncio.timeout(30):
            while True:
                stages = await self.handle.query("stages")
                stage = next(stage for stage in stages if stage["name"] == "perform_backup")
                if stage["state"] == StateEnum.FAILED:
                    return
                await asyncio.sleep(0.05)

    async def event_types(self) -> list[int]:
        history = await self.handle.fetch_history()
        return [event.event_type for event in history.events]

    async def retry_timers(self) -> list[int]:
        history = await self.handle.fetch_history()
        return [
            event.timer_started_event_attributes.start_to_fire_timeout.seconds
            for event in history.events
            if event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
        ]


@asynccontextmanager
async def _run_probe(
    env: WorkflowEnvironment,
) -> AsyncIterator[_ProbeRun]:
    queue = str(uuid4())
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[_DeployBackupProbe, _FailFirstBackup],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await env.client.start_workflow(
            _DeployBackupProbe.run, "device-1", id=str(uuid4()), task_queue=queue
        )
        yield _ProbeRun(handle)


async def test_failed_backup_child_fails_the_stage_and_retry_starts_a_new_backup(
    time_skipping_env: Callable[[], AbstractAsyncContextManager[WorkflowEnvironment]],
) -> None:
    """A failed backup child leaves the stage FAILED and retryable; Retry backs up again."""
    async with time_skipping_env() as env:
        async with _run_probe(env) as probe:
            await probe.wait_for_failed_backup_stage()
            events = await probe.event_types()
            assert EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED in events
            assert await probe.retry_timers() == [DEFAULT_FAILED_STAGE_RETRY_TIMEOUT_SECONDS]

            await probe.handle.signal("retry", "perform_backup")

            assert await probe.handle.result() == "backed up"
            events = await probe.event_types()
            child_starts = events.count(
                EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
            )
            assert child_starts == 2


async def test_failed_backup_child_closes_the_workflow_when_not_retried(
    time_skipping_env: Callable[[], AbstractAsyncContextManager[WorkflowEnvironment]],
) -> None:
    """Without a retry, the backup stage's window expires and the workflow fails."""
    async with time_skipping_env() as env:
        async with _run_probe(env) as probe:
            await probe.wait_for_failed_backup_stage()

            with pytest.raises(WorkflowFailureError) as caught:
                await probe.handle.result()

            causes: list[BaseException] = []
            cause: BaseException | None = caught.value.cause
            while cause is not None:
                causes.append(cause)
                cause = cause.__cause__ or getattr(cause, "cause", None)
            assert any(getattr(c, "type", None) == "StageRetryTimeout" for c in causes)
            assert any(isinstance(c, ChildWorkflowError) for c in causes)
