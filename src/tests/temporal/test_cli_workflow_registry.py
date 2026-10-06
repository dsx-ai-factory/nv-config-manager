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
"""Tests that the workflow CLI's command set comes from the workflow registry."""

import re
from collections.abc import Callable

import click
import pytest
from click.testing import CliRunner
from pydantic import BaseModel
from temporalio import workflow

from nv_config_manager.temporal import cli as temporal_cli
from nv_config_manager.temporal.workflow_registry import build_workflow_registry
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WorkflowConflictError,
    WorkflowPluginDescriptor,
    WorkflowRegistry,
    builtin_plugin,
)
from nv_config_manager_workflows.stage import StageMixin

BUILTIN_COMMANDS = frozenset({"login", "logout", "auth-status", "list-workflows", "examples"})
BUILTIN_WORKFLOW_COMMANDS = frozenset(
    {
        "backup",
        "config-diff",
        "connected-host-metadata",
        "deploy",
        "device-cable-validation",
        "device-password-rotation",
        "diagnostics",
        "hello-world",
        "hello-world-approval",
        "ib-port-guid-discovery",
        "ibp-key-creation",
        "ibp-key-member-add",
        "ibp-key-member-delete",
        "ibp-key-member-update",
        "infiniband-cable-validation",
        "infiniband-get-unhealthy-ports",
        "infiniband-mlnx-os-upgrade",
        "multi-deploy",
        "nv-link-switch-firmware-upgrade",
        "port-lldp-info",
        "redfish-provisioning",
        "reprovision",
        "site-backup",
        "site-cable-validation",
        "site-password-rotation",
        "sp-x-overlay-assignment",
        "sp-x-overlay-creation",
        "sp-x-overlay-deletion",
        "sp-x-overlay-tenant-change",
        "switch-os-upgrade",
        "validate-hardware",
    }
)


class _PluginInput(BaseModel):
    value: str


@workflow.defn
class CliPluginVisibleWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "CLI Plugin Visible"
    workflow_description = "A plugin workflow exposed through the API and MCP"
    workflow_input_class = _PluginInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/plugin/cli-visible"
    workflow_namespace = "plugin"
    workflow_mcp_enabled = True

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


@workflow.defn
class CliPluginApiOnlyWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "CLI Plugin API Only"
    workflow_description = "A plugin workflow exposed through the API but not MCP"
    workflow_input_class = _PluginInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/plugin/cli-api-only"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


@workflow.defn
class CliPluginHiddenWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "CLI Plugin Hidden"
    workflow_description = "A plugin workflow not exposed through the API"
    workflow_input_class = _PluginInput
    workflow_api_endpoint = "/plugin/cli-hidden"
    workflow_namespace = "plugin"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


@workflow.defn
class CliReservedNameWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "CLI Reserved Name"
    workflow_description = "A plugin workflow whose CLI name is set per test"
    workflow_input_class = _PluginInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/plugin/cli-reserved"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


def _builtin_command_group() -> click.Group:
    """Return a group holding only the CLI's commands that are not workflows."""
    return click.Group(
        commands={
            name: command
            for name, command in temporal_cli.cli.commands.items()
            if name not in temporal_cli.discovery.workflows
        }
    )


def test_cli_registers_the_registry_api_workflows() -> None:
    api_workflows = {
        workflow_class.get_workflow_cli_name(): workflow_class
        for workflow_class in build_workflow_registry().api_workflows
    }
    cli_workflows = {
        name: info.workflow_class for name, info in temporal_cli.discovery.workflows.items()
    }

    # Inclusion, not equality: installed workflow plugins add commands.
    assert BUILTIN_COMMANDS | BUILTIN_WORKFLOW_COMMANDS <= set(temporal_cli.cli.commands)
    assert set(temporal_cli.cli.commands) == BUILTIN_COMMANDS | set(api_workflows)
    assert set(_builtin_command_group().commands) == BUILTIN_COMMANDS
    assert cli_workflows == api_workflows


