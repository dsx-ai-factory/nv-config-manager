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
"""Shared helpers for workflow scheduler host tests."""

from typing import Any, cast

import pytest
from temporalio.client import Client

from nv_config_manager.temporal.scheduler import host
from nv_config_manager_workflows.registration import SchedulerRegistration
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.schedulers import runtime as scheduler_runtime

SECRET_SENTINEL = "SECRET-TOKEN-123"


def registration(
    identity: str,
    scheduler: type[Any],
    *,
    plugin: str = "fixture-plugin",
) -> SchedulerRegistration:
    """Build the registry record shape consumed by the service host."""
    return SchedulerRegistration(
        plugin=plugin,
        identity=identity,
        scheduler=cast(type[WorkflowScheduler], scheduler),
    )


def host_messages(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    """Return the level and rendered message of every scheduler host record."""
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == host.logger.name
    ]


def secret_scheduler_runtime() -> scheduler_runtime.SchedulerRuntime:
    """Build runtime providers whose closures hold credential-like values."""
    credential = SECRET_SENTINEL

    async def temporal_client() -> Client:
        raise ConnectionError(f"Temporal rejected client key {credential}")

    def workflow_roles(workflow: str) -> scheduler_runtime.SchedulerWorkflowRoles:
        return scheduler_runtime.SchedulerWorkflowRoles(
            read_roles=frozenset({credential}),
            execute_roles=frozenset({credential}),
        )

    async def sleep(seconds: float) -> None:
        raise AssertionError(credential)

    return scheduler_runtime.SchedulerRuntime(
        temporal_client=temporal_client,
        workflow_roles=workflow_roles,
        sleep=sleep,
    )


def assert_sentinel_absent(
    caplog: pytest.LogCaptureFixture,
    errors: list[BaseException] | tuple[BaseException, ...] = (),
) -> None:
    for record in caplog.records:
        assert SECRET_SENTINEL not in record.getMessage()
        assert SECRET_SENTINEL not in repr(record.args)
    for error in errors:
        assert SECRET_SENTINEL not in str(error)
        assert SECRET_SENTINEL not in repr(error)
