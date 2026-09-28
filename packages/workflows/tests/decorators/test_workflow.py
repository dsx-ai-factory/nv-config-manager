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
"""Tests for the reusable workflow run decorator and lock orchestration."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.lock import (
    AcquireWorkflowLockInput,
    ReleaseWorkflowLockInput,
    RenewWorkflowLockInput,
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)
from nv_config_manager_workflows.decorators import workflow as decorator_module
from nv_config_manager_workflows.decorators.workflow import (
    WorkflowRuntimeFailure,
    run_nv_config_manager_workflow,
)
from nv_config_manager_workflows.metadata import WorkflowLockSpec


class _ProbeInput(BaseModel):
    resource: str


class _LockedWorkflow:
    workflow_name = "Locked Probe"
    workflow_namespace = "test"

    def get_workflow_lock(self) -> WorkflowLockSpec:
        """Return a representative per-resource lock declaration."""
        return WorkflowLockSpec(
            key_fields=["resource"],
            ttl_seconds=120,
            renew_interval_seconds=45,
            wait_timeout_seconds=10,
        )


class _UnlockedWorkflow:
    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        """Return the requested resource without declaring a lock."""
        return workflow_input.resource


class _FailureWorkflow:
    def __init__(self, failure: Exception) -> None:
        """Store the failure the workflow body should raise."""
        self.failure = failure

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> None:
        """Raise the configured failure."""
        raise self.failure


class _PatchGateWorkflow(_LockedWorkflow):
    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        """Return the resource after the decorator evaluates the patch gate."""
        return workflow_input.resource


async def test_decorator_returns_unlocked_workflow_result() -> None:
    """A workflow without a lock declaration runs without consulting the patch gate."""
    with patch.object(decorator_module.workflow, "patched") as patched:
        result = await _UnlockedWorkflow().run(_ProbeInput(resource="leaf-01"))

    assert result == "leaf-01"
    patched.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "non_retryable"),
    [
        (ValueError("unexpected"), True),
        (ApplicationError("retryable", non_retryable=False), False),
        (ApplicationError("permanent", non_retryable=True), True),
    ],
)
async def test_decorator_preserves_failure_classification(
    failure: Exception,
    non_retryable: bool,
) -> None:
    """Workflow failures keep the established Temporal retry classification."""
    with pytest.raises(WorkflowRuntimeFailure, match=f"Workflow failed: {failure}") as error:
        await _FailureWorkflow(failure).run(_ProbeInput(resource="leaf-01"))

    assert error.value.non_retryable is non_retryable
    assert error.value.__cause__ is failure


async def test_patch_gate_bypasses_locking_for_pre_lock_histories() -> None:
    """A history that has not taken the existing patch continues without lock commands."""
    run_with_lock = AsyncMock()

    with (
        patch.object(decorator_module.workflow, "patched", return_value=False) as patched,
        patch.object(decorator_module, "_run_with_lock", new=run_with_lock),
    ):
        result = await _PatchGateWorkflow().run(_ProbeInput(resource="leaf-01"))

    assert result == "leaf-01"
    patched.assert_called_once_with("nvcm-workflow-lock-v1")
    run_with_lock.assert_not_awaited()


@pytest.mark.parametrize(
    ("on_conflict", "maximum_attempts", "fail_on_conflict"),
    [("wait", 0, False), ("fail", 1, True)],
)
async def test_acquire_preserves_timeout_and_retry_policy(
    on_conflict: Literal["wait", "fail"],
    maximum_attempts: int,
    fail_on_conflict: bool,
) -> None:
    """Acquisition emits the same activity input, timeout, and retry policy."""
    execute_activity = AsyncMock()
    spec = WorkflowLockSpec(
        key_fields=["resource"],
        ttl_seconds=120,
        renew_interval_seconds=45,
        wait_timeout_seconds=10,
        on_conflict=on_conflict,
    )

    with patch.object(decorator_module.workflow, "execute_activity", new=execute_activity):
        await decorator_module._acquire_lock("lock-key", "run-id", spec)

    args, kwargs = execute_activity.call_args
    assert args == (
        acquire_workflow_lock,
        AcquireWorkflowLockInput(
            key="lock-key",
            token="run-id",
            ttl_seconds=120,
            wait_timeout_seconds=10,
            fail_on_conflict=fail_on_conflict,
        ),
    )
    assert kwargs["start_to_close_timeout"] == timedelta(seconds=40)
    assert kwargs["retry_policy"].maximum_attempts == maximum_attempts


async def test_renewal_preserves_schedule_and_retry_policy() -> None:
    """Renewal leaves enough lease headroom and retains three activity attempts."""
    execute_activity = AsyncMock()
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    spec = WorkflowLockSpec(
        key_fields=["resource"],
        ttl_seconds=120,
        renew_interval_seconds=45,
    )

    with (
        patch.object(decorator_module.workflow, "execute_activity", new=execute_activity),
        patch.object(decorator_module.asyncio, "sleep", new=sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        await decorator_module._renew_loop("lock-key", "run-id", spec)

    args, kwargs = execute_activity.call_args
    assert args == (
        renew_workflow_lock,
        RenewWorkflowLockInput(key="lock-key", token="run-id", ttl_seconds=120),
    )
    assert kwargs["start_to_close_timeout"] == timedelta(seconds=30)
    assert kwargs["schedule_to_close_timeout"] == timedelta(seconds=60)
    assert kwargs["retry_policy"].maximum_attempts == 3


async def test_release_preserves_timeout_and_retry_policy() -> None:
    """Release retains its best-effort activity timeout and retry count."""
    execute_activity = AsyncMock()

    with patch.object(decorator_module.workflow, "execute_activity", new=execute_activity):
        await decorator_module._release_lock("lock-key", "run-id")

    args, kwargs = execute_activity.call_args
    assert args == (
        release_workflow_lock,
        ReleaseWorkflowLockInput(key="lock-key", token="run-id"),
    )
    assert kwargs["start_to_close_timeout"] == timedelta(seconds=30)
    assert kwargs["retry_policy"].maximum_attempts == 3


async def test_release_failure_is_suppressed() -> None:
    """Release failure does not replace the workflow body's result or exception."""
    execute_activity = AsyncMock(side_effect=RuntimeError("Temporal unavailable"))
    warning = Mock()

    with (
        patch.object(decorator_module.workflow, "execute_activity", new=execute_activity),
        patch.object(decorator_module.workflow.logger, "warning", new=warning),
    ):
        await decorator_module._release_lock("lock-key", "run-id")

    warning.assert_called_once_with(
        "Failed to release workflow lock %s; TTL will expire it",
        "lock-key",
    )


