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
"""Tests for the workflow lock activities.

The activities translate lock-helper results into Temporal retry semantics.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import lock as lock_activities
from nv_config_manager_workflows.activities.lock import (
    AcquireWorkflowLockInput,
    ReleaseWorkflowLockInput,
    RenewWorkflowLockInput,
    acquire_workflow_lock,
    release_workflow_lock,
    renew_workflow_lock,
)


class _Recorder:
    """Stands in for a lock helper, recording how the activity called it."""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> bool:
        self.calls.append((args, kwargs))
        return self.result


_InstallRecorder = Callable[[str, bool], _Recorder]
"""Replace the named lock helper with a recorder that returns ``result``."""


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch) -> _InstallRecorder:
    """Replace one lock helper with a recorder returning ``result``."""

    def _install(name: str, result: bool) -> _Recorder:
        recorder = _Recorder(result)
        monkeypatch.setattr(lock_activities, name, recorder)
        return recorder

    return _install


class TestAcquire:
    async def test_succeeds_silently(self, helper: _InstallRecorder) -> None:
        helper("acquire_lock", True)

        await acquire_workflow_lock(
            AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
        )

    async def test_conflict_is_retryable_when_waiting(self, helper: _InstallRecorder) -> None:
        helper("acquire_lock", False)

        with pytest.raises(ApplicationError) as exc:
            await acquire_workflow_lock(
                AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
            )

        assert exc.value.non_retryable is False

    async def test_conflict_is_non_retryable_when_failing(self, helper: _InstallRecorder) -> None:
        helper("acquire_lock", False)

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

    async def test_blocks_while_waiting(self, helper: _InstallRecorder) -> None:
        acquire = helper("acquire_lock", True)

        await acquire_workflow_lock(
            AcquireWorkflowLockInput(key="k", token="t", ttl_seconds=60, wait_timeout_seconds=5)
        )

        assert acquire.calls[0][1]["blocking"] is True

    async def test_does_not_block_when_failing(self, helper: _InstallRecorder) -> None:
        """on_conflict='fail' must not wait out wait_timeout_seconds."""
        acquire = helper("acquire_lock", True)

        await acquire_workflow_lock(
            AcquireWorkflowLockInput(
                key="k",
                token="t",
                ttl_seconds=60,
                wait_timeout_seconds=5,
                fail_on_conflict=True,
            )
        )

        assert acquire.calls[0][1]["blocking"] is False


class TestRenew:
    async def test_succeeds_silently(self, helper: _InstallRecorder) -> None:
        helper("renew_lock", True)

        await renew_workflow_lock(RenewWorkflowLockInput(key="k", token="t", ttl_seconds=60))

    async def test_raises_when_lock_lost(self, helper: _InstallRecorder) -> None:
        helper("renew_lock", False)

        with pytest.raises(ApplicationError, match="Lost workflow lock"):
            await renew_workflow_lock(RenewWorkflowLockInput(key="k", token="t", ttl_seconds=60))


class TestRelease:
    async def test_never_raises(self, helper: _InstallRecorder) -> None:
        """An already-lost lock is not an error; the TTL would have freed it."""
        helper("release_lock", False)

        await release_workflow_lock(ReleaseWorkflowLockInput(key="k", token="t"))
