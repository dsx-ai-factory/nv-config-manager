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
"""Replay contract for extracted Hello World activities."""

from pathlib import Path

from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.hello_world.workflows.hello_world_workflow import (
    HelloWorldApproval,
)
from nv_config_manager_workflows.activities.slack import SlackMessageInput

_HISTORY_PATH = Path(__file__).parent / "fixtures" / "hello_world_approval.json"


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    """Return the activity scheduling attributes recorded in the fixture."""
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def test_hello_world_approval_history_replays() -> None:
    """Package import moves do not change commands emitted for this history."""
    history = WorkflowHistory.from_json("hello_world_approval", _HISTORY_PATH.read_text())
    replayer = Replayer(
        workflows=[HelloWorldApproval],
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(history)


async def test_hello_world_approval_history_preserves_activity_names_and_arguments() -> None:
    """The fixture freezes activity type names and their serialized call arguments."""
    history = WorkflowHistory.from_json("hello_world_approval", _HISTORY_PATH.read_text())
    scheduled = _scheduled_activities(history)
    names = [attributes.activity_type.name for attributes in scheduled]

    assert names == [
        "hello_world_prompt_activity",
        "send_slack_message",
        "send_slack_message",
        "hello_world_activity",
    ]

    converter = get_data_converter()
    first_slack = await converter.decode(
        scheduled[1].input.payloads,
        [SlackMessageInput],
    )
    second_slack = await converter.decode(
        scheduled[2].input.payloads,
        [SlackMessageInput],
    )
    greeting = await converter.decode(scheduled[3].input.payloads, [str])

    assert first_slack == [
        SlackMessageInput(
            message="⏳ Prompt stage is pending approval: Would you like to be greeted?",
            link_workflow=True,
        )
    ]
    assert second_slack == [
        SlackMessageInput(
            message="✅ Prompt stage approval result: True",
            thread_ts="captured-thread",
        )
    ]
    assert greeting == ["replay-user"]