async def test_run_with_lock_orders_acquire_body_and_release() -> None:
    """The workflow body runs only while its deterministic lock is held."""
    events: list[str] = []
    acquire = AsyncMock(side_effect=lambda *_args: events.append("acquire"))
    release = AsyncMock(side_effect=lambda *_args: events.append("release"))

    async def renew(*_args: object) -> None:
        await asyncio.Event().wait()

    async def body(instance: object, workflow_input: _ProbeInput) -> str:
        events.append("body")
        return workflow_input.resource

    instance = _LockedWorkflow()
    workflow_input = _ProbeInput(resource="leaf-01")

    with (
        patch.object(decorator_module, "_acquire_lock", new=acquire),
        patch.object(decorator_module, "_renew_loop", new=renew),
        patch.object(decorator_module, "_release_lock", new=release),
        patch.object(
            decorator_module.workflow,
            "info",
            return_value=SimpleNamespace(workflow_id="run-id"),
        ),
        patch.object(decorator_module.workflow, "wait", new=asyncio.wait),
    ):
        result = await decorator_module._run_with_lock(
            body,
            (instance, workflow_input),
            instance.get_workflow_lock(),
        )

    assert result == "leaf-01"
    assert events == ["acquire", "body", "release"]
    acquire.assert_awaited_once_with(
        "wf-lock:test:resource=leaf-01",
        "run-id",
        instance.get_workflow_lock(),
    )
    release.assert_awaited_once_with("wf-lock:test:resource=leaf-01", "run-id")


async def test_unexpected_renewal_completion_fails_and_releases() -> None:
    """A stopped renewal loop fails the run and still releases its lease."""
    release = AsyncMock()

    async def body(instance: object, workflow_input: _ProbeInput) -> None:
        await asyncio.Event().wait()

    async def renew(*_args: object) -> None:
        return None

    instance = _LockedWorkflow()

    with (
        patch.object(decorator_module, "_acquire_lock", new=AsyncMock()),
        patch.object(decorator_module, "_renew_loop", new=renew),
        patch.object(decorator_module, "_release_lock", new=release),
        patch.object(
            decorator_module.workflow,
            "info",
            return_value=SimpleNamespace(workflow_id="run-id"),
        ),
        patch.object(decorator_module.workflow, "wait", new=asyncio.wait),
        pytest.raises(WorkflowRuntimeFailure, match="renewal ended unexpectedly"),
    ):
        await decorator_module._run_with_lock(
            body,
            (instance, _ProbeInput(resource="leaf-01")),
            instance.get_workflow_lock(),
        )

    release.assert_awaited_once_with("wf-lock:test:resource=leaf-01", "run-id")
