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
import asyncio
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.stage import (
    StageMixin,
    StageWorkflowInput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.search_attributes import (
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.hello_world.activities.hello_world import (
    hello_world_activity,
    hello_world_prompt_activity,
    hello_world_reject_activity,
)
from nv_config_manager.temporal.hello_world.workflows.hello_world_workflow import (
    HelloWorld,
    HelloWorldApproval,
    HelloWorldInput,
)
from nv_config_manager.temporal.ngc.activities.slack import SlackMessageInput, SlackMessageOutput


def test_stage_mixin_reads_terminate_on_failure_from_workflow_input() -> None:
    workflow_state = StageMixin()

    workflow_state.set_input(StageWorkflowInput(terminate_on_failure=True))

    assert workflow_state.terminate_on_failure is True


@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.mixin.workflow.patched", return_value=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.upsert_search_attributes")
def test_unreachable_stage_cascades_to_direct_dependents(mock_upsert, mock_patched, mock_time):
    workflow_state = StageMixin()
    workflow_state.define_stage(
        name="source",
        description="Source",
        depends_on=[],
        requires_approval=False,
    )
    workflow_state.define_stage(
        name="dependent",
        description="Dependent",
        depends_on=["source"],
        requires_approval=False,
    )
    workflow_state.define_stage(
        name="unrelated",
        description="Unrelated",
        depends_on=[],
        requires_approval=False,
    )

    workflow_state.set_stage_state("source", StateEnum.UNREACHABLE)

    assert workflow_state.get_stage_state("source") == StateEnum.UNREACHABLE
    assert workflow_state.get_stage_state("dependent") == StateEnum.UNREACHABLE
    assert workflow_state.get_stage_state("unrelated") == StateEnum.NOT_STARTED
    assert mock_time.called
    assert mock_patched.called
    assert mock_upsert.called


@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.mixin.workflow.patched", return_value=False)
@patch("nv_config_manager_workflows.stage.mixin.workflow.upsert_search_attributes")
def test_stage_state_search_attributes_skip_old_histories(mock_upsert, mock_patched, mock_time):
    workflow_state = StageMixin()
    workflow_state.define_stage(
        name="test",
        description="test",
        depends_on=[],
        requires_approval=False,
    )

    workflow_state.set_stage_state("test", StateEnum.IN_PROGRESS)

    assert workflow_state.get_stage_state("test") == StateEnum.IN_PROGRESS
    assert mock_time.called
    assert mock_patched.called
    mock_upsert.assert_not_called()


@activity.defn(name="send_slack_message")
async def send_slack_message(input: SlackMessageInput) -> SlackMessageOutput:
    """Mock Slack message activity."""
    return SlackMessageOutput(thread_ts="test-thread")


async def start_workflow_environment() -> WorkflowEnvironment:
    """Start a Temporal test environment with stage search attributes."""
    env = await WorkflowEnvironment.start_local(
        dev_server_extra_args=[
            "--dynamic-config-value",
            "system.forceSearchAttributesCacheRefreshOnRead=true",
        ],
    )
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes={
                PENDING_APPROVAL_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
                FAILED_STAGE_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
            },
        )
    )
    await env.client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=env.client.namespace)
    )
    return env


