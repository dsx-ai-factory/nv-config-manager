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
"""Require replay histories for every production workflow type."""

from pathlib import Path

from temporalio.client import WorkflowHistory

from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
from nv_config_manager_workflows.registration.contract import workflow_type_name

_FIXTURE_ROOTS = (
    Path(__file__).parent / "fixtures",
    Path(__file__).parents[2] / "hello_world" / "workflows" / "fixtures",
)


def _fixture_workflow_type(path: Path) -> str:
    """Return the workflow type embedded in a captured history fixture."""
    history = WorkflowHistory.from_json(path.stem, path.read_text())
    for event in history.events:
        if event.HasField("workflow_execution_started_event_attributes"):
            return event.workflow_execution_started_event_attributes.workflow_type.name
    raise AssertionError(f"Replay fixture has no workflow start event: {path}")


def test_every_registered_workflow_has_a_replay_history() -> None:
    """Keep production workflow registration and replay coverage in sync."""
    registered_types = {
        workflow_type_name(workflow) for workflow in [*NGC_WORKFLOWS, *HELLO_WORLD_WORKFLOWS]
    }
    assert None not in registered_types

    fixture_paths = [path for root in _FIXTURE_ROOTS for path in root.glob("*.json")]
    fixture_types = {_fixture_workflow_type(path) for path in fixture_paths}

    assert fixture_types == registered_types
