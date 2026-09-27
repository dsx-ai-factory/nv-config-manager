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
"""Replay contracts for diagnostics and ticketing workflows."""

from pathlib import Path
from typing import Any

import pytest
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.diagnostics import DiagnosticsWorkflow

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_EXPECTED_ACTIVITY_NAMES = {
    "diagnostics_ticketed.json": [
        "validate_ticket",
        "get_network_device",
        "run_diagnostic_commands",
        "collect_tech_support_bundle",
        "upload_attachment",
        "upload_tech_support_from_redis",
        "add_ticket_comment",
        "publish_nats",
    ],
    "diagnostics_ticketless.json": [
        "get_network_device",
        "run_diagnostic_commands",
        "publish_nats",
    ],
}


def _history(filename: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (_FIXTURE_ROOT / filename).read_text(),
    )


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def _scheduled_inputs(filename: str) -> list[tuple[str, dict[str, Any]]]:
    converter = get_data_converter()
    decoded = []
    for item in _scheduled_activities(_history(filename)):
        payloads = await converter.decode(item.input.payloads)
        decoded.append((item.activity_type.name, payloads[0] if payloads else {}))
    return decoded


def _input_for(
    scheduled: list[tuple[str, dict[str, Any]]],
    activity_name: str,
) -> dict[str, Any]:
    [activity_input] = [value for name, value in scheduled if name == activity_name]
    return activity_input


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_diagnostics_histories_replay(history_filename: str) -> None:
    """Package extraction remains deterministic against captured diagnostics histories."""
    replayer = Replayer(workflows=[DiagnosticsWorkflow], data_converter=get_data_converter())

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_diagnostics_histories_preserve_activity_order(history_filename: str) -> None:
    """Captured histories independently freeze scheduled activity type names."""
    scheduled = await _scheduled_inputs(history_filename)

    assert [name for name, _ in scheduled] == _EXPECTED_ACTIVITY_NAMES[history_filename]


@pytest.mark.asyncio
async def test_ticketed_history_preserves_serialized_arguments() -> None:
    """The full branch retains command, bundle, attachment, and ticket payloads."""
    scheduled = await _scheduled_inputs("diagnostics_ticketed.json")

    validation = _input_for(scheduled, "validate_ticket")
    diagnostics = _input_for(scheduled, "run_diagnostic_commands")
    tech_support = _input_for(scheduled, "collect_tech_support_bundle")
    attachment = _input_for(scheduled, "upload_attachment")
    tech_upload = _input_for(scheduled, "upload_tech_support_from_redis")
    comment = _input_for(scheduled, "add_ticket_comment")

    assert validation == {"ticketing_platform": "jira", "issue_key": "GNI-1234"}
    assert diagnostics["commands"] == ["show_version"]
    assert diagnostics["device_data"]["id"] == "aaaa0001-0000-0000-0000-000000000001"
    assert tech_support["device_data"]["id"] == "aaaa0001-0000-0000-0000-000000000001"
    assert attachment["ticketing_platform"] == "jira"
    assert attachment["issue_key"] == "GNI-1234"
    assert attachment["filename"].startswith("diagnostics_GNI-1234_")
    assert attachment["content_type"] == "text/plain"
    assert tech_upload == {
        "ticketing_platform": "jira",
        "issue_key": "GNI-1234",
        "device_name": "switch-aaaa",
        "redis_key": "tech_support:mock-workflow:switch-aaaa",
    }
    assert comment["ticketing_platform"] == "jira"
    assert comment["issue_key"] == "GNI-1234"
    assert "Diagnostics workflow completed" in comment["body"]


@pytest.mark.asyncio
async def test_ticketless_history_preserves_branching_and_arguments() -> None:
    """Explicit ticketless mode schedules no ticketing or tech-support activities."""
    scheduled = await _scheduled_inputs("diagnostics_ticketless.json")

    diagnostics = _input_for(scheduled, "run_diagnostic_commands")
    assert diagnostics["commands"] == ["show_version"]
    assert diagnostics["device_data"]["id"] == "aaaa0001-0000-0000-0000-000000000001"
    assert not {
        "validate_ticket",
        "collect_tech_support_bundle",
        "upload_attachment",
        "upload_tech_support_from_redis",
        "add_ticket_comment",
    }.intersection(name for name, _ in scheduled)
