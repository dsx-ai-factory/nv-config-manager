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

import dataclasses
from unittest.mock import AsyncMock, Mock

import pytest

from nv_config_manager_workflows.schedulers import runtime as runtime_module
from nv_config_manager_workflows.schedulers.runtime import (
    BuiltinSchedulerRuntime,
    BuiltinSchedulerRuntimeNotConfiguredError,
    SchedulerRuntime,
    SchedulerRuntimeNotConfiguredError,
    SchedulerWorkflowRoles,
    configure_builtin_scheduler_runtime,
    configure_scheduler_runtime,
    get_builtin_scheduler_runtime,
    get_scheduler_runtime,
)


@pytest.fixture(autouse=True)
def reset_scheduler_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_scheduler_runtime", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_builtin_scheduler_runtime", runtime_module._UNSET)


def scheduler_runtime() -> SchedulerRuntime:
    return SchedulerRuntime(
        temporal_client=AsyncMock(),
        workflow_roles=Mock(),
        sleep=AsyncMock(),
    )


def builtin_runtime() -> BuiltinSchedulerRuntime:
    return BuiltinSchedulerRuntime(desired_backup_devices=AsyncMock())


def test_scheduler_runtime_requires_explicit_startup_configuration() -> None:
    with pytest.raises(
        SchedulerRuntimeNotConfiguredError,
        match="configure_scheduler_runtime",
    ):
        get_scheduler_runtime()


def test_backup_runtime_requires_explicit_startup_configuration() -> None:
    with pytest.raises(
        BuiltinSchedulerRuntimeNotConfiguredError,
        match="configure_builtin_scheduler_runtime",
    ):
        get_builtin_scheduler_runtime()


def test_runtimes_are_configured_independently_by_identity_and_can_be_replaced() -> None:
    first, second = scheduler_runtime(), scheduler_runtime()
    first_builtin, second_builtin = builtin_runtime(), builtin_runtime()

    configure_scheduler_runtime(first)
    assert get_scheduler_runtime() is first
    with pytest.raises(BuiltinSchedulerRuntimeNotConfiguredError):
        get_builtin_scheduler_runtime()

    configure_builtin_scheduler_runtime(first_builtin)
    assert get_builtin_scheduler_runtime() is first_builtin

    configure_scheduler_runtime(second)
    configure_builtin_scheduler_runtime(second_builtin)
    assert get_scheduler_runtime() is second
    assert get_builtin_scheduler_runtime() is second_builtin


def test_runtime_and_role_records_are_immutable() -> None:
    shared = scheduler_runtime()
    backup = builtin_runtime()
    roles = SchedulerWorkflowRoles(
        read_roles=frozenset({"reader"}),
        execute_roles=frozenset({"operator"}),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        shared.sleep = AsyncMock()  # type: ignore[misc]  # ty: ignore[invalid-assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        backup.desired_backup_devices = AsyncMock()  # type: ignore[misc]  # ty: ignore[invalid-assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        roles.read_roles = frozenset()  # type: ignore[misc]  # ty: ignore[invalid-assignment]
