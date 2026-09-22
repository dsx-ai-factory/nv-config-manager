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
"""Tests for the generic metadata-driven workflow lock.

Covers the lock spec, deterministic key building, the lock activities, and the
run-decorator wiring (acquire before the body, release after -- even on failure).
Renewal and the Redis backend are covered separately in ``tests/common/test_lock*``.
"""

import uuid

import pytest
from pydantic import BaseModel, ValidationError
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.common.activities.lock import (
    AcquireWorkflowLockInput,
    ReleaseWorkflowLockInput,
    RenewWorkflowLockInput,
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)
from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.lock import (
    WorkflowLockSpec,
    build_workflow_lock_key,
)
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin


class _KeyInput(BaseModel):
    host: str | None = None
    pkey: str | None = None


class TestWorkflowLockSpec:
    def test_defaults_are_sane(self):
        spec = WorkflowLockSpec(key_fields=["pkey"])
        assert spec.on_conflict == "wait"
        assert spec.renew_interval_seconds < spec.ttl_seconds

    def test_key_fields_required(self):
        with pytest.raises(ValidationError):
            WorkflowLockSpec(key_fields=[])

    def test_renew_must_be_shorter_than_ttl(self):
        with pytest.raises(ValidationError, match="renew_interval_seconds must be less"):
            WorkflowLockSpec(key_fields=["pkey"], ttl_seconds=30, renew_interval_seconds=30)

    def test_wait_timeout_must_be_positive(self):
        with pytest.raises(ValidationError, match="wait_timeout_seconds must be positive"):
            WorkflowLockSpec(key_fields=["pkey"], wait_timeout_seconds=0)

    def test_renew_interval_must_leave_buffer(self):
        """Renewal must finish before the TTL lapses, not merely precede it."""
        with pytest.raises(ValidationError, match="renewal buffer"):
            WorkflowLockSpec(key_fields=["pkey"], ttl_seconds=60, renew_interval_seconds=50)


class TestBuildWorkflowLockKey:
    def test_includes_namespace_and_fields(self):
        spec = WorkflowLockSpec(key_fields=["host", "pkey"])
        key = build_workflow_lock_key(
            spec,
            workflow_name="IBPKeyMemberAdd",
            namespace="ngc",
            workflow_input=_KeyInput(host="ufm1", pkey="0x0005"),
        )
        assert key == "wf-lock:ngc:host=ufm1:pkey=0x0005"

    def test_workflow_name_scoping_is_opt_in(self):
        spec = WorkflowLockSpec(key_fields=["pkey"], include_workflow_name=True)
        key = build_workflow_lock_key(
            spec,
            workflow_name="IBPKeyMemberAdd",
            namespace="ngc",
            workflow_input=_KeyInput(pkey="0x0005"),
        )
        assert key == "wf-lock:ngc:IBPKeyMemberAdd:pkey=0x0005"

    def test_spec_namespace_overrides_workflow_namespace(self):
        spec = WorkflowLockSpec(key_fields=["pkey"], namespace="override")
        key = build_workflow_lock_key(
            spec,
            workflow_name="W",
            namespace="ngc",
            workflow_input=_KeyInput(pkey="0x0005"),
        )
        assert key.startswith("wf-lock:override:")

    @pytest.mark.parametrize("bad", [None, ""])
    def test_missing_key_field_raises(self, bad):
        spec = WorkflowLockSpec(key_fields=["host", "pkey"])
        with pytest.raises(ValueError, match="pkey"):
            build_workflow_lock_key(
                spec,
                workflow_name="W",
                namespace="ngc",
                workflow_input=_KeyInput(host="ufm1", pkey=bad),
            )


class TestMetadataMixin:
    def test_returns_declared_spec(self):
        spec = WorkflowLockSpec(key_fields=["pkey"])

        class _Locked(WorkflowMetadataMixin):
            workflow_lock = spec

        assert _Locked.get_workflow_lock() is spec

    def test_absent_by_default(self):
        class _Unlocked(WorkflowMetadataMixin):
            pass

        assert _Unlocked.get_workflow_lock() is None


