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
"""Exercise the failed-stage retry deadline with Temporal's durable timers."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from nv_config_manager_workflows.decorators import run_nv_config_manager_workflow
from nv_config_manager_workflows.stage import StageMixin, StageOutput, StateEnum, stage_executor

pytestmark = pytest.mark.timeout(120)


@activity.defn
async def _fails_until_retries_exhausted() -> None:
    raise ApplicationError("transient activity failure")


@workflow.defn
class _FailingChild:
    @workflow.run
    async def run(self, time_out: bool = False) -> None:
        if time_out:
            await workflow.sleep(timedelta(seconds=10))
        raise ApplicationError("transient child failure")


@workflow.defn
class _FailedStageProbe(StageMixin):
    """Fail an activity or child, then wait for a manual stage retry."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0
        self.define_stage("probe", "Probe", requires_approval=False, depends_on=[])

    @stage_executor("probe")
    async def probe(self) -> StageOutput:
        self.attempts += 1
        if self.attempts > 1:
            return StageOutput(display="recovered")
        policy = RetryPolicy(maximum_attempts=2)
        if self.mode in {"child", "child-timeout"}:
            await workflow.execute_child_workflow(
                _FailingChild.run,
                self.mode == "child-timeout",
                run_timeout=timedelta(seconds=1) if self.mode == "child-timeout" else None,
                retry_policy=policy,
            )
        else:
            await workflow.execute_activity(
                _fails_until_retries_exhausted,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=policy,
            )
        return StageOutput(display="unexpected success")

    @run_nv_config_manager_workflow
    async def run(self, mode: str) -> str:
        self.mode = mode
        if mode == "delayed-activity":
            await workflow.sleep(timedelta(hours=4))
        await self.probe()
        return "recovered"


@pytest.mark.parametrize("mode", ["activity", "child", "child-timeout", "delayed-activity"])
async def test_exhausted_retries_close_as_failed_after_three_hours(time_skipping_env, mode):
    """The server records a three-hour timer followed by a non-retryable failure."""
    async with time_skipping_env() as env:
        queue = str(uuid4())
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_FailedStageProbe, _FailingChild],
            activities=[_fails_until_retries_exhausted],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _FailedStageProbe.run,
                mode,
                id=str(uuid4()),
                task_queue=queue,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            with pytest.raises(WorkflowFailureError) as caught:
                await handle.result()
            assert caught.value.cause.non_retryable is True
            assert caught.value.cause.cause.type == "StageRetryTimeout"
            assert (await handle.describe()).status == WorkflowExecutionStatus.FAILED
            history = await handle.fetch_history()
            timers = [
                event
                for event in history.events
                if event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                and event.timer_started_event_attributes.start_to_fire_timeout.seconds == 10800
            ]
            assert len(timers) == 1
            elapsed = history.events[-1].event_time.ToDatetime() - timers[0].event_time.ToDatetime()
            assert elapsed >= timedelta(hours=3)
            if mode == "delayed-activity":
                before_failure = (
                    timers[0].event_time.ToDatetime() - history.events[0].event_time.ToDatetime()
                )
                assert before_failure >= timedelta(hours=4)


async def test_manual_retry_before_deadline_completes_and_cancels_timer(time_skipping_env):
    """Successful recovery before three hours does not later fail the workflow."""
    async with time_skipping_env() as env:
        queue = str(uuid4())
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_FailedStageProbe],
            activities=[_fails_until_retries_exhausted],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _FailedStageProbe.run, "activity", id=str(uuid4()), task_queue=queue
            )
            async with asyncio.timeout(10):
                while (await handle.query("stages"))[0]["state"] != StateEnum.FAILED:
                    await asyncio.sleep(0.01)
            await env.sleep(timedelta(hours=2))
            assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
            await handle.signal("retry", "probe")
            assert await handle.result() == "recovered"
            assert (await handle.describe()).status == WorkflowExecutionStatus.COMPLETED
            history = await handle.fetch_history()
            assert any(
                event.event_type == EventType.EVENT_TYPE_TIMER_CANCELED for event in history.events
            )


async def test_retry_deadline_survives_worker_restart(time_skipping_env):
    """Replaying a parked workflow preserves its original durable retry timer."""
    async with time_skipping_env() as env:
        queue = str(uuid4())
        options = {
            "task_queue": queue,
            "workflows": [_FailedStageProbe],
            "activities": [_fails_until_retries_exhausted],
            "workflow_runner": UnsandboxedWorkflowRunner(),
            # Avoid the time-skipping server's dead sticky-queue wait and
            # require a full history replay by the replacement worker.
            "max_cached_workflows": 0,
        }
        async with Worker(env.client, **options):
            handle = await env.client.start_workflow(
                _FailedStageProbe.run, "activity", id=str(uuid4()), task_queue=queue
            )
            async with asyncio.timeout(10):
                while (await handle.query("stages"))[0]["state"] != StateEnum.FAILED:
                    await asyncio.sleep(0.01)
            await env.sleep(timedelta(hours=2))
        restarted_at = await env.get_current_time()
        async with Worker(env.client, **options):
            async with asyncio.timeout(20):
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
            assert (await handle.describe()).status == WorkflowExecutionStatus.FAILED
            history = await handle.fetch_history()
            closed_at = history.events[-1].event_time.ToDatetime()
            assert closed_at - restarted_at.replace(tzinfo=None) < timedelta(hours=2)
            assert (
                sum(
                    event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                    and event.timer_started_event_attributes.start_to_fire_timeout.seconds == 10800
                    for event in history.events
                )
                == 1
            )
