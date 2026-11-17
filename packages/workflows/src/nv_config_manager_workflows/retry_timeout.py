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
"""Pin the failed-stage retry window in workflow history for deterministic replay."""

from datetime import timedelta
from typing import Any, NoReturn

from temporalio import workflow
from temporalio.api.common.v1 import Payload
from temporalio.client import (
    CreateScheduleInput,
    ScheduleActionStartWorkflow,
    ScheduleHandle,
    StartWorkflowInput,
    WorkflowHandle,
)
from temporalio.client import Interceptor as ClientInterceptor
from temporalio.client import OutboundInterceptor as ClientOutboundInterceptor
from temporalio.worker import (
    ContinueAsNewInput,
    StartChildWorkflowInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)
from temporalio.worker import Interceptor as WorkerInterceptor

DEFAULT_FAILED_STAGE_RETRY_TIMEOUT_SECONDS = 10800
FAILED_STAGE_RETRY_TIMEOUT_HEADER = "nvcm-failed-stage-retry-timeout-seconds"


def validate_retry_timeout_seconds(seconds: int) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
        raise ValueError("failed_stage_retry_timeout_seconds must be a positive integer")
    try:
        timedelta(seconds=seconds)
    except OverflowError as error:
        raise ValueError("failed_stage_retry_timeout_seconds is too large") from error
    return seconds


def failed_stage_retry_timeout() -> timedelta:
    """Read immutable workflow headers; old histories keep the three-hour default."""
    payload = workflow.info().headers.get(FAILED_STAGE_RETRY_TIMEOUT_HEADER)
    seconds = DEFAULT_FAILED_STAGE_RETRY_TIMEOUT_SECONDS if payload is None else int(payload.data)
    return timedelta(seconds=validate_retry_timeout_seconds(seconds))


class FailedStageRetryTimeoutInterceptor(ClientInterceptor, WorkerInterceptor):
    """Record deployment configuration at start and inherit it in child runs."""

    def __init__(self, seconds: int = DEFAULT_FAILED_STAGE_RETRY_TIMEOUT_SECONDS) -> None:
        self.seconds = validate_retry_timeout_seconds(seconds)

    def intercept_client(self, next: ClientOutboundInterceptor) -> ClientOutboundInterceptor:
        return _ClientRetryTimeout(next, self.seconds)

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor]:
        return _WorkflowRetryTimeoutInbound


class _ClientRetryTimeout(ClientOutboundInterceptor):
    def __init__(self, next: ClientOutboundInterceptor, seconds: int) -> None:
        super().__init__(next)
        self._payload = Payload(metadata={"encoding": b"json/plain"}, data=str(seconds).encode())

    async def start_workflow(self, input: StartWorkflowInput) -> WorkflowHandle[Any, Any]:
        input.headers = {**input.headers, FAILED_STAGE_RETRY_TIMEOUT_HEADER: self._payload}
        return await super().start_workflow(input)

    async def create_schedule(self, input: CreateScheduleInput) -> ScheduleHandle:
        action = input.schedule.action
        if isinstance(action, ScheduleActionStartWorkflow):
            action.headers = {
                **(action.headers or {}),
                FAILED_STAGE_RETRY_TIMEOUT_HEADER: self._payload,
            }
        return await super().create_schedule(input)


class _WorkflowRetryTimeoutInbound(WorkflowInboundInterceptor):
    def init(self, outbound: WorkflowOutboundInterceptor) -> None:
        super().init(_WorkflowRetryTimeoutOutbound(outbound))


class _WorkflowRetryTimeoutOutbound(WorkflowOutboundInterceptor):
    @staticmethod
    def _inherit_header(input: StartChildWorkflowInput | ContinueAsNewInput) -> None:
        payload = workflow.info().headers.get(FAILED_STAGE_RETRY_TIMEOUT_HEADER)
        # Old histories must not gain new headers during replay.
        if payload is not None:
            input.headers = {**input.headers, FAILED_STAGE_RETRY_TIMEOUT_HEADER: payload}

    async def start_child_workflow(
        self, input: StartChildWorkflowInput
    ) -> workflow.ChildWorkflowHandle[Any, Any]:
        self._inherit_header(input)
        return await super().start_child_workflow(input)

    def continue_as_new(self, input: ContinueAsNewInput) -> NoReturn:
        self._inherit_header(input)
        super().continue_as_new(input)
