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
"""Tests for Temporal worker startup composition."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from pytest_mock import MockerFixture
from temporalio import workflow

from nv_config_manager.temporal.worker import main as worker_main
from nv_config_manager_workflows.activities.builtin import BUILTIN_ACTIVITIES
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import workflow_type_name
from nv_config_manager_workflows.registration.errors import WorkflowConflictError
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.stage import StageMixin
from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.hello_world import HelloWorldRunning


@workflow.defn(name="HelloWorld")
class ConflictingHelloWorldWorkflow(WorkflowMetadataMixin, StageMixin):
    """Plugin workflow that claims the service-owned HelloWorld Temporal type."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off"])
def test_local_workflow_is_excluded_unless_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    """The ten-year latency fixture never enters the normal built-in catalog."""
    if value is None:
        monkeypatch.delenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", raising=False)
    else:
        monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", value)

    workflows = worker_main._registered_workflows(
        WorkflowRegistry(
            all_workflows=list(BUILTIN_WORKFLOWS),
            all_activities=list(BUILTIN_ACTIVITIES),
        )
    )

    assert workflows == [
        *worker_main.NGC_REGISTERED_WORKFLOWS,
        *worker_main.HELLO_WORLD_REGISTERED_WORKFLOWS,
    ]
    assert set(workflows) == set(BUILTIN_WORKFLOWS)
    assert HelloWorldRunning not in workflows
    assert len({workflow_type_name(item) for item in workflows}) == 33


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_local_workflow_is_appended_only_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The opt-in catalog is added after normal registry validation and deduplication."""
    monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", value)

    workflows = worker_main._registered_workflows(
        WorkflowRegistry(
            all_workflows=list(BUILTIN_WORKFLOWS),
            all_activities=list(BUILTIN_ACTIVITIES),
        )
    )

    assert workflows == [
        *worker_main.NGC_REGISTERED_WORKFLOWS,
        *worker_main.HELLO_WORLD_REGISTERED_WORKFLOWS,
        HelloWorldRunning,
    ]
    assert set(workflows[:-1]) == set(BUILTIN_WORKFLOWS)
    assert len({workflow_type_name(item) for item in workflows}) == 34


async def test_runtime_is_configured_before_worker_construction(mocker: MockerFixture) -> None:
    """Activity dependencies are installed before the worker registers activities."""
    startup_events: list[str] = []
    mocker.patch.object(
        worker_main,
        "configure_workflow_runtime",
        side_effect=lambda: startup_events.append("configure-runtime"),
    )
    mocker.patch.object(worker_main, "setup_telemetry", return_value=mocker.sentinel.runtime)
    mocker.patch.object(worker_main, "temporal_address", return_value="temporal.example:7233")
    mocker.patch.object(worker_main, "client_connect_options", return_value={})
    mocker.patch.object(worker_main, "get_data_converter", return_value=mocker.sentinel.converter)

    async def connect(*args: Any, **kwargs: Any) -> Any:
        startup_events.append("connect-client")
        return mocker.sentinel.client

    mocker.patch.object(worker_main.Client, "connect", side_effect=connect)
    worker = mocker.Mock()
    worker.run = AsyncMock(side_effect=lambda: startup_events.append("run-worker"))
    worker_options: dict[str, Any] = {}

    def build_worker(*args: Any, **kwargs: Any) -> Any:
        startup_events.append("construct-worker")
        worker_options.update(kwargs)
        return worker

    mocker.patch.object(worker_main, "Worker", side_effect=build_worker)

    await worker_main.main()

    assert startup_events == [
        "configure-runtime",
        "connect-client",
        "construct-worker",
        "run-worker",
    ]
    registered_activities = worker_options["activities"]
    assert {item for item in registered_activities if item in BUILTIN_ACTIVITIES} == set(
        BUILTIN_ACTIVITIES
    )
    assert all(registered_activities.count(item) == 1 for item in BUILTIN_ACTIVITIES)


async def test_core_plugin_workflow_collision_fails_before_worker_construction(
    mocker: MockerFixture,
) -> None:
    """A plugin cannot claim the Temporal type of a service-owned workflow."""
    mocker.patch.object(worker_main, "configure_workflow_runtime")
    mocker.patch.object(worker_main, "setup_telemetry", return_value=mocker.sentinel.runtime)
    mocker.patch.object(worker_main, "temporal_address", return_value="temporal.example:7233")
    mocker.patch.object(worker_main, "client_connect_options", return_value={})
    mocker.patch.object(worker_main, "get_data_converter", return_value=mocker.sentinel.converter)
    mocker.patch.object(
        worker_main.Client,
        "connect",
        new=AsyncMock(return_value=mocker.sentinel.client),
    )
    mocker.patch.object(
        worker_main.WorkflowRegistry,
        "build",
        return_value=WorkflowRegistry(
            all_workflows=[ConflictingHelloWorldWorkflow],
            all_activities=list(BUILTIN_ACTIVITIES),
        ),
    )
    worker_constructor = mocker.patch.object(worker_main, "Worker")

    with pytest.raises(
        WorkflowConflictError,
        match='Duplicate Temporal workflow type "HelloWorld"',
    ):
        await worker_main.main()

    worker_constructor.assert_not_called()
