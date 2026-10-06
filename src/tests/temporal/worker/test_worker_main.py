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

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel
from pytest_mock import MockerFixture
from temporalio import workflow

from nv_config_manager.temporal.worker import main as worker_main
from nv_config_manager_workflows.activities.builtin import BUILTIN_ACTIVITIES
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import registry as registry_module
from nv_config_manager_workflows.registration.builtin import builtin_plugin
from nv_config_manager_workflows.registration.contract import activity_name, workflow_type_name
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import (
    WorkflowConflictError,
    WorkflowPluginDiscoveryError,
)
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.stage import StageMixin
from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.hello_world import HelloWorldRunning

_REGISTERED_TYPE_NAMES = Path(__file__).parents[1] / "fixtures" / "registered_type_names.json"


@workflow.defn(name="HelloWorld")
class ConflictingHelloWorldWorkflow(WorkflowMetadataMixin, StageMixin):
    """Plugin workflow that claims the built-in HelloWorld Temporal type."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn(name="HelloWorldRunning")
class ConflictingHelloWorldRunningWorkflow(WorkflowMetadataMixin, StageMixin):
    """Plugin workflow that claims the local-test HelloWorldRunning Temporal type."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


def _mock_worker_startup(mocker: MockerFixture) -> Mock:
    """Patch the external dependencies of main() and return the Worker constructor mock."""
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
    worker_constructor = mocker.patch.object(worker_main, "Worker")
    worker_constructor.return_value.run = AsyncMock()
    return worker_constructor


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

    assert workflows == list(BUILTIN_WORKFLOWS)
    assert HelloWorldRunning not in workflows
    assert len({workflow_type_name(item) for item in workflows}) == 33


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_local_workflow_is_appended_only_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The opt-in catalog is appended to a copy of the registry's workflows."""
    monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", value)
    registry = WorkflowRegistry(
        all_workflows=list(BUILTIN_WORKFLOWS),
        all_activities=list(BUILTIN_ACTIVITIES),
    )

    workflows = worker_main._registered_workflows(registry)

    assert workflows == [*BUILTIN_WORKFLOWS, HelloWorldRunning]
    assert registry.all_workflows == list(BUILTIN_WORKFLOWS)
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
    mocker.patch.object(
        worker_main,
        "log_workflow_registry",
        side_effect=lambda registry: startup_events.append("log-registry"),
    )
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
        "log-registry",
        "construct-worker",
        "run-worker",
    ]
    registered_activities = worker_options["activities"]
    assert {item for item in registered_activities if item in BUILTIN_ACTIVITIES} == set(
        BUILTIN_ACTIVITIES
    )
    assert all(registered_activities.count(item) == 1 for item in BUILTIN_ACTIVITIES)


async def test_plugin_claiming_a_builtin_temporal_type_fails_before_worker_construction(
    mocker: MockerFixture,
) -> None:
    """A plugin cannot claim the Temporal type of a built-in workflow."""
    worker_constructor = _mock_worker_startup(mocker)
    mocker.patch.object(
        registry_module,
        "discover_workflow_plugins",
        return_value={
            "builtin": builtin_plugin(),
            "conflicting": WorkflowPluginDescriptor(
                name="conflicting", workflows=[ConflictingHelloWorldWorkflow]
            ),
        },
    )
    log_registry = mocker.patch.object(worker_main, "log_workflow_registry")

    with pytest.raises(
        WorkflowConflictError,
        match='Duplicate Temporal workflow type "HelloWorld" contributed by workflow plugins '
        '"builtin" and "conflicting"',
    ):
        await worker_main.main()

    log_registry.assert_not_called()
    worker_constructor.assert_not_called()


async def test_registry_without_builtin_plugin_fails_before_worker_construction(
    mocker: MockerFixture,
) -> None:
    """The worker never polls Temporal with a registry missing the built-in plugin."""
    worker_constructor = _mock_worker_startup(mocker)
    mocker.patch.object(WorkflowRegistry, "build", return_value=WorkflowRegistry())
    log_registry = mocker.patch.object(worker_main, "log_workflow_registry")

    with pytest.raises(WorkflowPluginDiscoveryError, match='Workflow plugin "builtin"'):
        await worker_main.main()

    log_registry.assert_not_called()
    worker_constructor.assert_not_called()


@pytest.mark.parametrize("local_test_workflows", [False, True])
async def test_worker_registers_the_frozen_temporal_type_names(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_test_workflows: bool,
) -> None:
    """The worker registers exactly the Temporal types recorded in workflow histories."""
    if local_test_workflows:
        monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", "1")
    else:
        monkeypatch.delenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", raising=False)
    worker_constructor = _mock_worker_startup(mocker)
    log_registry = mocker.patch.object(worker_main, "log_workflow_registry")
    frozen: dict[str, list[str]] = json.loads(_REGISTERED_TYPE_NAMES.read_text())
    expected_workflows = [
        name for name in frozen["workflows"] if local_test_workflows or name != "HelloWorldRunning"
    ]

    await worker_main.main()

    worker_constructor.assert_called_once()
    options = worker_constructor.call_args.kwargs
    assert options["task_queue"] == "default-task-queue"
    assert sorted(workflow_type_name(item) for item in options["workflows"]) == expected_workflows
    assert sorted(activity_name(item) for item in options["activities"]) == frozen["activities"]
    (logged_registry,) = log_registry.call_args.args
    assert HelloWorldRunning not in logged_registry.all_workflows


async def test_local_test_workflow_conflict_fails_before_worker_construction(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry workflow cannot share a Temporal type with an opted-in local-test workflow."""
    monkeypatch.setenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", "1")
    worker_constructor = _mock_worker_startup(mocker)
    mocker.patch.object(
        worker_main,
        "build_workflow_registry",
        return_value=WorkflowRegistry(
            all_workflows=[*BUILTIN_WORKFLOWS, ConflictingHelloWorldRunningWorkflow],
            all_activities=list(BUILTIN_ACTIVITIES),
        ),
    )
    log_registry = mocker.patch.object(worker_main, "log_workflow_registry")

    with pytest.raises(
        WorkflowConflictError,
        match='Local test workflow "HelloWorldRunning" claims Temporal workflow type '
        '"HelloWorldRunning"',
    ):
        await worker_main.main()

    log_registry.assert_not_called()
    worker_constructor.assert_not_called()