@pytest.mark.asyncio
async def test_execute_workflow():
    task_queue_name = str(uuid.uuid4())
    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[HelloWorld],
            activities=[hello_world_activity],
        ):
            assert "Hello, test!" == await env.client.execute_workflow(
                HelloWorld.run,
                HelloWorldInput(name="test"),
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow_approval(mock_time):
    task_queue_name = str(uuid.uuid4())
    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[HelloWorldApproval],
            activities=[
                hello_world_activity,
                hello_world_prompt_activity,
                hello_world_reject_activity,
                send_slack_message,
            ],
        ):
            # Test approve signal
            handle = await env.client.start_workflow(
                HelloWorldApproval.run,
                HelloWorldInput(name="test"),
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            # Confirm that the workflow does not advance
            while await handle.query("pending_approval") is False:
                await asyncio.sleep(0.1)

            workflow_description = await handle.describe()
            assert workflow_description.status == WorkflowExecutionStatus.RUNNING
            assert await handle.query("pending_approval")
            assert await handle.query("input") == {"name": "test"}

            expected_stages_preapprove = [
                {
                    "approval_threshold": 1,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": None,
                    "input": None,
                    "name": "prompt",
                    "output": {
                        "approved": False,
                        "display": "Would you like to be greeted?",
                    },
                    "rejecters": [],
                    "requires_approval": True,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "PENDING_APPROVAL",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {
                            "state": "PENDING_APPROVAL",
                            "time": "1970-01-01T00:00:00+00:00",
                        },
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": None,
                    "input": None,
                    "name": "greet",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": None,
                    "input": None,
                    "name": "goodbye",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
            ]

            assert await handle.query("stages") == expected_stages_preapprove

            # Send approve signal
            await handle.signal("approve", {"stage_name": "prompt", "user": "Test"})
            assert "Hello, test!" == await handle.result()

            expected_stages_approve = [
                {
                    "approval_threshold": 1,
                    "approvers": [{"time": "1970-01-01T00:00:00+00:00", "user": "Test"}],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "prompt",
                    "output": {
                        "approved": True,
                        "display": "Would you like to be greeted?",
                    },
                    "rejecters": [],
                    "requires_approval": True,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "COMPLETE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {
                            "state": "PENDING_APPROVAL",
                            "time": "1970-01-01T00:00:00+00:00",
                        },
                        {"state": "APPROVED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": 0.0,
                    "input": {"name": "test"},
                    "name": "greet",
                    "output": {"display": "Hello, test!"},
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "COMPLETE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": None,
                    "input": None,
                    "name": "goodbye",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "UNREACHABLE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "UNREACHABLE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
            ]

            assert await handle.query("stages") == expected_stages_approve

            # Test reject signal
            handle = await env.client.start_workflow(
                HelloWorldApproval.run,
                HelloWorldInput(name="test"),
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            # Send reject signal
            while await handle.query("pending_approval") is False:
                await asyncio.sleep(0.1)

            assert await handle.query("stages") == expected_stages_preapprove
            await handle.signal("reject", {"stage_name": "prompt", "user": "Test"})
            assert "Goodbye!" == await handle.result()

            expected_stages_reject = [
                {
                    "approval_threshold": 1,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "prompt",
                    "output": {
                        "approved": False,
                        "display": "Would you like to be greeted?",
                    },
                    "rejecters": [{"time": "1970-01-01T00:00:00+00:00", "user": "Test"}],
                    "requires_approval": True,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "COMPLETE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {
                            "state": "PENDING_APPROVAL",
                            "time": "1970-01-01T00:00:00+00:00",
                        },
                        {"state": "REJECTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": None,
                    "input": None,
                    "name": "greet",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "UNREACHABLE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "UNREACHABLE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "goodbye",
                    "output": {"display": "Goodbye!"},
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "COMPLETE",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": None,
                },
            ]

            assert await handle.query("stages") == expected_stages_reject


@activity.defn(name="hello_world_prompt_activity")
async def hello_world_exception() -> str:
    raise Exception("Some failure.")


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_retries(mock_tb, mock_time):
    task_queue_name = str(uuid.uuid4())
    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[HelloWorldApproval],
            activities=[
                hello_world_exception,
                hello_world_activity,
                hello_world_reject_activity,
            ],
        ):
            handle = await env.client.start_workflow(
                HelloWorldApproval.run,
                HelloWorldInput(name="test"),
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            stages = await handle.query("stages")
            while stages[0]["state"] != "FAILED":
                await asyncio.sleep(0.1)
                stages = await handle.query("stages")

            expected_stages = [
                {
                    "approval_threshold": 1,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "prompt",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": True,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "FAILED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": "exists",
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": None,
                    "input": None,
                    "name": "greet",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": None,
                    "input": None,
                    "name": "goodbye",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
            ]

            assert stages == expected_stages

            await handle.signal("retry", "prompt")
            stages = await handle.query("stages")
            while stages[0]["state"] != "FAILED":
                await asyncio.sleep(0.1)
                stages = await handle.query("stages")

            expected_stages = [
                {
                    "approval_threshold": 1,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "prompt",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": True,
                    "retry_count": 1,
                    "retryable": True,
                    "state": "FAILED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": "exists",
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": None,
                    "input": None,
                    "name": "greet",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": None,
                    "input": None,
                    "name": "goodbye",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
            ]


@activity.defn(name="hello_world_prompt_activity")
async def hello_world_exception_non_retry() -> str:
    raise ApplicationError("Some non-retryable failure.", non_retryable=True)


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_non_retryable(mock_tb, mock_time):
    task_queue_name = str(uuid.uuid4())
    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[HelloWorldApproval],
            activities=[
                hello_world_exception_non_retry,
                hello_world_activity,
                hello_world_reject_activity,
            ],
        ):
            handle = await env.client.start_workflow(
                HelloWorldApproval.run,
                HelloWorldInput(name="test"),
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            with pytest.raises(WorkflowFailureError) as error:
                await handle.result()

            assert re.match(
                r"Workflow failed: Activity hello_world_prompt_activity:\w+ "
                r"in prompt has failed and cannot be retried: Some non-retryable failure\.",
                str(error.value.cause),
            )

            with pytest.raises(RPCError) as error:
                await handle.signal("retry", "prompt")
            assert error.value.message in {
                "Completed workflow",
                "workflow execution already completed",
            }
            stages = await handle.query("stages")
            assert stages == [
                {
                    "approval_threshold": 1,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "Ask the user if they want to be greeted",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "prompt",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": True,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "FAILED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": "exists",
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Greet the user.",
                    "execution_time": None,
                    "input": None,
                    "name": "greet",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": ["prompt"],
                    "description": "Say goodbye to the user",
                    "execution_time": None,
                    "input": None,
                    "name": "goodbye",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "NOT_STARTED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}
                    ],
                    "traceback": None,
                },
            ]


@workflow.defn
class MockHelloWorldStageFail(StageMixin):
    def __init__(self) -> None:
        StageMixin.__init__(self)
        self.define_stage(
            name="hello_world",
            description="Hello world",
            depends_on=[],
            requires_approval=False,
        )

    @stage_executor("hello_world")
    async def hello_world(self) -> None:
        raise ValueError("Error in stage code")

    @run_nv_config_manager_workflow
    async def run(self, _) -> None:
        await self.hello_world()


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_uncaught_exception_stage(mock_tb, mock_time):
    task_queue_name = str(uuid.uuid4())

    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[MockHelloWorldStageFail],
            activities=[],
        ):
            handle = await env.client.start_workflow(
                MockHelloWorldStageFail.run,
                None,
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            with pytest.raises(WorkflowFailureError) as error:
                await handle.result()

            assert (
                str(error.value.cause)
                == "Workflow failed: Unexpected exception during stage runtime: "
                "Error in stage code"
            )


@workflow.defn
class MockHelloWorldRunFail(StageMixin):
    def __init__(self) -> None:
        StageMixin.__init__(self)

    @run_nv_config_manager_workflow
    async def run(self, _) -> None:
        raise ValueError("Error in run code")


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_uncaught_exception_run(mock_tb, mock_time):
    task_queue_name = str(uuid.uuid4())

    async with await start_workflow_environment() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[MockHelloWorldRunFail],
            activities=[],
        ):
            handle = await env.client.start_workflow(
                MockHelloWorldRunFail.run,
                None,
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            with pytest.raises(WorkflowFailureError) as error:
                await handle.result()

            assert str(error.value.cause) == "Workflow failed: Error in run code"


@activity.defn
async def activity_timeout() -> None:
    await asyncio.sleep(5)


@workflow.defn
class MockHelloWorldRunActivityTimeout(StageMixin):
    def __init__(self) -> None:
        StageMixin.__init__(self)
        self.define_stage(
            name="test",
            description="test",
            requires_approval=False,
            depends_on=[],
        )

    @stage_executor("test")
    async def test(self) -> None:
        await workflow.execute_activity(
            activity_timeout,
            start_to_close_timeout=timedelta(milliseconds=1),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

    @run_nv_config_manager_workflow
    async def run(self, terminate_on_failure: bool | None) -> None:
        self.set_terminate_on_failure(bool(terminate_on_failure))
        await self.test()


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.mixin.workflow.patched", return_value=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.upsert_search_attributes")
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_workflow_activity_timeout(mock_tb, mock_upsert, mock_patched, mock_time):
    task_queue_name = str(uuid.uuid4())

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[MockHelloWorldRunActivityTimeout],
            activities=[activity_timeout],
        ):
            handle = await env.client.start_workflow(
                MockHelloWorldRunActivityTimeout.run,
                None,
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            stages = await handle.query("stages")
            while stages[0]["state"] != "FAILED":
                await asyncio.sleep(0.1)
                stages = await handle.query("stages")

            assert stages == [
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "test",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "test",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 0,
                    "retryable": True,
                    "state": "FAILED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": "exists",
                }
            ]

            await handle.signal("retry", "test")
            stages = await handle.query("stages")
            while stages[0]["state"] != "FAILED":
                await asyncio.sleep(0.1)
                stages = await handle.query("stages")
            assert stages == [
                {
                    "approval_threshold": 0,
                    "approvers": [],
                    "child_workflows": [],
                    "depends_on": [],
                    "description": "test",
                    "execution_time": 0.0,
                    "input": None,
                    "name": "test",
                    "output": None,
                    "rejecters": [],
                    "requires_approval": False,
                    "retry_count": 1,
                    "retryable": True,
                    "state": "FAILED",
                    "state_history": [
                        {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                        {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    ],
                    "traceback": "exists",
                }
            ]

            await handle.signal("retry", "test")
            with pytest.raises(WorkflowFailureError):
                await handle.result()


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch("nv_config_manager_workflows.stage.mixin.workflow.patched", return_value=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.upsert_search_attributes")
@patch("nv_config_manager_workflows.stage.executor.traceback.format_exc", return_value="exists")
async def test_workflow_terminates_on_stage_failure(mock_tb, mock_upsert, mock_patched, mock_time):
    task_queue_name = str(uuid.uuid4())

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[MockHelloWorldRunActivityTimeout],
            activities=[activity_timeout],
        ):
            handle = await env.client.start_workflow(
                MockHelloWorldRunActivityTimeout.run,
                True,
                id=str(uuid.uuid4()),
                task_queue=task_queue_name,
            )

            with pytest.raises(WorkflowFailureError) as error:
                await handle.result()

            assert "Stage test has failed and is non-retryable" in str(error.value.cause)
