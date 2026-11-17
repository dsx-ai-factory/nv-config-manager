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
"""Workflow timeout defaults at the Temporal client and worker boundaries."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
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
    Replayer,
    StartChildWorkflowInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)

from nv_config_manager.temporal.timeouts import DefaultWorkflowTimeoutInterceptor
from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.workflows.multi_deploy import MultiDeployWorkflow
from nv_config_manager_workflows.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)

_TIMEOUT_CASES = [
    pytest.param(None, None, timedelta(hours=3), id="default"),
    pytest.param(timedelta(minutes=10), None, timedelta(minutes=10), id="short-run"),
    pytest.param(timedelta(hours=6), None, timedelta(hours=6), id="long-run"),
    pytest.param(None, timedelta(minutes=30), None, id="short-execution"),
    pytest.param(None, timedelta(hours=6), None, id="long-execution"),
    pytest.param(timedelta(hours=4), timedelta(hours=6), timedelta(hours=4), id="both"),
    pytest.param(timedelta(0), None, timedelta(0), id="zero-run"),
    pytest.param(None, timedelta(0), None, id="zero-execution"),
]


class _CaptureClientCalls(Interceptor):
    def __init__(self) -> None:
        """Capture SDK-generated inputs without connecting to a Temporal server."""
        self.outbound = Mock(spec=OutboundInterceptor)

    def intercept_client(self, next: OutboundInterceptor) -> OutboundInterceptor:
        """Stop the interceptor chain before it reaches the network."""
        return self.outbound


@pytest.mark.parametrize(("run_timeout", "execution_timeout", "expected_run"), _TIMEOUT_CASES)
@pytest.mark.parametrize("scheduled", [False, True], ids=["direct", "scheduled"])
async def test_client_workflow_timeout_defaults(
    run_timeout: timedelta | None,
    execution_timeout: timedelta | None,
    expected_run: timedelta | None,
    scheduled: bool,
) -> None:
    """Actual SDK calls default only missing timeouts and preserve either override."""
    capture = _CaptureClientCalls()
    client = await Client.connect(
        "localhost:7233",
        lazy=True,
        interceptors=[DefaultWorkflowTimeoutInterceptor(), capture],
    )
    if scheduled:
        await client.create_schedule(
            "probe-schedule",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "Probe",
                    id="probe",
                    task_queue="default-task-queue",
                    run_timeout=run_timeout,
                    execution_timeout=execution_timeout,
                ),
                spec=ScheduleSpec(),
            ),
        )
        options = capture.outbound.create_schedule.await_args.args[0].schedule.action
    else:
        await client.start_workflow(
            "Probe",
            id="probe",
            task_queue="default-task-queue",
            run_timeout=run_timeout,
            execution_timeout=execution_timeout,
        )
        options = capture.outbound.start_workflow.await_args.args[0]

    assert options.run_timeout == expected_run
    assert options.execution_timeout == execution_timeout
    assert options.task_queue == "default-task-queue"


@pytest.mark.parametrize(("run_timeout", "execution_timeout", "expected_run"), _TIMEOUT_CASES)
@pytest.mark.parametrize("patched", [True, False], ids=["new-history", "old-history"])
async def test_child_workflow_timeout_defaults(
    run_timeout: timedelta | None,
    execution_timeout: timedelta | None,
    expected_run: timedelta | None,
    patched: bool,
) -> None:
    """Worker composition preserves overrides and does not change old child commands."""
    downstream = Mock(spec=WorkflowInboundInterceptor)
    outbound = Mock(spec=WorkflowOutboundInterceptor)
    outbound.start_child_workflow = AsyncMock()
    interceptor_class = DefaultWorkflowTimeoutInterceptor().workflow_interceptor_class(
        WorkflowInterceptorClassInput(unsafe_extern_functions={})
    )
    interceptor_class(downstream).init(outbound)
    wrapped_outbound = downstream.init.call_args.args[0]
    options = Mock(
        spec=StartChildWorkflowInput,
        run_timeout=run_timeout,
        execution_timeout=execution_timeout,
    )

    with patch("nv_config_manager.temporal.timeouts.workflow.patched", return_value=patched):
        handle = await wrapped_outbound.start_child_workflow(options)

    assert options.run_timeout == (expected_run if patched else run_timeout)
    assert options.execution_timeout == execution_timeout
    outbound.start_child_workflow.assert_awaited_once_with(options)
    assert handle is outbound.start_child_workflow.return_value


@pytest.mark.parametrize(
    ("filename", "workflow_class"),
    [
        ("multi_deploy.json", MultiDeployWorkflow),
        ("site_password_rotation.json", SitePasswordRotationWorkflow),
    ],
)
async def test_existing_parent_histories_replay_with_timeout_interceptor(
    filename: str, workflow_class: type
) -> None:
    """The real worker sandbox can replay children started before timeout defaults."""
    fixture = (
        Path(__file__).parents[3] / "packages/workflows/tests/workflows/replay/fixtures" / filename
    )
    history = WorkflowHistory.from_json(filename.removesuffix(".json"), fixture.read_text())

    await Replayer(
        workflows=[workflow_class],
        interceptors=[DefaultWorkflowTimeoutInterceptor()],
        data_converter=get_data_converter(),
    ).replay_workflow(history)
