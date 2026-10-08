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
from __future__ import annotations

import pytest

from nv_config_manager.mcp.workflows import (
    DEFAULT_SITE_LEVEL_DEVICE_STATUS,
    SITE_LEVEL_DEVICE_FILTER_PROMPT,
    MCPWorkflow,
    discover_mcp_workflows,
    normalize_workflow_parameters,
)
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WorkflowRegistry,
    builtin_plugin,
)

BUILTIN_MCP_TOOL_NAMES = [
    "run_backup",
    "run_connected_host_metadata",
    "run_cumulus_hardware_validation",
    "run_device_cable_validation",
    "run_infiniband_cable_validation",
    "run_infiniband_get_unhealthy_ports",
    "run_port_lldp_info",
    "run_site_backup",
    "run_site_cable_validation",
]


@pytest.fixture(scope="module")
def registry() -> WorkflowRegistry:
    # Built-ins only, so extra installed workflow plugins cannot change the expectations.
    return WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})


@pytest.fixture(scope="module")
def mcp_workflows(registry: WorkflowRegistry) -> list[MCPWorkflow]:
    return discover_mcp_workflows(registry.mcp_workflows)


def test_only_safe_diagnostic_workflows_are_mcp_enabled(
    mcp_workflows: list[MCPWorkflow],
) -> None:
    assert [workflow.tool_name for workflow in mcp_workflows] == BUILTIN_MCP_TOOL_NAMES


def test_registered_workflow_models_describe_every_input_field(
    registry: WorkflowRegistry,
) -> None:
    missing_descriptions: dict[str, list[str]] = {}
    for workflow in registry.all_workflows:
        input_class = workflow.get_workflow_input_class()
        if input_class is None:
            continue
        missing_descriptions[workflow.__name__] = [
            field_name
            for field_name, field_info in input_class.model_fields.items()
            if not field_info.description
        ]

    assert not {tool: fields for tool, fields in missing_descriptions.items() if fields}


def test_workflow_parameter_normalization_fills_existing_nullable_bookkeeping(
    mcp_workflows: list[MCPWorkflow],
) -> None:
    workflow = next(item for item in mcp_workflows if item.tool_name == "run_backup")

    normalized = normalize_workflow_parameters(workflow, {"device_id": "device-1"})

    assert normalized["device_id"] == "device-1"
    assert normalized["trigger"] == "API"
    assert normalized["user"] is None
    assert normalized["user_domain"] is None
    assert normalized["workflow_id"] is None
    assert normalized["intended_config_commit_id"] is None


def test_workflow_input_schema_matches_normalized_mcp_parameters(
    mcp_workflows: list[MCPWorkflow],
) -> None:
    workflow = next(item for item in mcp_workflows if item.tool_name == "run_backup")

    schema = workflow.input_schema

    assert schema["required"] == ["device_id"]
    assert schema["properties"]["trigger"]["default"] == "API"
    assert schema["properties"]["trigger"]["description"]
    assert schema["properties"]["user"]["default"] is None
    assert schema["properties"]["device_id"]["description"]


def test_site_level_filter_workflows_include_mcp_targeting_prompt(
    mcp_workflows: list[MCPWorkflow],
) -> None:
    workflows = {workflow.tool_name: workflow for workflow in mcp_workflows}

    for tool_name in ("run_site_cable_validation", "run_cumulus_hardware_validation"):
        assert workflows[tool_name].tool_prompt == SITE_LEVEL_DEVICE_FILTER_PROMPT
        assert "does not accept a single `device_id`" in workflows[tool_name].tool_description
        assert (
            "`status` defaults to `Active` and `Provisioned`"
            in workflows[tool_name].tool_description
        )
        assert "nv_config_manager_device_status: true" in workflows[tool_name].tool_description
        assert "how many managed devices match" in workflows[tool_name].tool_description

    assert workflows["run_backup"].tool_prompt is None
    assert SITE_LEVEL_DEVICE_FILTER_PROMPT not in workflows["run_backup"].tool_description


def test_site_level_filter_workflows_default_reachable_statuses(
    mcp_workflows: list[MCPWorkflow],
) -> None:
    workflows = {workflow.tool_name: workflow for workflow in mcp_workflows}

    for tool_name in ("run_site_cable_validation", "run_cumulus_hardware_validation"):
        normalized = normalize_workflow_parameters(workflows[tool_name], {"site": "PDX01"})
        assert normalized["site"] == "PDX01"
        assert normalized["status"] == DEFAULT_SITE_LEVEL_DEVICE_STATUS

        unfiltered = normalize_workflow_parameters(
            workflows[tool_name], {"site": "PDX01", "status": []}
        )
        assert unfiltered["status"] == []

    backup_normalized = normalize_workflow_parameters(
        workflows["run_backup"], {"device_id": "device-1"}
    )
    assert "status" not in backup_normalized