class TestLockActivities:
    """The activities translate helper results into Temporal retry semantics."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds_silently(self, disable_workflow_lock_io):
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
        )
        disable_workflow_lock_io["acquire_lock"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_acquire_conflict_is_retryable_when_waiting(self, disable_workflow_lock_io):
        disable_workflow_lock_io["acquire_lock"].return_value = False
        with pytest.raises(ApplicationError) as exc:
            await acquire_workflow_lock(
                AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
            )
        assert exc.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_acquire_conflict_is_non_retryable_when_failing(self, disable_workflow_lock_io):
        disable_workflow_lock_io["acquire_lock"].return_value = False
        with pytest.raises(ApplicationError) as exc:
            await acquire_workflow_lock(
                AcquireWorkflowLockInput(
                    key="k",
                    token="t",
                    ttl_seconds=60,
                    wait_timeout_seconds=5,
                    fail_on_conflict=True,
                )
            )
        assert exc.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_acquire_blocks_while_waiting(self, disable_workflow_lock_io):
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
        )
        assert disable_workflow_lock_io["acquire_lock"].await_args.kwargs["blocking"] is True

    @pytest.mark.asyncio
    async def test_acquire_does_not_block_when_failing(self, disable_workflow_lock_io):
        """on_conflict='fail' must not wait out wait_timeout_seconds."""
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(
                key="k",
                token="t",
                ttl_seconds=60,
                wait_timeout_seconds=5,
                fail_on_conflict=True,
            )
        )
        assert disable_workflow_lock_io["acquire_lock"].await_args.kwargs["blocking"] is False

    @pytest.mark.asyncio
    async def test_renew_raises_when_lock_lost(self, disable_workflow_lock_io):
        disable_workflow_lock_io["renew_lock"].return_value = False
        with pytest.raises(ApplicationError, match="Lost workflow lock"):
            await renew_workflow_lock(RenewWorkflowLockInput(key="k", token="t", ttl_seconds=60))

    @pytest.mark.asyncio
    async def test_release_never_raises(self, disable_workflow_lock_io):
        disable_workflow_lock_io["release_lock"].return_value = False
        # Should complete without raising even when the lock was already lost.
        await release_workflow_lock(ReleaseWorkflowLockInput(key="k", token="t"))


# ---------------------------------------------------------------------------
# Decorator wiring, exercised end to end through a minimal locked workflow.
# ---------------------------------------------------------------------------


class _ProbeInput(BaseModel):
    resource: str
    fail: bool = False


@workflow.defn
class _LockedProbeWorkflow(WorkflowMetadataMixin):
    """Minimal workflow that opts into the per-resource lock."""

    workflow_name = "Locked Probe"
    workflow_namespace = "test"
    workflow_lock = WorkflowLockSpec(
        key_fields=["resource"], ttl_seconds=60, renew_interval_seconds=10
    )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: _ProbeInput) -> str:
        """Return a marker, or blow up when asked to."""
        if workflow_input.fail:
            raise ApplicationError("probe failure", non_retryable=True)
        return f"ran:{workflow_input.resource}"


async def _run_probe(env_factory, probe_input: _ProbeInput) -> str:
    task_queue = str(uuid.uuid4())
    async with env_factory() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[_LockedProbeWorkflow],
            activities=REGISTERED_COMMON_ACTIVITIES,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _LockedProbeWorkflow.run,
                probe_input,
                id=str(uuid.uuid4()),
                task_queue=task_queue,
            )


@pytest.mark.asyncio
async def test_lock_acquired_and_released_around_run(time_skipping_env, disable_workflow_lock_io):
    """A locked workflow acquires the keyed lock, runs, then releases it."""
    result = await _run_probe(time_skipping_env, _ProbeInput(resource="r1"))

    assert result == "ran:r1"
    acquire = disable_workflow_lock_io["acquire_lock"]
    release = disable_workflow_lock_io["release_lock"]
    assert acquire.await_count == 1
    assert acquire.await_args.args[0] == "wf-lock:test:resource=r1"
    assert release.await_count == 1
    assert release.await_args.args[0] == "wf-lock:test:resource=r1"


@pytest.mark.asyncio
async def test_lock_released_even_when_run_fails(time_skipping_env, disable_workflow_lock_io):
    """The lock is released in the finally path when the body raises."""
    with pytest.raises(WorkflowFailureError):
        await _run_probe(time_skipping_env, _ProbeInput(resource="r2", fail=True))

    release = disable_workflow_lock_io["release_lock"]
    assert release.await_count == 1
    assert release.await_args.args[0] == "wf-lock:test:resource=r2"
