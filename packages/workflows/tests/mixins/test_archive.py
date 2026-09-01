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
"""Workflow result archival contract tests."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from temporalio import workflow

from nv_config_manager_workflows.mixins import (
    ArchiveMixin,
    WorkflowResultLog,
)


def workflow_info() -> SimpleNamespace:
    """Return the workflow-info fields used by the archive contract."""
    return SimpleNamespace(
        workflow_id="workflow-17",
        workflow_type="BackupWorkflow",
        start_time=datetime(2026, 8, 31, 12, 30, tzinfo=UTC),
        search_attributes={"Site": ["rdu"]},
    )


def test_result_log_preserves_the_wire_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow,
        "now",
        lambda: datetime(2026, 8, 31, 12, 45, tzinfo=UTC),
    )

    result = WorkflowResultLog.from_workflow_info(cast(workflow.Info, workflow_info()))

    assert result.model_dump(mode="json") == {
        "workflow_id": "workflow-17",
        "workflow_type": "BackupWorkflow",
        "start_time": "2026-08-31T12:30:00+00:00",
        "publish_time": "2026-08-31T12:45:00+00:00",
        "search_attributes": {"Site": ["rdu"]},
    }


@pytest.mark.asyncio
async def test_archive_schedules_the_existing_activity_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def execute_activity(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(workflow, "info", workflow_info)
    monkeypatch.setattr(
        workflow,
        "now",
        lambda: datetime(2026, 8, 31, 12, 45, tzinfo=UTC),
    )
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)

    await ArchiveMixin().archive_results()

    (activity_name, activity_input), options = calls[0]
    assert activity_name == "publish_nats"
    assert activity_input["subject"] is None
    assert json.loads(activity_input["message"])["workflow_id"] == "workflow-17"
    assert options["schedule_to_close_timeout"] == timedelta(minutes=1)
    assert options["retry_policy"].maximum_attempts == 1
