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
"""Simple Hello World Workflow Definition."""

from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.hello_world.activities.hello_world import (
    hello_world_activity,
    hello_world_prompt_activity,
    hello_world_reject_activity,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.ngc.activities.slack import (
        SlackMessageInput,
        send_slack_message,
    )

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=1)


class HelloWorldInput(BaseModel):
    """Hello World Input Definition."""

    name: str = Field(description="Name to include in the workflow greeting.")


@workflow.defn
class HelloWorld(WorkflowMetadataMixin, StageMixin):
    """Simple hello world workflow for testing."""

    # Workflow metadata
    workflow_name = "Hello World"
    workflow_description = "Simple hello world workflow for testing and demonstration"
    workflow_input_class = HelloWorldInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/hello_world"
    workflow_namespace = "hello_world"

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="hello_world",
            description="Hello world",
            depends_on=[],
            requires_approval=False,
        )

    class HelloWorldStageInput(StageInput):
        """Hello World stage input."""

        name: str

    class HelloWorldStageOutput(StageOutput):
        """Hello World stage output."""

    @stage_executor("hello_world")
    async def hello_world(self, stage_input: HelloWorldStageInput) -> HelloWorldStageOutput:
        """Hello World stage."""
        result = await workflow.execute_activity(
            hello_world_activity,
            stage_input.name,
            schedule_to_close_timeout=timedelta(seconds=5),
        )
        return self.HelloWorldStageOutput(display=result)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: HelloWorldInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Hello World workflow execution."""
        result = await self.hello_world(self.HelloWorldStageInput(name=workflow_input.name))
        return result.display


@workflow.defn
class HelloWorldRunning(StageMixin):
    """Long-running workflow for local workflow-list latency testing."""

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="running",
            description="Long-running non-approval latency fixture",
            depends_on=[],
            requires_approval=False,
        )

    @workflow.run
    async def run(self, workflow_input: HelloWorldInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Run long enough to remain visible as a non-pending running workflow."""
        self.set_input(workflow_input)
        self.set_stage_state("running", StateEnum.IN_PROGRESS)
        await workflow.sleep(timedelta(days=3650))
        self.set_stage_state("running", StateEnum.COMPLETE)
        return workflow_input.name


class PromptStageOutput(StageOutput):
    """Prompt Stage Output."""

    approved: bool


class GreetStageInput(StageInput):
    """Greet Stage Input."""

    name: str


class GreetStageOutput(StageOutput):
    """Greet Stage Output."""


class GoodbyeStageOutput(StageOutput):
    """Goodbye Stage Output."""


@workflow.defn
class HelloWorldApproval(WorkflowMetadataMixin, StageMixin):
    """Hello world workflow with approval step."""

    # Workflow metadata
    workflow_name = "Hello World Approval"
    workflow_description = "Hello world workflow with approval step for testing staged workflows"
    workflow_input_class = HelloWorldInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/hello_world_approval"
    workflow_namespace = "hello_world"

    def __init__(self) -> None:
        """Initialize workflow with no approvers."""
        StageMixin.__init__(self)

        self.define_stage(
            name="prompt",
            description="Ask the user if they want to be greeted",
            requires_approval=True,
            depends_on=[],
            approval_threshold=1,
        )
        self.define_stage(
            name="greet",
            description="Greet the user.",
            requires_approval=False,
            depends_on=["prompt"],
        )
        self.define_stage(
            name="goodbye",
            description="Say goodbye to the user",
            requires_approval=False,
            depends_on=["prompt"],
        )

    @stage_executor("prompt")
    async def prompt(self) -> PromptStageOutput:
        """Prompt the user for greeting."""
        output = await workflow.execute_activity(
            hello_world_prompt_activity,
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        self.set_stage_output("prompt", PromptStageOutput(display=output, approved=False))
        self.set_stage_state("prompt", StateEnum.PENDING_APPROVAL)
        slack_output = await workflow.execute_activity(
            send_slack_message,
            SlackMessageInput(
                message=f"⏳ Prompt stage is pending approval: {output}",
                link_workflow=True,
            ),
            schedule_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        await workflow.wait_condition(
            lambda: self.get_stage_state("prompt") != StateEnum.PENDING_APPROVAL
        )
        approved = self.get_stage_state("prompt") == StateEnum.APPROVED
        await workflow.execute_activity(
            send_slack_message,
            SlackMessageInput(
                message=f"{'✅' if approved else '❌'} Prompt stage approval result: {approved}",
                thread_ts=slack_output.thread_ts,
            ),
            schedule_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return PromptStageOutput(display=output, approved=approved)

    @stage_executor("greet")
    async def greet(self, stage_input: GreetStageInput) -> GreetStageOutput:
        """Greet the user."""
        result = await workflow.execute_activity(
            hello_world_activity,
            stage_input.name,
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return GreetStageOutput(display=result)

    @stage_executor("goodbye")
    async def goodbye(self) -> GoodbyeStageOutput:
        """Say goodbye to the user."""
        result = await workflow.execute_activity(
            hello_world_reject_activity,
            schedule_to_close_timeout=timedelta(seconds=5),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        return GoodbyeStageOutput(display=result)

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: HelloWorldInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Hello World with approval workflow execution."""
        self.set_input(workflow_input)

        prompt_output = await self.prompt()

        if prompt_output.approved:
            self.set_stage_state("goodbye", StateEnum.UNREACHABLE)
            greet_output = await self.greet(GreetStageInput(name=workflow_input.name))
            return greet_output.display

        self.set_stage_state("greet", StateEnum.UNREACHABLE)
        goodbye_output = await self.goodbye()
        return goodbye_output.display
