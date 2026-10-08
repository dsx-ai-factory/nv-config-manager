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
"""The stage state machine: which transitions are legal and what they record."""

from typing import Any

import pytest

from nv_config_manager_workflows.stage import (
    HistoryEntry,
    Review,
    Stage,
    StageStateFailure,
    StateEnum,
)

# Ordered state pairs the transition table rejects, covering every source state.
ILLEGAL_TRANSITIONS = [
    (StateEnum.NOT_STARTED, StateEnum.COMPLETE),
    (StateEnum.NOT_STARTED, StateEnum.FAILED),
    (StateEnum.NOT_STARTED, StateEnum.APPROVED),
    (StateEnum.IN_PROGRESS, StateEnum.NOT_STARTED),
    (StateEnum.IN_PROGRESS, StateEnum.APPROVED),
    (StateEnum.IN_PROGRESS, StateEnum.UNREACHABLE),
    (StateEnum.PENDING_APPROVAL, StateEnum.COMPLETE),
    (StateEnum.PENDING_APPROVAL, StateEnum.FAILED),
    (StateEnum.COMPLETE, StateEnum.IN_PROGRESS),
    (StateEnum.UNREACHABLE, StateEnum.IN_PROGRESS),
    (StateEnum.FAILED, StateEnum.COMPLETE),
]


def build_stage(
    state: StateEnum = StateEnum.NOT_STARTED,
    *,
    requires_approval: bool = False,
    approval_threshold: int = 0,
    retryable: bool = True,
) -> Stage:
    """Build a stage sitting in a given state with its history seeded."""
    return Stage(
        name="deploy",
        description="Deploy the rendered configuration",
        requires_approval=requires_approval,
        state=state,
        depends_on=[],
        approval_threshold=approval_threshold,
        state_history=[HistoryEntry(state=state, time=0.0)],
        retryable=retryable,
        traceback=None,
    )


class TestTransitions:
    def test_a_legal_transition_records_state_and_history(self, clock: Any) -> None:
        stage = build_stage()
        clock.advance(5)

        stage.transition(StateEnum.IN_PROGRESS)

        assert stage.state == StateEnum.IN_PROGRESS
        assert stage.state_history[-1] == HistoryEntry(state=StateEnum.IN_PROGRESS, time=5.0)

    @pytest.mark.parametrize(("start", "target"), ILLEGAL_TRANSITIONS)
    def test_an_illegal_transition_is_rejected(self, start: StateEnum, target: StateEnum) -> None:
        stage = build_stage(start)

        with pytest.raises(StageStateFailure, match=f"Invalid transition from {start}"):
            stage.transition(target)

        assert stage.state == start

    def test_a_rejected_transition_leaves_no_history(self) -> None:
        stage = build_stage(StateEnum.COMPLETE)

        with pytest.raises(StageStateFailure):
            stage.transition(StateEnum.IN_PROGRESS)

        assert len(stage.state_history) == 1

    def test_an_approval_stage_cannot_complete_unreviewed(self) -> None:
        stage = build_stage(StateEnum.IN_PROGRESS, requires_approval=True, approval_threshold=1)

        with pytest.raises(StageStateFailure, match="must be reviewed before completion"):
            stage.transition(StateEnum.COMPLETE)

    def test_retrying_a_failed_stage_clears_the_previous_traceback(self, clock: Any) -> None:
        stage = build_stage(StateEnum.FAILED)
        stage.traceback = "Traceback (most recent call last): ..."

        stage.transition(StateEnum.IN_PROGRESS)

        assert stage.traceback is None


class TestReview:
    def test_approvals_below_the_threshold_leave_the_stage_pending(self, clock: Any) -> None:
        stage = build_stage(
            StateEnum.PENDING_APPROVAL, requires_approval=True, approval_threshold=2
        )

        stage.approve("first-reviewer")

        assert stage.state == StateEnum.PENDING_APPROVAL
        assert stage.approvers == [Review(user="first-reviewer", time=0.0)]

    def test_reaching_the_threshold_approves_the_stage(self, clock: Any) -> None:
        stage = build_stage(
            StateEnum.PENDING_APPROVAL, requires_approval=True, approval_threshold=2
        )

        stage.approve("first-reviewer")
        stage.approve("second-reviewer")

        assert stage.state == StateEnum.APPROVED

    def test_an_approved_stage_may_complete(self, clock: Any) -> None:
        stage = build_stage(
            StateEnum.PENDING_APPROVAL, requires_approval=True, approval_threshold=1
        )

        stage.approve("reviewer")
        stage.transition(StateEnum.COMPLETE)

        assert stage.state == StateEnum.COMPLETE

    def test_one_rejection_rejects_the_stage(self, clock: Any) -> None:
        stage = build_stage(
            StateEnum.PENDING_APPROVAL, requires_approval=True, approval_threshold=2
        )

        stage.reject("reviewer")

        assert stage.state == StateEnum.REJECTED
        assert stage.rejecters == [Review(user="reviewer", time=0.0)]

    @pytest.mark.parametrize("review", ["approve", "reject"])
    def test_reviewing_a_stage_that_is_not_pending_is_rejected(self, review: str) -> None:
        stage = build_stage(StateEnum.IN_PROGRESS)

        with pytest.raises(StageStateFailure, match="is not pending approval"):
            getattr(stage, review)("reviewer")


class TestExecutionTime:
    def test_a_running_stage_reports_no_execution_time(self, clock: Any) -> None:
        stage = build_stage()
        stage.transition(StateEnum.IN_PROGRESS)

        assert stage.execution_time is None

    def test_execution_time_spans_the_last_run_to_the_terminal_state(self, clock: Any) -> None:
        stage = build_stage()
        clock.advance(10)
        stage.transition(StateEnum.IN_PROGRESS)
        clock.advance(30)
        stage.transition(StateEnum.COMPLETE)

        assert stage.execution_time == 30.0

    def test_execution_time_measures_the_retry_not_the_original_attempt(self, clock: Any) -> None:
        """A retried stage reports its latest attempt, not the one that failed."""
        stage = build_stage()
        stage.transition(StateEnum.IN_PROGRESS)
        clock.advance(100)
        stage.transition(StateEnum.FAILED)
        clock.advance(5)
        stage.transition(StateEnum.IN_PROGRESS)
        clock.advance(2)
        stage.transition(StateEnum.COMPLETE)

        assert stage.execution_time == 2.0


class TestTimeSerialization:
    def test_times_serialize_as_utc_isoformat(self) -> None:
        """Temporal rejects datetime fields, so times travel as floats and render as text."""
        entry = HistoryEntry(state=StateEnum.COMPLETE, time=0.0)

        assert entry.model_dump(mode="json")["time"] == "1970-01-01T00:00:00+00:00"

    def test_isoformat_input_is_accepted_back(self) -> None:
        entry = HistoryEntry.model_validate(
            {"state": StateEnum.COMPLETE, "time": "1970-01-01T00:00:00+00:00"}
        )

        assert entry.time == 0.0
