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
"""Tests for the workflow run decorator's lock wiring.

Drives the decorator against stand-ins for the Temporal runtime rather than a
workflow environment, so the package's standalone test job stays free of the
test-server download. End-to-end coverage against a real worker lives with the
service tests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.decorators import workflow as decorator_module
from nv_config_manager_workflows.decorators.workflow import (
    WorkflowRuntimeFailure,
    run_nv_config_manager_workflow,
)
from nv_config_manager_workflows.metadata import WorkflowLockSpec, WorkflowMetadataMixin

WORKFLOW_ID = "run-1"


class _ProbeInput(BaseModel):
    resource: str
    fail: bool = False


class _Trace(list[str]):
    """Ordered record of lock activities and body execution."""


TRACE: _Trace = _Trace()
ACTIVITY_CALLS: list[dict[str, Any]] = []


class _UnlockedProbe:
    """A workflow that declares no lock."""

    workflow_name = "Unlocked Probe"
    workflow_namespace = "test"

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        TRACE.append("body")
        return f"ran:{workflow_input.resource}"


class _LockedProbe(WorkflowMetadataMixin):
    """A workflow that opts into the per-resource lock."""

    workflow_name = "Locked Probe"
    workflow_namespace = "test"
    workflow_lock = WorkflowLockSpec(
        key_fields=["resource"], ttl_seconds=60, renew_interval_seconds=10
    )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        TRACE.append("body")
        if workflow_input.fail:
            raise ApplicationError("probe failure", non_retryable=True)
        return f"ran:{workflow_input.resource}"


class _FailFastProbe(WorkflowMetadataMixin):
    """A workflow that refuses to queue behind another holder."""

    workflow_name = "Fail Fast Probe"
    workflow_namespace = "test"
    workflow_lock = WorkflowLockSpec(key_fields=["resource"], on_conflict="fail")

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        TRACE.append("body")
        return "ran"


@pytest.fixture(autouse=True)
def temporal_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Temporal calls the decorator makes with local stand-ins."""
    TRACE.clear()
    ACTIVITY_CALLS.clear()

    async def _execute_activity(activity: Any, arg: Any, **kwargs: Any) -> None:
        name = activity.__name__
        TRACE.append(name)
        ACTIVITY_CALLS.append({"name": name, "input": arg, **kwargs})

    async def _never_renew(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(workflow, "execute_activity", _execute_activity)
    monkeypatch.setattr(workflow, "wait", asyncio.wait)
    monkeypatch.setattr(workflow, "info", lambda: type("Info", (), {"workflow_id": WORKFLOW_ID})())
    monkeypatch.setattr(decorator_module, "_renew_loop", _never_renew)


@pytest.fixture
def patched_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the lock patch as applied, as a fresh history would."""
    monkeypatch.setattr(workflow, "patched", lambda _patch_id: True)


@pytest.fixture
def legacy_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the lock patch as absent, as a pre-patch history would."""
    monkeypatch.setattr(workflow, "patched", lambda _patch_id: False)


def _call(name: str) -> dict[str, Any]:
    return next(call for call in ACTIVITY_CALLS if call["name"] == name)


class TestWithoutALockSpec:
    async def test_body_runs_with_no_lock_activities(self, patched_history: None) -> None:
        result = await _UnlockedProbe().run(_ProbeInput(resource="r1"))

        assert result == "ran:r1"
        assert TRACE == ["body"]


class TestReplayGate:
    async def test_pre_patch_history_takes_the_unlocked_path(self, legacy_history: None) -> None:
        """Executions started before the lock existed must not gain lock commands."""
        result = await _LockedProbe().run(_ProbeInput(resource="r1"))

        assert result == "ran:r1"
        assert TRACE == ["body"]


class TestWithALockSpec:
    async def test_lock_is_held_around_the_body(self, patched_history: None) -> None:
        result = await _LockedProbe().run(_ProbeInput(resource="r1"))

        assert result == "ran:r1"
        assert TRACE == ["acquire_workflow_lock", "body", "release_workflow_lock"]

    async def test_key_and_token_identify_the_resource_and_run(self, patched_history: None) -> None:
        await _LockedProbe().run(_ProbeInput(resource="r1"))

        acquire = _call("acquire_workflow_lock")["input"]
        release = _call("release_workflow_lock")["input"]
        assert acquire.key == "wf-lock:test:resource=r1"
        assert acquire.token == WORKFLOW_ID
        assert release.key == acquire.key
        assert release.token == acquire.token

    async def test_lock_is_released_when_the_body_fails(self, patched_history: None) -> None:
        with pytest.raises(WorkflowRuntimeFailure):
            await _LockedProbe().run(_ProbeInput(resource="r2", fail=True))

        assert TRACE == ["acquire_workflow_lock", "body", "release_workflow_lock"]
        assert _call("release_workflow_lock")["input"].key == "wf-lock:test:resource=r2"

    async def test_body_failure_keeps_its_non_retryable_verdict(self, patched_history: None) -> None:
        with pytest.raises(WorkflowRuntimeFailure) as exc:
            await _LockedProbe().run(_ProbeInput(resource="r2", fail=True))

        assert exc.value.non_retryable is True

    async def test_waiting_spec_lets_acquire_retry(self, patched_history: None) -> None:
        """A waiter retries until the holder releases; 0 means unlimited."""
        await _LockedProbe().run(_ProbeInput(resource="r1"))

        assert _call("acquire_workflow_lock")["retry_policy"].maximum_attempts == 0

    async def test_fail_on_conflict_spec_acquires_once(self, patched_history: None) -> None:
        await _FailFastProbe().run(_ProbeInput(resource="r1"))

        acquire = _call("acquire_workflow_lock")
        assert acquire["retry_policy"].maximum_attempts == 1
        assert acquire["input"].fail_on_conflict is True
