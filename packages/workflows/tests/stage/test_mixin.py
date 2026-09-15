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
"""Stage definition, dependency rules, signals and search-attribute indexing."""

from typing import Any

import pytest
from pydantic import BaseModel

from nv_config_manager_workflows.search_attributes import (
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
)
from nv_config_manager_workflows.stage import (
    ReviewSignalInput,
    StageMixin,
    StageStateFailure,
    StageWorkflowInput,
    StateEnum,
)


def build_workflow(clock: Any) -> StageMixin:
    """Build a stage workflow with a render stage feeding a deploy stage."""
    state = StageMixin()
    state.define_stage(name="render", description="Render", requires_approval=False, depends_on=[])
    state.define_stage(
        name="deploy", description="Deploy", requires_approval=False, depends_on=["render"]
    )
    return state


class TestDefineStage:
    def test_a_stage_starts_not_started_with_seeded_history(self, clock: Any) -> None:
        state = build_workflow(clock)

        stage = state.get_stage_by_name("render")
        assert stage.state == StateEnum.NOT_STARTED
        assert [entry.state for entry in stage.state_history] == [StateEnum.NOT_STARTED]

    def test_a_duplicate_stage_name_is_rejected(self, clock: Any) -> None:
        state = build_workflow(clock)

        with pytest.raises(StageStateFailure, match="Stage already defined with name render"):
            state.define_stage(
                name="render", description="Render again", requires_approval=False, depends_on=[]
            )

    def test_a_dependency_on_an_undefined_stage_is_rejected(self, clock: Any) -> None:
        state = build_workflow(clock)

        with pytest.raises(StageStateFailure, match="Stage verify in depends_on does not exist"):
            state.define_stage(
                name="report", description="Report", requires_approval=False, depends_on=["verify"]
            )

    def test_an_approval_stage_needs_a_positive_threshold(self, clock: Any) -> None:
        state = StageMixin()

        with pytest.raises(StageStateFailure, match="threshold >= 1"):
            state.define_stage(
                name="approve",
                description="Approve",
                requires_approval=True,
                depends_on=[],
                approval_threshold=0,
            )

    def test_an_unknown_stage_lookup_is_rejected(self, clock: Any) -> None:
        state = build_workflow(clock)

        with pytest.raises(StageStateFailure, match="No stage defined with name verify"):
            state.get_stage_by_name("verify")

        assert state.stage_exists("verify") is False


class TestDependencies:
    def test_a_stage_cannot_start_before_its_dependency_completes(self, clock: Any) -> None:
        state = build_workflow(clock)

        with pytest.raises(StageStateFailure, match="Cannot start deploy before render"):
            state.set_stage_state("deploy", StateEnum.IN_PROGRESS)

    def test_a_stage_starts_once_its_dependency_completes(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.IN_PROGRESS)
        state.set_stage_state("render", StateEnum.COMPLETE)
        state.set_stage_state("deploy", StateEnum.IN_PROGRESS)

        assert state.get_stage_state("deploy") == StateEnum.IN_PROGRESS

    def test_an_unreachable_dependency_still_unblocks_its_dependents(
        self, clock: Any, legacy_history: Any
    ) -> None:
        """Skipping a stage must not strand the stages behind it."""
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.UNREACHABLE, cascade_unreachable=False)
        state.set_stage_state("deploy", StateEnum.IN_PROGRESS)

        assert state.get_stage_state("deploy") == StateEnum.IN_PROGRESS

    def test_unreachable_cascades_to_dependents(self, clock: Any, legacy_history: Any) -> None:
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.UNREACHABLE)

        assert state.get_stage_state("deploy") == StateEnum.UNREACHABLE

    def test_setting_the_state_a_stage_already_holds_is_a_no_op(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.NOT_STARTED)

        assert len(state.get_stage_by_name("render").state_history) == 1

    def test_stages_can_be_selected_by_state_and_by_dependency(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.IN_PROGRESS)

        assert [s.name for s in state.stages_by_state(StateEnum.IN_PROGRESS)] == ["render"]
        assert [s.name for s in state.stages_by_dependency("render")] == ["deploy"]


