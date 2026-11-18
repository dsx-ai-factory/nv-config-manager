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
"""The deployment retry window must be validated and pinned in workflow history."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio import workflow
from temporalio.api.common.v1 import Payload
from temporalio.client import (
    Client,
    Interceptor,
    OutboundInterceptor,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
    WorkflowHistory,
)
from temporalio.worker import (
    ContinueAsNewInput,
    Replayer,
    StartChildWorkflowInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)

from nv_config_manager.temporal.client.connection import failed_stage_retry_timeout_seconds
from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.retry_timeout import (
    FAILED_STAGE_RETRY_TIMEOUT_HEADER,
    FailedStageRetryTimeoutInterceptor,
    failed_stage_retry_timeout,
)
from nv_config_manager_workflows.workflows.multi_deploy import MultiDeployWorkflow
from nv_config_manager_workflows.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 10800), ("", 10800), (" 7200 ", 7200), ("1", 1), ("31536000", 31536000)],
)
def test_ini_retry_window(custom_ini, raw, expected):
    custom_ini(
        "" if raw is None else f"[temporal.worker]\nfailed_stage_retry_timeout_seconds={raw}\n"
    )
    assert failed_stage_retry_timeout_seconds() == expected


@pytest.mark.parametrize(
    "raw", ["0", "-1", "1.5", "true", "forever", "31536001", "1000000000000000", "9" * 30]
)
def test_invalid_ini_retry_window_is_rejected(custom_ini, raw):
    custom_ini(f"[temporal.worker]\nfailed_stage_retry_timeout_seconds={raw}\n")
    with pytest.raises(ValueError):
        failed_stage_retry_timeout_seconds()


class _CaptureClientCalls(Interceptor):
    def __init__(self):
        self.outbound = Mock(spec=OutboundInterceptor)

    def intercept_client(self, next):
        return self.outbound


@pytest.mark.parametrize("scheduled", [False, True])
async def test_sdk_starts_record_window_without_imposing_a_run_timeout(scheduled):
    capture = _CaptureClientCalls()
    client = await Client.connect(
        "localhost:7233",
        lazy=True,
        interceptors=[FailedStageRetryTimeoutInterceptor(7200), capture],
    )
    if scheduled:
        await client.create_schedule(
            "schedule",
            Schedule(
                action=ScheduleActionStartWorkflow("Probe", id="probe", task_queue="queue"),
                spec=ScheduleSpec(),
            ),
        )
        options = capture.outbound.create_schedule.await_args.args[0].schedule.action
    else:
        await client.start_workflow("Probe", id="probe", task_queue="queue")
        options = capture.outbound.start_workflow.await_args.args[0]
    assert options.headers[FAILED_STAGE_RETRY_TIMEOUT_HEADER].data == b"7200"
    assert options.run_timeout is None
    assert options.execution_timeout is None


@pytest.mark.parametrize("seconds", [None, 7200])
async def test_children_and_continue_as_new_inherit_history_not_worker_configuration(
    monkeypatch, seconds
):
    headers = {} if seconds is None else {FAILED_STAGE_RETRY_TIMEOUT_HEADER: Payload(data=b"7200")}
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(headers=headers))
    downstream = Mock(spec=WorkflowInboundInterceptor)
    outbound = Mock(spec=WorkflowOutboundInterceptor)
    outbound.start_child_workflow = AsyncMock()
    outbound.continue_as_new.side_effect = RuntimeError("continue-as-new")
    interceptor = FailedStageRetryTimeoutInterceptor(60).workflow_interceptor_class(
        WorkflowInterceptorClassInput(unsafe_extern_functions={})
    )
    interceptor(downstream).init(outbound)
    wrapped = downstream.init.call_args.args[0]
    trace = Payload(data=b"trace")
    child = Mock(spec=StartChildWorkflowInput, headers={"trace": trace})
    await wrapped.start_child_workflow(child)
    expected = {"trace": trace, **headers}
    assert child.headers == expected
    continued = Mock(spec=ContinueAsNewInput, headers={"trace": trace})
    with pytest.raises(RuntimeError, match="continue-as-new"):
        wrapped.continue_as_new(continued)
    assert continued.headers == expected
    assert failed_stage_retry_timeout() == timedelta(seconds=seconds or 10800)


@pytest.mark.parametrize(
    "filename,workflow_class",
    [
        ("multi_deploy.json", MultiDeployWorkflow),
        ("site_password_rotation.json", SitePasswordRotationWorkflow),
    ],
)
async def test_old_child_histories_replay_with_retry_window_interceptor(filename, workflow_class):
    fixture = (
        Path(__file__).parents[3] / "packages/workflows/tests/workflows/replay/fixtures" / filename
    )
    await Replayer(
        workflows=[workflow_class],
        interceptors=[FailedStageRetryTimeoutInterceptor(60)],
        data_converter=get_data_converter(),
    ).replay_workflow(WorkflowHistory.from_json(filename, fixture.read_text()))
