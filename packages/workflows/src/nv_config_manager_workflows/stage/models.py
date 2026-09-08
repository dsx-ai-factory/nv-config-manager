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
"""The stage state machine and the models that travel with it.

These models are serialized into workflow history and returned from the
``stages`` and ``compressed_stages`` queries, so a field change here is a
history-compatibility change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_serializer, field_validator
from temporalio import workflow

from nv_config_manager_workflows.stage.exceptions import StageStateFailure


class ReviewSignalInput(BaseModel):
    """Input for approve/reject signals."""

    stage_name: str
    user: str


class Review(BaseModel):
    """Individual Review."""

    user: str
    time: float

    @field_serializer("time")
    def serialize_time(self, time: float) -> str:
        """Serialize time as isoformat datetime."""
        # Due to an issue with Temporal, we cannot use
        # datetime objects directly in Pydantic models
        return datetime.fromtimestamp(time, tz=UTC).isoformat()

    @field_validator("time", mode="before")
    @classmethod
    def convert_isoformat(cls, raw: str | float | int) -> float:
        """Convert isoformat string to timestamp."""
        if isinstance(raw, (float, int)):
            return float(raw)
        return datetime.fromisoformat(raw).timestamp()


class StateEnum(StrEnum):
    """Enum of states for a Stage."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    COMPLETE = "COMPLETE"
    UNREACHABLE = "UNREACHABLE"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


class HistoryEntry(BaseModel):
    """State Transition History Entry."""

    state: StateEnum
    time: float

    @field_serializer("time")
    def serialize_time(self, time: float) -> str:
        """Serialize time as isoformat datetime."""
        # Due to an issue with Temporal, we cannot use
        # datetime objects directly in Pydantic models
        return datetime.fromtimestamp(time, tz=UTC).isoformat()

    @field_validator("time", mode="before")
    @classmethod
    def convert_isoformat(cls, raw: str | float | int) -> float:
        """Convert isoformat string to timestamp."""
        if isinstance(raw, (float, int)):
            return float(raw)
        return datetime.fromisoformat(raw).timestamp()


class StageWorkflowInput(BaseModel):
    """Base input for workflows that configure stage failure behavior."""

    terminate_on_failure: bool = Field(
        default=False,
        description="Terminate the workflow instead of waiting to retry a failed stage.",
    )


class StageInput(BaseModel):
    """Dataclass to represent Stage Input."""


class StageOutput(BaseModel):
    """Dataclass to represent Stage Output."""

    display: str


class Stage(BaseModel):
    """Workflow Stage Model."""

    _VALID_TRANSITIONS = {  # pylint: disable=invalid-name
        StateEnum.NOT_STARTED: [
            StateEnum.IN_PROGRESS,
            StateEnum.UNREACHABLE,
        ],
        StateEnum.IN_PROGRESS: [
            StateEnum.PENDING_APPROVAL,
            StateEnum.COMPLETE,
            StateEnum.FAILED,
        ],
        StateEnum.PENDING_APPROVAL: [StateEnum.APPROVED, StateEnum.REJECTED],
        StateEnum.APPROVED: [StateEnum.COMPLETE, StateEnum.FAILED],
        StateEnum.REJECTED: [StateEnum.COMPLETE, StateEnum.FAILED],
        StateEnum.FAILED: [StateEnum.IN_PROGRESS],
    }

    name: str
    description: str
    requires_approval: bool
    state: StateEnum
    input: Any = None
    output: Any = None
    depends_on: list[str]
    approvers: list[Review] = []
    rejecters: list[Review] = []
    approval_threshold: int = 0
    state_history: list[HistoryEntry] = []
    retryable: bool
    retry_count: int = 0
    traceback: str | None
    child_workflows: list[str] = []

    @computed_field
    def execution_time(self) -> float | None:
        """Calculate the time spent executing the stage."""
        if self.state not in (StateEnum.COMPLETE, StateEnum.FAILED):
            return None
        # Find latest history entry where state is IN_PROGRESS
        start_entry: HistoryEntry = next(
            entry for entry in reversed(self.state_history) if entry.state == StateEnum.IN_PROGRESS
        )
        start = start_entry.time
        end_entry: HistoryEntry = next(
            entry for entry in reversed(self.state_history) if entry.state == self.state
        )
        end = end_entry.time
        return end - start

    def approve(self, reviewer: str) -> None:
        """Approve the stage."""
        if not self.state == StateEnum.PENDING_APPROVAL:
            raise StageStateFailure(f"Stage {self.name} is not pending approval.")
        self.approvers.append(Review(user=reviewer, time=workflow.time()))
        if len(self.approvers) >= self.approval_threshold:
            self.transition(StateEnum.APPROVED)

    def reject(self, reviewer: str) -> None:
        """Reject the stage."""
        if not self.state == StateEnum.PENDING_APPROVAL:
            raise StageStateFailure(f"Stage {self.name} is not pending approval.")
        self.rejecters.append(Review(user=reviewer, time=workflow.time()))
        self.transition(StateEnum.REJECTED)

    def transition(self, new_state: StateEnum) -> None:
        """Transition the state."""
        if new_state not in self._VALID_TRANSITIONS.get(self.state, []):
            raise StageStateFailure(f"Invalid transition from {self.state} to {new_state}.")
        if (
            new_state == StateEnum.COMPLETE
            and self.requires_approval
            and self.state not in [StateEnum.APPROVED, StateEnum.REJECTED]
        ):
            raise StageStateFailure(
                "A stage that requires approval must be reviewed before completion."
            )

        if self.state == StateEnum.FAILED and new_state == StateEnum.IN_PROGRESS:
            # Clear the previous traceback
            self.traceback = None

        self.state_history.append(HistoryEntry(state=new_state, time=workflow.time()))
        self.state = new_state
