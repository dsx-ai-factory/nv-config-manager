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
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, RetryState

from nv_config_manager_workflows.metadata import RequiredActivity, WorkflowMetadataMixin
from nv_config_manager_workflows.mixins import (
    ArchiveMixin,
    WorkflowResultLog,
)
from nv_config_manager_workflows.mixins.archive import PUBLISH_NATS_ACTIVITY_NAME


def workflow_info() -> SimpleNamespace:
    """Return the workflow-info fields used by the archive contract."""
    return SimpleNamespace(
        workflow_id="workflow-17",
        workflow_type="BackupWorkflow",
        start_time=datetime(2026, 8, 31, 12, 30, tzinfo=UTC),
        search_attributes={"Site": ["rdu"]},
        namespace="default",
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
        "namespace": "default",
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
    assert options["retry_policy"].maximum_attempts == 3


def test_the_publisher_is_declared_as_a_required_activity() -> None:
    """The activity is executed by name, so registration must catch its absence."""

    class ArchivedWorkflow(WorkflowMetadataMixin, ArchiveMixin):
        workflow_required_activities: Sequence[RequiredActivity] = ("collect_facts",)

    assert ArchivedWorkflow.get_workflow_required_activities() == (
        PUBLISH_NATS_ACTIVITY_NAME,
        "collect_facts",
    )


def _activity_error(cause: BaseException, retry_state: RetryState) -> ActivityError:
    error = ActivityError(
        "publish_nats failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type=PUBLISH_NATS_ACTIVITY_NAME,
        activity_id="1",
        retry_state=retry_state,
    )
    error.__cause__ = cause
    return error


def _patch_workflow(monkeypatch: pytest.MonkeyPatch, execute_activity: Any) -> MagicMock:
    warning = MagicMock()
    monkeypatch.setattr(workflow, "info", workflow_info)
    monkeypatch.setattr(
        workflow,
        "now",
        lambda: datetime(2026, 8, 31, 12, 45, tzinfo=UTC),
    )
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "logger", SimpleNamespace(warning=warning))
    return warning


@pytest.mark.asyncio
async def test_a_failed_publish_does_not_fail_the_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run has already finished, so a broker outage must not fail it."""
    cause = ApplicationError("nats unreachable")

    async def execute_activity(*_args: Any, **_kwargs: Any) -> None:
        raise _activity_error(cause, RetryState.MAXIMUM_ATTEMPTS_REACHED)

    warning = _patch_workflow(monkeypatch, execute_activity)

    await ArchiveMixin().archive_results()

    warning.assert_called_once()
    assert warning.call_args.args[-1] is cause


@pytest.mark.asyncio
async def test_a_cancelled_publish_still_cancels_the_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is not a broker failure, so Temporal must still see it."""

    async def execute_activity(*_args: Any, **_kwargs: Any) -> None:
        raise _activity_error(CancelledError("cancelled"), RetryState.CANCEL_REQUESTED)

    warning = _patch_workflow(monkeypatch, execute_activity)

    with pytest.raises(ActivityError):
        await ArchiveMixin().archive_results()

    warning.assert_not_called()


@pytest.mark.asyncio
async def test_an_error_that_is_not_a_failed_activity_fails_the_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a failed publish is a lost notification; anything else is a defect."""

    async def execute_activity(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("workflow sandbox violation")

    warning = _patch_workflow(monkeypatch, execute_activity)

    with pytest.raises(RuntimeError):
        await ArchiveMixin().archive_results()

    warning.assert_not_called()


@pytest.mark.asyncio
async def test_a_payload_that_cannot_be_built_fails_the_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result that cannot be serialized must not look like a broker outage."""
    execute_activity = MagicMock()
    warning = _patch_workflow(monkeypatch, execute_activity)

    def broken_info() -> Any:
        raise ValueError("no workflow info")

    monkeypatch.setattr(workflow, "info", broken_info)

    with pytest.raises(ValueError, match="no workflow info"):
        await ArchiveMixin().archive_results()

    execute_activity.assert_not_called()
    warning.assert_not_called()
