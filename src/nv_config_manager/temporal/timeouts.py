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
"""Default Temporal workflow run limits, preserving explicit timeout overrides."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.client import (
    CreateScheduleInput,
    ScheduleActionStartWorkflow,
    ScheduleHandle,
    StartWorkflowInput,
    WorkflowHandle,
)
from temporalio.client import Interceptor as ClientInterceptor
from temporalio.client import OutboundInterceptor as ClientOutboundInterceptor
from temporalio.worker import Interceptor as WorkerInterceptor
from temporalio.worker import (
    StartChildWorkflowInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)

DEFAULT_WORKFLOW_RUN_TIMEOUT = timedelta(hours=3)
_CHILD_TIMEOUT_PATCH_ID = "nvcm-default-child-workflow-timeout-v1"


def _apply_default_timeout(
    options: StartWorkflowInput | StartChildWorkflowInput | ScheduleActionStartWorkflow,
) -> None:
    """Leave either explicit workflow timeout in control of the run's lifetime."""
    if options.run_timeout is None and options.execution_timeout is None:
        options.run_timeout = DEFAULT_WORKFLOW_RUN_TIMEOUT


class DefaultWorkflowTimeoutInterceptor(ClientInterceptor, WorkerInterceptor):
    """Apply the default to client starts, scheduled starts, and child workflows."""

    def intercept_client(self, next: ClientOutboundInterceptor) -> ClientOutboundInterceptor:
        """Wrap workflow and schedule creation on service clients."""
        return _ClientTimeoutInterceptor(next)

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor]:
        """Install child-workflow defaults through the worker's client interceptor."""
        return _WorkflowTimeoutInboundInterceptor


class _ClientTimeoutInterceptor(ClientOutboundInterceptor):
    async def start_workflow(self, input: StartWorkflowInput) -> WorkflowHandle[Any, Any]:
        """Supply a run limit when the caller supplied no workflow timeout."""
        _apply_default_timeout(input)
        return await super().start_workflow(input)

    async def create_schedule(self, input: CreateScheduleInput) -> ScheduleHandle:
        """Bound newly created scheduled runs unless their action has an override."""
        if isinstance(input.schedule.action, ScheduleActionStartWorkflow):
            _apply_default_timeout(input.schedule.action)
        return await super().create_schedule(input)


class _WorkflowTimeoutInboundInterceptor(WorkflowInboundInterceptor):
    def init(self, outbound: WorkflowOutboundInterceptor) -> None:
        """Wrap child starts in the workflow sandbox."""
        super().init(_WorkflowTimeoutOutboundInterceptor(outbound))


class _WorkflowTimeoutOutboundInterceptor(WorkflowOutboundInterceptor):
    async def start_child_workflow(
        self, input: StartChildWorkflowInput
    ) -> workflow.ChildWorkflowHandle[Any, Any]:
        """Apply defaults to new child commands while preserving old histories."""
        if (
            input.run_timeout is None
            and input.execution_timeout is None
            and workflow.patched(_CHILD_TIMEOUT_PATCH_ID)
        ):
            _apply_default_timeout(input)
        return await super().start_child_workflow(input)
