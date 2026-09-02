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
"""Decorators for workflow methods."""

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from datetime import timedelta
from functools import wraps
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, TemporalError

from nv_config_manager_workflows.metadata import (
    LOCK_RENEW_BUFFER_SECONDS,
    WorkflowLockSpec,
    build_workflow_lock_key,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_workflows.activities.lock import (
        AcquireWorkflowLockInput,
        ReleaseWorkflowLockInput,
        RenewWorkflowLockInput,
        acquire_workflow_lock,
        release_workflow_lock,
        renew_workflow_lock,
    )

F = TypeVar("F", bound=Callable[..., Any])

# Give lock activities headroom over their blocking window for scheduling overhead.
_LOCK_ACTIVITY_BUFFER_S = 30
_RENEW_ACTIVITY_TIMEOUT_S = 30
_RELEASE_ACTIVITY_TIMEOUT_S = 30

# Waiters keep retrying (with backoff) until the holder releases the lock.
_WAIT_RETRY_POLICY = RetryPolicy()
_FAIL_FAST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
_RENEW_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
_RELEASE_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


# Gate the workflow-level lock behind a Temporal patch so executions started
# before the lock existed replay deterministically
# See https://docs.temporal.io/patching.
_WORKFLOW_LOCK_PATCH_ID = "nvcm-workflow-lock-v1"


class WorkflowRuntimeFailure(ApplicationError):
    """Failures raised during workflow execution."""


def run_nv_config_manager_workflow[F: Callable[..., Any]](func: F) -> F:
    """Decorator for the workflow run method.

    Override of the temporalio.workflow.run decorator to raise
    uncaught exceptions as WorkflowRuntimeFailure which will cause
    workflow to enter a failed state rather than to suspend.
    """

    @workflow.run
    @wraps(func)
    async def _run(*args: object) -> Any:
        spec = _resolve_lock_spec(args[0]) if args else None
        try:
            if spec is None or not workflow.patched(_WORKFLOW_LOCK_PATCH_ID):
                return await func(*args)
            return await _run_with_lock(func, args, spec)
        except Exception as error:
            non_retryable = False
            if isinstance(error, ApplicationError):
                non_retryable = error.non_retryable
            elif not isinstance(error, TemporalError):
                # Unexpected exceptions likely indicate a bug in the workflow code
                non_retryable = True
            raise WorkflowRuntimeFailure(
                f"Workflow failed: {error}",
                non_retryable=non_retryable,
            ) from error

    return cast(F, _run)


def _resolve_lock_spec(instance: object) -> WorkflowLockSpec | None:
    """Read the lock spec off a workflow instance, if it declares one."""
    getter = getattr(instance, "get_workflow_lock", None)
    return getter() if callable(getter) else None


def _workflow_name(instance: object) -> str:
    """Human/registered workflow name used to scope a per-workflow lock key."""
    return getattr(type(instance), "workflow_name", None) or type(instance).__name__


async def _run_with_lock(
    func: Callable[..., Any], args: tuple[object, ...], spec: WorkflowLockSpec
) -> Any:
    """Hold a per-resource lock for the whole run, renewing it until completion."""
    key = build_workflow_lock_key(
        spec,
        workflow_name=_workflow_name(args[0]),
        namespace=getattr(type(args[0]), "workflow_namespace", None),
        workflow_input=cast(BaseModel, args[1]),
    )
    token = workflow.info().workflow_id

    await _acquire_lock(key, token, spec)

    body_task = asyncio.ensure_future(func(*args))
    renew_task = asyncio.ensure_future(_renew_loop(key, token, spec))
    try:
        await workflow.wait([body_task, renew_task], return_when=asyncio.FIRST_COMPLETED)
        if body_task.done():
            return body_task.result()
        renew_task.result()
        raise WorkflowRuntimeFailure("Workflow lock renewal ended unexpectedly")
    finally:
        await _cancel(renew_task)
        await _cancel(body_task)
        await _release_lock(key, token)


async def _cancel(task: asyncio.Future[Any]) -> None:
    """Cancel a task and absorb its resulting cancellation/errors."""
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _acquire_lock(key: str, token: str, spec: WorkflowLockSpec) -> None:
    """Block until the per-resource lock is held (or fail fast on conflict)."""
    fail_on_conflict = spec.on_conflict == "fail"
    await workflow.execute_activity(
        acquire_workflow_lock,
        AcquireWorkflowLockInput(
            key=key,
            token=token,
            ttl_seconds=spec.ttl_seconds,
            wait_timeout_seconds=spec.wait_timeout_seconds,
            fail_on_conflict=fail_on_conflict,
        ),
        start_to_close_timeout=timedelta(
            seconds=spec.wait_timeout_seconds + _LOCK_ACTIVITY_BUFFER_S
        ),
        retry_policy=_FAIL_FAST_RETRY_POLICY if fail_on_conflict else _WAIT_RETRY_POLICY,
    )


async def _renew_loop(key: str, token: str, spec: WorkflowLockSpec) -> None:
    """Periodically extend the lock TTL for the life of the run."""
    renew_deadline_seconds = (
        spec.ttl_seconds - spec.renew_interval_seconds - LOCK_RENEW_BUFFER_SECONDS
    )
    attempt_timeout_seconds = min(_RENEW_ACTIVITY_TIMEOUT_S, renew_deadline_seconds)
    while True:
        await asyncio.sleep(spec.renew_interval_seconds)
        await workflow.execute_activity(
            renew_workflow_lock,
            RenewWorkflowLockInput(key=key, token=token, ttl_seconds=spec.ttl_seconds),
            start_to_close_timeout=timedelta(seconds=attempt_timeout_seconds),
            schedule_to_close_timeout=timedelta(seconds=renew_deadline_seconds),
            retry_policy=_RENEW_RETRY_POLICY,
        )


async def _release_lock(key: str, token: str) -> None:
    """Release the lock; never mask the run's own outcome if release fails."""
    release: Coroutine[Any, Any, Any] = workflow.execute_activity(
        release_workflow_lock,
        ReleaseWorkflowLockInput(key=key, token=token),
        start_to_close_timeout=timedelta(seconds=_RELEASE_ACTIVITY_TIMEOUT_S),
        retry_policy=_RELEASE_RETRY_POLICY,
    )
    try:
        await release
    except Exception:  # pylint: disable=broad-exception-caught
        workflow.logger.warning("Failed to release workflow lock %s; TTL will expire it", key)