class TestSearchAttributes:
    def test_stage_state_is_indexed_on_every_transition(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.IN_PROGRESS)
        state.set_stage_state("render", StateEnum.FAILED)

        assert upserted[-1] == {
            FAILED_STAGE_SEARCH_ATTRIBUTE: [True],
            PENDING_APPROVAL_SEARCH_ATTRIBUTE: [False],
        }

    def test_histories_from_before_the_patch_are_not_indexed(
        self, clock: Any, legacy_history: Any, upserted: Any
    ) -> None:
        """Upserting on replay of an older history would break determinism."""
        state = build_workflow(clock)

        state.set_stage_state("render", StateEnum.IN_PROGRESS)

        assert upserted == []


class TestTerminateOnFailure:
    def test_a_stage_workflow_input_configures_termination(self, clock: Any) -> None:
        state = StageMixin()

        state.set_input(StageWorkflowInput(terminate_on_failure=True))

        assert state.terminate_on_failure is True
        assert state.input() == StageWorkflowInput(terminate_on_failure=True)

    def test_an_unrelated_input_leaves_termination_off(self, clock: Any) -> None:
        class PlainInput(BaseModel):
            device: str

        state = StageMixin()

        state.set_input(PlainInput(device="leaf01"))

        assert state.terminate_on_failure is False


class TestSignals:
    async def test_approve_transitions_a_pending_stage(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = StageMixin()
        state.define_stage(
            name="approve",
            description="Approve",
            requires_approval=True,
            depends_on=[],
            approval_threshold=1,
        )
        state.set_stage_state("approve", StateEnum.IN_PROGRESS)
        state.set_stage_state("approve", StateEnum.PENDING_APPROVAL)

        await state.approve(ReviewSignalInput(stage_name="approve", user="reviewer"))

        assert state.get_stage_state("approve") == StateEnum.APPROVED
        assert state.pending_approval() is False

    async def test_reject_transitions_a_pending_stage(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = StageMixin()
        state.define_stage(
            name="approve",
            description="Approve",
            requires_approval=True,
            depends_on=[],
            approval_threshold=1,
        )
        state.set_stage_state("approve", StateEnum.IN_PROGRESS)
        state.set_stage_state("approve", StateEnum.PENDING_APPROVAL)

        await state.reject(ReviewSignalInput(stage_name="approve", user="reviewer"))

        assert state.get_stage_state("approve") == StateEnum.REJECTED

    @pytest.mark.parametrize("signal", ["approve", "reject"])
    async def test_a_review_signal_for_an_unknown_stage_is_ignored(
        self, clock: Any, legacy_history: Any, signal: str
    ) -> None:
        """A signal naming a stage that does not exist must not fail the workflow."""
        state = build_workflow(clock)

        await getattr(state, signal)(ReviewSignalInput(stage_name="verify", user="reviewer"))

        assert state.stages_by_state(StateEnum.NOT_STARTED) == state.stages()

    async def test_retry_returns_a_failed_stage_to_in_progress(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = build_workflow(clock)
        state.set_stage_state("render", StateEnum.IN_PROGRESS)
        state.set_stage_state("render", StateEnum.FAILED)

        await state.retry("render")

        assert state.get_stage_state("render") == StateEnum.IN_PROGRESS
        assert state.failed_stage() is False

    async def test_retry_is_ignored_for_a_stage_that_has_not_failed(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = build_workflow(clock)
        state.set_stage_state("render", StateEnum.IN_PROGRESS)

        await state.retry("render")

        assert state.get_stage_state("render") == StateEnum.IN_PROGRESS

    async def test_retry_is_ignored_for_a_non_retryable_stage(
        self, clock: Any, patched_history: Any, upserted: Any
    ) -> None:
        state = StageMixin()
        state.define_stage(
            name="render",
            description="Render",
            requires_approval=False,
            depends_on=[],
            retryable=False,
        )
        state.set_stage_state("render", StateEnum.IN_PROGRESS)
        state.set_stage_state("render", StateEnum.FAILED)

        await state.retry("render")

        assert state.get_stage_state("render") == StateEnum.FAILED

    async def test_retry_for_an_unknown_stage_is_ignored(
        self, clock: Any, legacy_history: Any
    ) -> None:
        state = build_workflow(clock)

        await state.retry("verify")

        assert state.get_stage_state("render") == StateEnum.NOT_STARTED


class TestChildWorkflows:
    def test_child_workflow_ids_are_recorded_against_their_stage(self, clock: Any) -> None:
        state = build_workflow(clock)

        state.append_child_workflow("deploy", "deploy-leaf01")
        state.append_child_workflow("deploy", "deploy-leaf02")

        assert state.get_stage_by_name("deploy").child_workflows == [
            "deploy-leaf01",
            "deploy-leaf02",
        ]
