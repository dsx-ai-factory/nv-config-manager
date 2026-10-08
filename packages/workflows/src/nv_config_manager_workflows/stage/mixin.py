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
"""Workflow Stage Mixin."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from temporalio import workflow

from nv_config_manager_workflows.log import get_workflow_logger
from nv_config_manager_workflows.mixins.base import BaseMixin
from nv_config_manager_workflows.search_attributes import (
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
)
from nv_config_manager_workflows.stage.exceptions import StageStateFailure
from nv_config_manager_workflows.stage.models import (
    HistoryEntry,
    ReviewSignalInput,
    Stage,
    StageInput,
    StageOutput,
    StageWorkflowInput,
    StateEnum,
)
from nv_config_manager_workflows.stage.presentation import (
    compress_stages,
    decompress_stages,
    render_markdown_table,
    render_markdown_table_dict,
)

STAGE_STATE_SEARCH_ATTRIBUTES_PATCH = "stage-state-search-attributes-v1"


class StageMixin(BaseMixin):
    """Stage Mixin Class."""

    logger = get_workflow_logger(__name__)

    def __init__(self) -> None:
        """Initialize Workflow with Stages."""
        self._stages: list[Stage] = []
        self._input: BaseModel | None = None
        self._terminate_on_failure = False

    @property
    def terminate_on_failure(self) -> bool:
        """Return whether a stage failure should terminate the workflow."""
        return self._terminate_on_failure

    def set_terminate_on_failure(self, enabled: bool) -> None:
        """Configure stage failures to terminate instead of waiting for retry."""
        self._terminate_on_failure = enabled

    def set_input(self, workflow_input: BaseModel) -> None:
        """Set the workflow input."""
        self._input = workflow_input
        self.set_terminate_on_failure(
            isinstance(workflow_input, StageWorkflowInput) and workflow_input.terminate_on_failure
        )

    def stage_exists(self, name: str) -> bool:
        """Return true if a stage has been defined with the given name."""
        try:
            next(stage for stage in self._stages if stage.name == name)
            return True
        except StopIteration:
            return False

    def get_stage_by_name(self, name: str) -> Stage:
        """Get workflow stage by name."""
        try:
            return next(stage for stage in self._stages if stage.name == name)
        except StopIteration as exc:
            raise StageStateFailure(f"No stage defined with name {name}.") from exc

    def define_stage(  # pylint: disable=too-many-arguments
        self,
        name: str,
        description: str,
        requires_approval: bool,
        depends_on: list[str],
        approval_threshold: int = 0,
        retryable: bool = True,
    ) -> None:
        """Define a stage."""
        if self.stage_exists(name):
            raise StageStateFailure(f"Stage already defined with name {name}.")

        if requires_approval and approval_threshold < 1:
            raise StageStateFailure("Approval stages must have a threshold >= 1.")

        for depname in depends_on:
            if not self.stage_exists(depname):
                raise StageStateFailure(f"Stage {depname} in depends_on does not exist.")

        stage = Stage(
            name=name,
            description=description,
            requires_approval=requires_approval,
            state=StateEnum.NOT_STARTED,
            output=None,
            depends_on=depends_on,
            approval_threshold=approval_threshold,
            state_history=[HistoryEntry(state=StateEnum.NOT_STARTED, time=workflow.time())],
            retryable=retryable,
            retry_count=0,
            traceback=None,
            child_workflows=[],
        )
        self._stages.append(stage)

    def append_child_workflow(self, name: str, workflow_id: str) -> None:
        """Append a child workflow to the child workflows associated with this stage."""
        stage = self.get_stage_by_name(name)
        stage.child_workflows.append(workflow_id)

    def set_stage_state(
        self, name: str, state: StateEnum, *, cascade_unreachable: bool = True
    ) -> None:
        """Update stage progress."""
        stage = self.get_stage_by_name(name)
        if stage.state == state:
            return
        # Check dependencies
        if state == StateEnum.IN_PROGRESS:
            for dependency in stage.depends_on:
                depstage = self.get_stage_by_name(dependency)
                if depstage.state not in (StateEnum.COMPLETE, StateEnum.UNREACHABLE):
                    raise StageStateFailure(f"Cannot start {name} before {dependency} is complete.")

        stage.transition(state)
        self._upsert_stage_state_search_attributes()

        if state == StateEnum.UNREACHABLE and cascade_unreachable:
            # Set all stages dependent on this stage as unreachable as well
            for dependent_stage in self.stages_by_dependency(stage.name):
                if dependent_stage.state != state:
                    dependent_stage.transition(state)
            self._upsert_stage_state_search_attributes()

    def get_stage_state(self, name: str) -> StateEnum:
        """Get the state of the stage."""
        return self.get_stage_by_name(name).state

    def set_stage_input(self, name: str, stage_input: StageInput) -> None:
        """Set the input for a stage."""
        stage = self.get_stage_by_name(name)
        stage.input = stage_input

    def set_stage_output(self, name: str, output: StageOutput) -> None:
        """Set the output for a stage."""
        stage = self.get_stage_by_name(name)
        stage.output = output

    def stages_by_state(self, state: StateEnum) -> list[Stage]:
        """Return a list of stages by their current state."""
        return [stage for stage in self._stages if stage.state == state]

    def stages_by_dependency(self, dependency: str) -> list[Stage]:
        """Return a list of stages dependent on a given stage."""
        return [stage for stage in self._stages if dependency in stage.depends_on]

    def _upsert_stage_state_search_attributes(self) -> None:
        """Index workflow stage state summary flags."""
        # Keep histories from before stage-state search attributes replayable.
        if not workflow.patched(STAGE_STATE_SEARCH_ATTRIBUTES_PATCH):
            return
        workflow.upsert_search_attributes(
            {
                FAILED_STAGE_SEARCH_ATTRIBUTE: [self.failed_stage()],
                PENDING_APPROVAL_SEARCH_ATTRIBUTE: [self.pending_approval()],
            }
        )

    @staticmethod
    def markdown_table(
        rows: Sequence[BaseModel] | BaseModel, exclude: set[str] | None = None
    ) -> str:
        """Provide a markdown table output."""
        return render_markdown_table(rows, exclude)

    @staticmethod
    def markdown_table_dict(rows: Sequence[Any] | Any) -> str:
        """Provide a tabular output for a dict."""
        return render_markdown_table_dict(rows)

    @workflow.query
    def pending_approval(self) -> bool:
        """Return true if the workflow is currently pending approval."""
        return bool(
            next(
                (stage for stage in self._stages if stage.state == StateEnum.PENDING_APPROVAL),
                None,
            )
        )

    @workflow.query
    def failed_stage(self) -> bool:
        """Return true if any workflow stage is currently failed."""
        return bool(
            next(
                (stage for stage in self._stages if stage.state == StateEnum.FAILED),
                None,
            )
        )

    @workflow.query
    def stages(self) -> list[Stage]:
        """Return all defined stages for the workflow."""
        return self._stages

    @staticmethod
    def compress_stages(stages: list[Stage]) -> str:
        """Compress stages into a base64 encoded gzipped JSON string."""
        return compress_stages(stages)

    @staticmethod
    def decompress_stages(compressed_stages: str) -> list[Stage]:
        """Decompress stages from a base64 encoded gzipped JSON string."""
        return decompress_stages(compressed_stages)

    @workflow.query
    def compressed_stages(self) -> str:
        """Return the stages output in a compressed format.

        Returns:
            str: A compressed JSON string containing the stages data.
            The string is compressed using gzip and encoded in base64 for efficient transmission.
        """
        return self.compress_stages(self._stages)

    @workflow.query
    def input(self) -> BaseModel | None:
        """Return the worfklow input."""
        return self._input

    @workflow.signal
    async def approve(self, review_input: ReviewSignalInput) -> None:
        """Approve signal."""
        if self.stage_exists(review_input.stage_name):
            stage = self.get_stage_by_name(review_input.stage_name)
            stage.approve(review_input.user)
            self._upsert_stage_state_search_attributes()
        else:
            self.logger.error(
                "Received approve signal for non-existent stage: %s",
                review_input.stage_name,
            )

    @workflow.signal
    async def reject(self, review_input: ReviewSignalInput) -> None:
        """Reject signal."""
        if self.stage_exists(review_input.stage_name):
            stage = self.get_stage_by_name(review_input.stage_name)
            stage.reject(review_input.user)
            self._upsert_stage_state_search_attributes()
        else:
            self.logger.error(
                "Received reject signal for non-existent stage: %s",
                review_input.stage_name,
            )

    @workflow.signal
    async def retry(self, stage_name: str) -> None:
        """Retry signal."""
        if not self.stage_exists(stage_name):
            self.logger.error("Received retry signal for non-existent stage: %s", stage_name)
            return
        stage = self.get_stage_by_name(stage_name)
        if not stage.retryable:
            self.logger.warning("Ignoring retry request for non-retryable stage.")
            return
        if stage.state != StateEnum.FAILED:
            self.logger.warning("Ignoring retry request for non-failed stage.")
            return
        # Move the stage back to IN_PROGRESS to trigger rerun of stage function
        self.set_stage_state(stage_name, StateEnum.IN_PROGRESS)