def test_cli_exposes_plugin_workflows_only_when_api_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = WorkflowPluginDescriptor(
        name="cli-test",
        workflows=(CliPluginVisibleWorkflow, CliPluginApiOnlyWorkflow, CliPluginHiddenWorkflow),
    )
    registry = WorkflowRegistry.build(
        {BUILTIN_PLUGIN_NAME: builtin_plugin(), descriptor.name: descriptor}
    )
    monkeypatch.setattr(WorkflowRegistry, "build", lambda: registry)
    discovery = temporal_cli.WorkflowDiscovery()
    group = _builtin_command_group()

    temporal_cli.register_workflow_commands(group, discovery.workflows)

    assert set(group.commands) == BUILTIN_COMMANDS | BUILTIN_WORKFLOW_COMMANDS | {
        "cli-plugin-visible",
        "cli-plugin-api-only",
    }
    visible = discovery.workflows["cli-plugin-visible"]
    assert visible.workflow_class is CliPluginVisibleWorkflow
    assert visible.endpoint == "/plugin/cli-visible"
    assert visible.namespace == "plugin"
    api_only = discovery.workflows["cli-plugin-api-only"]
    assert api_only.workflow_class is CliPluginApiOnlyWorkflow
    assert api_only.namespace == ""


def test_cli_keeps_the_builtin_workflow_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin main's built-in namespaces now that the CLI has no namespace fallback.

    main defaulted a workflow without ``workflow_namespace`` by its service list (ngc or
    hello_world); every built-in must keep declaring it so ``list-workflows`` is unchanged.
    """
    registry = WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})
    discovery = temporal_cli.WorkflowDiscovery(registry.api_workflows)
    monkeypatch.setattr(temporal_cli, "discovery", discovery)
    expected = {
        name: "hello_world" if name in {"hello-world", "hello-world-approval"} else "ngc"
        for name in BUILTIN_WORKFLOW_COMMANDS
    }

    result = CliRunner().invoke(temporal_cli.cli, ["list-workflows"])

    assert {name: info.namespace for name, info in discovery.workflows.items()} == expected
    assert result.exit_code == 0, result.output
    listed = re.findall(r"^(\S+)\n  Description: .*\n  Namespace: (.*)$", result.output, re.M)
    assert dict(listed) == expected


@pytest.mark.parametrize("command_name", sorted(BUILTIN_COMMANDS))
def test_cli_refuses_a_workflow_named_like_a_builtin_command(
    command_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        CliReservedNameWorkflow, "get_workflow_cli_name", classmethod(lambda cls: command_name)
    )
    discovery = temporal_cli.WorkflowDiscovery(workflows=[CliReservedNameWorkflow])
    group = _builtin_command_group()
    builtin_command = group.commands[command_name]

    with pytest.raises(SystemExit) as exc_info:
        temporal_cli.register_workflow_commands(group, discovery.workflows)

    assert exc_info.value.code == 1
    assert group.commands[command_name] is builtin_command
    assert (
        f"Error: workflow CliReservedNameWorkflow uses CLI name '{command_name}', "
        "which is reserved for a built-in command"
    ) in capsys.readouterr().err


def _registry_without_builtin_plugin() -> WorkflowRegistry:
    return WorkflowRegistry()


def _conflicting_registry() -> WorkflowRegistry:
    raise WorkflowConflictError('Workflow CLI name "deploy" is contributed twice')


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (_registry_without_builtin_plugin, f'Workflow plugin "{BUILTIN_PLUGIN_NAME}"'),
        (_conflicting_registry, 'Workflow CLI name "deploy" is contributed twice'),
    ],
    ids=["builtin-plugin-missing", "registration-conflict"],
)
def test_cli_exits_when_the_registry_cannot_be_built(
    build: Callable[[], WorkflowRegistry],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(WorkflowRegistry, "build", build)

    with pytest.raises(SystemExit) as exc_info:
        temporal_cli.WorkflowDiscovery()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Error loading workflow registry: " in err
    assert message in err
