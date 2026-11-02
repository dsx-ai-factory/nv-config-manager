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
"""Process-local capabilities supplied to workflow schedulers by the service host."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from temporalio.client import Client


@dataclass(frozen=True, slots=True)
class SchedulerWorkflowRoles:
    """Read and execute roles attached to scheduled workflow executions."""

    read_roles: frozenset[str]
    execute_roles: frozenset[str]


type TemporalClientProvider = Callable[[], Awaitable[Client]]
type WorkflowRolesProvider = Callable[[str], SchedulerWorkflowRoles | None]
type SleepProvider = Callable[[float], Awaitable[None]]
type DesiredBackupDevicesProvider = Callable[[], Awaitable[set[str]]]


@dataclass(frozen=True, slots=True)
class SchedulerRuntime:
    """Immutable capabilities shared by every scheduler in the host process.

    ``temporal_client`` connects a new Temporal client on every call so that
    rotated TLS material and changed connection settings take effect without a
    restart. Call it once per unit of work, such as one reconciliation pass,
    rather than caching the result for the lifetime of the scheduler.
    ``workflow_roles`` returns the RBAC roles for a workflow class name, or
    ``None`` when no roles are configured for it.
    """

    temporal_client: TemporalClientProvider
    workflow_roles: WorkflowRolesProvider
    sleep: SleepProvider


@dataclass(frozen=True, slots=True)
class BuiltinSchedulerRuntime:
    """Immutable capabilities used only by schedulers from the built-in plugin."""

    desired_backup_devices: DesiredBackupDevicesProvider


class SchedulerRuntimeNotConfiguredError(RuntimeError):
    """Raised when startup did not configure the shared scheduler runtime."""


class BuiltinSchedulerRuntimeNotConfiguredError(RuntimeError):
    """Raised when startup did not configure the built-in scheduler runtime."""


class _Unset:
    """Mark runtime capabilities that the service has not configured yet."""


_UNSET: Final = _Unset()
_scheduler_runtime: SchedulerRuntime | _Unset = _UNSET
_builtin_scheduler_runtime: BuiltinSchedulerRuntime | _Unset = _UNSET


def configure_scheduler_runtime(runtime: SchedulerRuntime) -> None:
    """Install the service capabilities shared by every scheduler."""
    global _scheduler_runtime  # noqa: PLW0603
    _scheduler_runtime = runtime


def get_scheduler_runtime() -> SchedulerRuntime:
    """Return shared scheduler capabilities or report missing startup configuration."""
    runtime = _scheduler_runtime
    if isinstance(runtime, _Unset):
        raise SchedulerRuntimeNotConfiguredError(
            "Scheduler runtime is not configured. Call "
            "configure_scheduler_runtime(runtime) at service startup."
        )
    return runtime


def configure_builtin_scheduler_runtime(runtime: BuiltinSchedulerRuntime) -> None:
    """Install the service capabilities used by built-in schedulers."""
    global _builtin_scheduler_runtime  # noqa: PLW0603
    _builtin_scheduler_runtime = runtime


def get_builtin_scheduler_runtime() -> BuiltinSchedulerRuntime:
    """Return built-in scheduler capabilities or report missing startup configuration."""
    runtime = _builtin_scheduler_runtime
    if isinstance(runtime, _Unset):
        raise BuiltinSchedulerRuntimeNotConfiguredError(
            "Built-in scheduler runtime is not configured. Call "
            "configure_builtin_scheduler_runtime(runtime) at service startup."
        )
    return runtime


__all__ = [
    "BuiltinSchedulerRuntime",
    "BuiltinSchedulerRuntimeNotConfiguredError",
    "DesiredBackupDevicesProvider",
    "SchedulerRuntime",
    "SchedulerRuntimeNotConfiguredError",
    "SchedulerWorkflowRoles",
    "SleepProvider",
    "TemporalClientProvider",
    "WorkflowRolesProvider",
    "configure_builtin_scheduler_runtime",
    "configure_scheduler_runtime",
    "get_builtin_scheduler_runtime",
    "get_scheduler_runtime",
]
