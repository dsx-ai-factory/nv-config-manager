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
"""Activities that manage a workflow's distributed lock."""

from __future__ import annotations

import logging

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.lock import (
    LockBackendNotConfiguredError,
    acquire_lock,
    release_lock,
    renew_lock,
)

log = logging.getLogger(__name__)


class AcquireWorkflowLockInput(BaseModel):
    """Parameters for acquiring a workflow's per-resource lock."""

    key: str
    token: str
    ttl_seconds: int
    wait_timeout_seconds: float
    fail_on_conflict: bool = False


class RenewWorkflowLockInput(BaseModel):
    """Parameters for extending a held workflow lock's TTL."""

    key: str
    token: str
    ttl_seconds: int


class ReleaseWorkflowLockInput(BaseModel):
    """Parameters for releasing a held workflow lock."""

    key: str
    token: str


@activity.defn
async def acquire_workflow_lock(input: AcquireWorkflowLockInput) -> None:
    """Acquire the per-resource lock, waiting or failing on contention."""
    try:
        acquired = await acquire_lock(
            input.key,
            input.token,
            timeout=input.ttl_seconds,
            blocking_timeout=input.wait_timeout_seconds,
            blocking=not input.fail_on_conflict,
        )
    except LockBackendNotConfiguredError as error:
        raise ApplicationError(str(error), non_retryable=True) from error
    if acquired:
        log.info("Acquired workflow lock %s", input.key)
        return

    raise ApplicationError(
        f"Workflow lock {input.key} is held by another run",
        non_retryable=input.fail_on_conflict,
    )


@activity.defn
async def renew_workflow_lock(input: RenewWorkflowLockInput) -> None:
    """Extend the lock's TTL; fail loudly if this run no longer owns it."""
    if await renew_lock(input.key, input.token, timeout=input.ttl_seconds):
        return

    raise ApplicationError(
        f"Lost workflow lock {input.key}; another run may hold it",
        non_retryable=True,
    )


@activity.defn
async def release_workflow_lock(input: ReleaseWorkflowLockInput) -> None:
    """Release the lock. Best effort: an already-lost lock is not an error."""
    if await release_lock(input.key, input.token):
        log.info("Released workflow lock %s", input.key)
