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
# pylint:disable=too-many-branches
"""The decorator that drives one stage through its state transitions."""

from __future__ import annotations

import functools
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from temporalio import workflow
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    RetryState,
    TemporalError,
)

from nv_config_manager_workflows.stage.exceptions import StageRuntimeFailure, StageStateFailure
from nv_config_manager_workflows.stage.models import StageInput, StageOutput, StateEnum

if TYPE_CHECKING:
    from nv_config_manager_workflows.stage.mixin import StageMixin

F = TypeVar("F", bound=Callable[..., Any])


def stage_executor(stage_name: str, *, name_attribute: str | None = None) -> Callable[[F], F]:
    """Stage decorator."""

    def stage_decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrap_stage(
            self: StageMixin, stage_input: StageInput | None = None
        ) -> StageOutput:
            effective_stage_name = (
                getattr(self, name_attribute) if name_attribute is not None else stage_name
            )
            self.set_stage_state(effective_stage_name, StateEnum.IN_PROGRESS)
            current_stage = self.get_stage_by_name(effective_stage_name)
            while True:
                try:
                    result: StageOutput
                    if stage_input:
                        self.set_stage_input(effective_stage_name, stage_input)
                        result = await func(self, stage_input)
                    else:
                        result = await func(self)
                    self.set_stage_output(effective_stage_name, result)
                    self.set_stage_state(effective_stage_name, StateEnum.COMPLETE)
                    return result
                except StageStateFailure as exc:
                    raise exc
                except Exception as exc:  # pylint:disable=broad-exception-caught
                    self.set_stage_state(effective_stage_name, StateEnum.FAILED)
                    current_stage.traceback = traceback.format_exc()

                    if self.terminate_on_failure or not current_stage.retryable:
                        raise StageRuntimeFailure(
                            f"Stage {effective_stage_name} has failed and is non-retryable: {exc}",
                            non_retryable=True,
                        ) from exc

                    if isinstance(exc, ApplicationError):
                        if exc.non_retryable:
                            raise StageRuntimeFailure(
                                f"Stage {effective_stage_name} has failed and cannot be retried: {exc.cause}",
                                non_retryable=True,
                            ) from exc
                    elif isinstance(exc, ActivityError):
                        if exc.retry_state == RetryState.NON_RETRYABLE_FAILURE and not isinstance(
                            exc.cause, TimeoutError
                        ):
                            raise StageRuntimeFailure(
                                f"Activity {exc.activity_type}:{exc.activity_id} in "
                                f"{effective_stage_name} has failed and cannot be retried: "
                                f"{exc.cause}",
                                non_retryable=True,
                            ) from exc
                    elif isinstance(exc, ChildWorkflowError):
                        if exc.retry_state == RetryState.NON_RETRYABLE_FAILURE and not isinstance(
                            exc.cause, TimeoutError
                        ):
                            raise StageRuntimeFailure(
                                f"Child workflow {exc.workflow_type}:{exc.workflow_id} "
                                f"in {effective_stage_name} has failed and cannot be retried: "
                                f"{exc.cause}",
                                non_retryable=True,
                            ) from exc
                    elif not isinstance(exc, TemporalError):
                        # Unexpected exceptions likely indicate a bug in the stage code
                        raise StageRuntimeFailure(
                            f"Unexpected exception during stage runtime: {exc}",
                            non_retryable=True,
                        ) from exc
                    # Wait for retry signal that transitions back to in-progress
                    await workflow.wait_condition(
                        lambda: current_stage.state == StateEnum.IN_PROGRESS
                    )
                    current_stage.retry_count += 1

        return cast(F, wrap_stage)

    return stage_decorator
