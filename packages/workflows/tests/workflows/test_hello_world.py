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
"""Contracts for the package-owned Hello World workflows."""

from datetime import timedelta
from unittest.mock import AsyncMock, call, patch

from nv_config_manager_workflows.activities.hello_world import (
    hello_world_activity,
    hello_world_prompt_activity,
    hello_world_reject_activity,
)
from nv_config_manager_workflows.activities.slack import send_slack_message
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import workflow_type_name
from nv_config_manager_workflows.stage import StateEnum
from nv_config_manager_workflows.workflows.hello_world import (
    LOCAL_TEST_WORKFLOWS,
    HelloWorld,
    HelloWorldApproval,
    HelloWorldInput,
    HelloWorldRunning,
)


def test_hello_world_contracts_are_frozen() -> None:
    """Names, schema, metadata, and activity declarations survive the module move."""
    assert [
        workflow_type_name(workflow)
        for workflow in (HelloWorld, HelloWorldApproval, HelloWorldRunning)
    ] == ["HelloWorld", "HelloWorldApproval", "HelloWorldRunning"]
    assert HelloWorldInput.model_json_schema() == {
        "description": "Hello World Input Definition.",
        "properties": {
            "name": {
                "description": "Name to include in the workflow greeting.",
                "title": "Name",
                "type": "string",
            }
        },
        "required": ["name"],
        "title": "HelloWorldInput",
        "type": "object",
    }
    assert HelloWorldInput(name="Ada").model_dump(mode="json") == {"name": "Ada"}
    assert HelloWorld.get_workflow_required_activities() == (hello_world_activity,)
    assert HelloWorldApproval.get_workflow_required_activities() == (
        hello_world_prompt_activity,
        send_slack_message,
        hello_world_activity,
        hello_world_reject_activity,
    )
    assert LOCAL_TEST_WORKFLOWS == (HelloWorldRunning,)
    assert not issubclass(HelloWorldRunning, WorkflowMetadataMixin)


async def test_local_workflow_preserves_stage_transitions_and_ten_year_timer() -> None:
    """The opt-in latency fixture remains running across its exact 3,650-day sleep."""
    with patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=0.0):
        instance = HelloWorldRunning()
        original_set_stage_state = instance.set_stage_state
        sleep = AsyncMock()

        with (
            patch.object(instance, "set_stage_state", wraps=original_set_stage_state) as set_state,
            patch(
                "nv_config_manager_workflows.stage.mixin.workflow.patched",
                return_value=False,
            ),
            patch("nv_config_manager_workflows.workflows.hello_world.workflow.sleep", sleep),
        ):
            assert await instance.run(HelloWorldInput(name="latency-fixture")) == "latency-fixture"

    assert set_state.call_args_list == [
        call("running", StateEnum.IN_PROGRESS),
        call("running", StateEnum.COMPLETE),
    ]
    sleep.assert_awaited_once_with(timedelta(days=3650))
