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
"""A plugin discovered through its entry point appears consistently in every registry consumer."""

import click
import pytest
from fastapi import APIRouter
from nvcm_fixture_plugin.schedulers import FixtureHeartbeatScheduler
from nvcm_fixture_plugin.workflows import FixtureApiOnlyWorkflow, FixtureEchoWorkflow

from nv_config_manager.mcp.workflows import discover_mcp_workflows
from nv_config_manager.temporal import cli as temporal_cli
from nv_config_manager.temporal.api.dynamic_endpoints import register_dynamic_endpoints
from nv_config_manager.temporal.worker import main as worker_main
from nv_config_manager.temporal.workflow_registry import build_workflow_registry
from nv_config_manager_workflows.registration import (
    PluginInfo,
    WorkflowRegistry,
    registry_manifest,
)
from nv_config_manager_workflows.registration.contract import activity_name
from nv_config_manager_workflows.registration.descriptor import UNKNOWN_PLUGIN_VERSION

FIXTURE_PLUGIN = "nvcm-fixture"
FIXTURE_WORKFLOWS = (FixtureEchoWorkflow, FixtureApiOnlyWorkflow)


@pytest.fixture
def registry(fixture_plugin_installed: None) -> WorkflowRegistry:
    """Build the service registry from the installed and fixture plugin entry points."""
    return build_workflow_registry()


def test_worker_registers_the_plugin_workflows_and_activity(
    registry: WorkflowRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NVCM_ENABLE_LOCAL_TEST_WORKFLOWS", raising=False)

    workflows = worker_main._registered_workflows(registry)

    assert [workflows.count(workflow) for workflow in FIXTURE_WORKFLOWS] == [1, 1]
    activity_names = [activity_name(activity) for activity in registry.all_activities]
    assert activity_names.count("nvcm_fixture_echo") == 1


def test_api_routes_include_the_plugin_workflows_once(registry: WorkflowRegistry) -> None:
    router = APIRouter(prefix="/workflow")

    register_dynamic_endpoints(router, workflows=registry.api_workflows)

    route_paths = [route.path for route in router.routes]
    assert route_paths.count("/workflow/fixture/echo") == 1
    assert route_paths.count("/workflow/fixture/api-only") == 1


def test_cli_registers_the_plugin_workflow_commands(registry: WorkflowRegistry) -> None:
    discovery = temporal_cli.WorkflowDiscovery(registry.api_workflows)
    group = click.Group()

    temporal_cli.register_workflow_commands(group, discovery.workflows)

    assert {
        name: (discovery.workflows[name].workflow_class, discovery.workflows[name].namespace)
        for name in ("fixture-echo", "fixture-api-only")
    } == {
        "fixture-echo": (FixtureEchoWorkflow, "fixture"),
        "fixture-api-only": (FixtureApiOnlyWorkflow, "fixture"),
    }
    assert {"fixture-echo", "fixture-api-only"} <= set(group.commands)


def test_mcp_exposes_only_the_mcp_enabled_plugin_workflow(registry: WorkflowRegistry) -> None:
    tools = discover_mcp_workflows(registry.mcp_workflows)

    plugin_tools = [
        (tool.tool_name, tool.endpoint)
        for tool in tools
        if tool.workflow_name in {workflow.__name__ for workflow in FIXTURE_WORKFLOWS}
    ]
    assert plugin_tools == [("run_echo", "/fixture/echo")]


def test_scheduler_registration_keeps_plugin_provenance(registry: WorkflowRegistry) -> None:
    assert [
        (registration.plugin, registration.identity, registration.scheduler)
        for registration in registry.scheduler_registrations
        if registration.plugin == FIXTURE_PLUGIN
    ] == [(FIXTURE_PLUGIN, "nvcm-fixture.heartbeat", FixtureHeartbeatScheduler)]


def test_manifest_lists_the_plugin(registry: WorkflowRegistry) -> None:
    assert (
        PluginInfo(
            FIXTURE_PLUGIN,
            UNKNOWN_PLUGIN_VERSION,
            workflow_count=2,
            activity_count=1,
            scheduler_count=1,
        )
        in registry_manifest(registry).plugins
    )
