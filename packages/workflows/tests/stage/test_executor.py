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
"""How the stage decorator reacts to a stage body that returns or raises."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError, RetryState

from nv_config_manager_workflows.stage import (
    StageMixin,
    StageOutput,
    StageRuntimeFailure,
    StageStateFailure,
    StateEnum,
    stage_executor,
)


class Workflow(StageMixin):
    """A one-stage workflow whose stage body the test supplies."""

    def __init__(
        self,
        body: Callable[["Workflow"], Awaitable[StageOutput]],
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__()
        self._body = body
        self.attempts = 0
        self.define_stage(
            name="render",
            description="Render",
            requires_approval=False,
            depends_on=[],
            retryable=retryable,
        )

    @stage_executor("render")
    async def render(self) -> StageOutput:
        """Run the supplied stage body."""
        self.attempts += 1
        return await self._body(self)


async def succeeds(_workflow: Workflow) -> StageOutput:
    return StageOutput(display="rendered")


def failing(error: Exception) -> Callable[[Workflow], Awaitable[StageOutput]]:
    """Build a stage body that always raises ``error``."""

    async def body(_workflow: Workflow) -> StageOutput:
        raise error

    return body


def failing_once(error: Exception) -> Callable[[Workflow], Awaitable[StageOutput]]:
    """Build a stage body that raises once and then succeeds."""

    async def body(state: Workflow) -> StageOutput:
        if state.attempts == 1:
            raise error
        return StageOutput(display="rendered")

    return body


class TestSuccess:
    async def test_a_completed_stage_records_its_output(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = Workflow(succeeds)

        result = await state.render()

        assert result == StageOutput(display="rendered")
        assert state.get_stage_state("render") == StateEnum.COMPLETE
        assert state.get_stage_by_name("render").output == StageOutput(display="rendered")


class TestFailure:
    async def test_a_state_failure_propagates_untouched(
        self, clock: Any, legacy_history: Any
    ) -> None:
        """An invalid transition is a bug in the workflow, not a retryable stage failure."""
        state = Workflow(failing(StageStateFailure("bad transition")))

        with pytest.raises(StageStateFailure, match="bad transition"):
            await state.render()

        assert state.get_stage_state("render") == StateEnum.IN_PROGRESS

    async def test_a_failed_stage_records_the_traceback(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = Workflow(failing(RuntimeError("boom")), retryable=False)

        with pytest.raises(StageRuntimeFailure):
            await state.render()

        assert state.get_stage_state("render") == StateEnum.FAILED
        recorded = state.get_stage_by_name("render").traceback
        assert recorded is not None
        assert "RuntimeError: boom" in recorded

    async def test_a_non_retryable_stage_fails_the_workflow(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = Workflow(failing(RuntimeError("boom")), retryable=False)

        with pytest.raises(StageRuntimeFailure, match="is non-retryable: boom") as failure:
            await state.render()

        assert failure.value.non_retryable is True

    async def test_terminate_on_failure_fails_the_workflow(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = Workflow(failing(RuntimeError("boom")))
        state.set_terminate_on_failure(True)

        with pytest.raises(StageRuntimeFailure, match="is non-retryable: boom"):
            await state.render()

    async def test_a_non_retryable_application_error_is_not_awaited_for_retry(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = Workflow(failing(ApplicationError("no route to host", non_retryable=True)))

        with pytest.raises(StageRuntimeFailure, match="cannot be retried"):
            await state.render()

    async def test_an_unexpected_exception_is_not_awaited_for_retry(
        self, clock: Any, legacy_history: Any
    ) -> None:
        """A plain exception signals a bug in the stage body, which a retry will not fix."""
        state = Workflow(failing(ValueError("off by one")))

        with pytest.raises(StageRuntimeFailure, match="Unexpected exception"):
            await state.render()


class TestRetry:
    @pytest.mark.parametrize(
        "error",
        [
            ApplicationError("transient"),
            ActivityError(
                "activity retries exhausted",
                scheduled_event_id=1,
                started_event_id=2,
                identity="worker",
                activity_type="test_activity",
                activity_id="activity-1",
                retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
            ),
            ChildWorkflowError(
                "child retries exhausted",
                namespace="default",
                workflow_id="child-1",
                run_id="run-1",
                workflow_type="TestChild",
                initiated_event_id=1,
                started_event_id=2,
                retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
            ),
        ],
    )
    async def test_unretried_failure_closes_workflow_after_three_hours(
        self,
        error: Exception,
        clock: Any,
        patched_history: Any,
        upserted: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exhausted activity/child failures get a bounded manual retry window."""
        state = Workflow(failing(error))

        async def expires(_condition: Any, *, timeout: timedelta) -> None:
            assert state.get_stage_state("render") == StateEnum.FAILED
            assert timeout == timedelta(hours=3)
            clock.advance(timeout.total_seconds())
            raise TimeoutError

        monkeypatch.setattr(workflow, "wait_condition", expires)
        with pytest.raises(StageRuntimeFailure, match="not retried within 10800 seconds") as failure:
            await state.render()

        assert failure.value.type == "StageRetryTimeout"
        assert failure.value.non_retryable is True
        assert failure.value.__cause__ is error
        assert state.get_stage_state("render") == StateEnum.FAILED
        assert state.get_stage_by_name("render").retry_count == 0
        assert state.attempts == 1

    async def test_manual_retry_can_recover_within_the_window(
        self,
        clock: Any,
        patched_history: Any,
        upserted: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A retry signal before the deadline still resumes the failed stage."""
        state = Workflow(failing_once(ApplicationError("transient")))

        async def retry_signal(_condition: Any, *, timeout: timedelta) -> None:
            assert timeout == timedelta(hours=3)
            clock.advance(timedelta(hours=2).total_seconds())
            state.set_stage_state("render", StateEnum.IN_PROGRESS)

        monkeypatch.setattr(workflow, "wait_condition", retry_signal)
        assert await state.render() == StageOutput(display="rendered")
        assert state.get_stage_state("render") == StateEnum.COMPLETE
        assert state.get_stage_by_name("render").retry_count == 1

    async def test_a_retryable_failure_waits_for_the_retry_signal(
        self, clock: Any, legacy_history: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retryable stage parks until a signal returns it to IN_PROGRESS."""
        state = Workflow(failing_once(ApplicationError("transient")))

        async def retry_signal(_condition: Any) -> None:
            state.set_stage_state("render", StateEnum.IN_PROGRESS)

        monkeypatch.setattr(workflow, "wait_condition", retry_signal)

        result = await state.render()

        assert result == StageOutput(display="rendered")
        assert state.get_stage_state("render") == StateEnum.COMPLETE
        assert state.get_stage_by_name("render").retry_count == 1
        assert state.attempts == 2
