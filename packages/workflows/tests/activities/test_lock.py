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
"""Tests for the reusable workflow lock activities."""

from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.lock import (
    AcquireWorkflowLockInput,
    ReleaseWorkflowLockInput,
    RenewWorkflowLockInput,
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)
from nv_config_manager_workflows.runtime import LockNotConfiguredError, configure_lock_backend


class StubLockBackend:
    """Controllable backend for activity delegation tests."""

    acquire: AsyncMock
    renew: AsyncMock
    release: AsyncMock

    def __init__(self) -> None:
        """Create successful lock operations by default."""
        self.acquire = AsyncMock(return_value=True)
        self.renew = AsyncMock(return_value=True)
        self.release = AsyncMock(return_value=True)


@pytest.fixture
def lock_backend() -> StubLockBackend:
    """Install and return a controllable lock backend."""
    backend = StubLockBackend()
    configure_lock_backend(lambda: backend)
    return backend


def test_lock_activity_input_fields_remain_stable() -> None:
    """Activity payload field names and ordering are part of Temporal histories."""
    assert list(AcquireWorkflowLockInput.model_fields) == [
        "key",
        "token",
        "ttl_seconds",
        "wait_timeout_seconds",
        "fail_on_conflict",
    ]
    assert list(RenewWorkflowLockInput.model_fields) == ["key", "token", "ttl_seconds"]
    assert list(ReleaseWorkflowLockInput.model_fields) == ["key", "token"]
    assert AcquireWorkflowLockInput.model_fields["fail_on_conflict"].default is False


async def test_acquire_delegates_waiting_configuration(lock_backend: StubLockBackend) -> None:
    """Waiting acquisition forwards the unchanged key, token, lease, and timeout."""
    await acquire_workflow_lock(
        AcquireWorkflowLockInput(key="lock", token="owner", ttl_seconds=60, wait_timeout_seconds=5)
    )

    lock_backend.acquire.assert_awaited_once_with(
        "lock",
        "owner",
        timeout=60,
        blocking_timeout=5,
        blocking=True,
    )


async def test_acquire_conflict_is_retryable_while_waiting(
    lock_backend: StubLockBackend,
) -> None:
    """A waiter lets Temporal retry a lock conflict."""
    lock_backend.acquire.return_value = False

    with pytest.raises(ApplicationError, match="held by another run") as error:
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(
                key="lock",
                token="owner",
                ttl_seconds=60,
                wait_timeout_seconds=5,
            )
        )

    assert error.value.non_retryable is False


async def test_acquire_conflict_is_non_retryable_when_failing_fast(
    lock_backend: StubLockBackend,
) -> None:
    """Fail-fast acquisition neither blocks nor asks Temporal to retry."""
    lock_backend.acquire.return_value = False

    with pytest.raises(ApplicationError, match="held by another run") as error:
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(
                key="lock",
                token="owner",
                ttl_seconds=60,
                wait_timeout_seconds=5,
                fail_on_conflict=True,
            )
        )

    assert error.value.non_retryable is True
    await_args = lock_backend.acquire.await_args
    assert await_args is not None
    assert await_args.kwargs["blocking"] is False


async def test_renew_delegates_and_fails_permanently_when_ownership_is_lost(
    lock_backend: StubLockBackend,
) -> None:
    """A lost lease is a permanent workflow runtime failure."""
    lock_backend.renew.return_value = False

    with pytest.raises(ApplicationError, match="Lost workflow lock") as error:
        await renew_workflow_lock(RenewWorkflowLockInput(key="lock", token="owner", ttl_seconds=60))

    assert error.value.non_retryable is True
    lock_backend.renew.assert_awaited_once_with("lock", "owner", timeout=60)


async def test_release_is_best_effort(lock_backend: StubLockBackend) -> None:
    """An already-lost lock does not mask the workflow's outcome."""
    lock_backend.release.return_value = False

    await release_workflow_lock(ReleaseWorkflowLockInput(key="lock", token="owner"))

    lock_backend.release.assert_awaited_once_with("lock", "owner")


async def test_activity_raises_named_error_when_backend_was_not_configured(
    unconfigured_workflow_runtime: None,
) -> None:
    """Activity execution before startup fails clearly and without retries."""
    with pytest.raises(LockNotConfiguredError, match="configure_lock_backend") as error:
        await acquire_workflow_lock(
            AcquireWorkflowLockInput(
                key="lock",
                token="owner",
                ttl_seconds=60,
                wait_timeout_seconds=5,
            )
        )

    assert error.value.non_retryable is True
