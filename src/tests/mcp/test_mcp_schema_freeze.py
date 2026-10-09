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
"""Freeze the input schemas MCP clients see for workflow starter tools.

For every MCP-enabled workflow, ``fixtures/mcp_tool_schemas.json`` records, keyed
by tool name:

``tool_input_schema``
    The ``inputSchema`` FastMCP advertises in ``tools/list``, built from
    ``_workflow_tool_signature`` in ``nv_config_manager.mcp.tools``.
``input_schema``
    ``MCPWorkflow.input_schema``, returned to agents in every tool response.

Both must match exactly, and the set of workflow tools must not change. Annotating
workflow inputs for generic forms must not alter either. Regenerate the snapshot
only after an intentional change with::

    NVCM_UPDATE_SNAPSHOTS=1 uv run pytest src/tests/mcp/test_mcp_schema_freeze.py
"""

import difflib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from nv_config_manager.mcp.main import create_mcp_server
from nv_config_manager.mcp.settings import MCPOAuthSettings, MCPSettings
from nv_config_manager.mcp.workflows import discover_mcp_workflows
from nv_config_manager.temporal.workflow_registry import build_workflow_registry

_SNAPSHOT = Path(__file__).with_name("fixtures") / "mcp_tool_schemas.json"
_UPDATE_SNAPSHOTS = os.environ.get("NVCM_UPDATE_SNAPSHOTS") == "1"
_MAX_DIFF_LINES = 60


def _json_diff(expected: Any, actual: Any) -> str:
    """Return a bounded unified diff of two JSON values."""
    diff = list(
        difflib.unified_diff(
            json.dumps(expected, indent=2, sort_keys=True).splitlines(),
            json.dumps(actual, indent=2, sort_keys=True).splitlines(),
            fromfile="snapshot",
            tofile="current",
            n=2,
            lineterm="",
        )
    )
    if len(diff) > _MAX_DIFF_LINES:
        omitted = len(diff) - _MAX_DIFF_LINES
        diff = [*diff[:_MAX_DIFF_LINES], f"... ({omitted} more diff lines)"]
    return "\n".join(f"    {line}" for line in diff)


def _settings() -> MCPSettings:
    return MCPSettings(
        workflow_api_url="http://workflow:9000",
        workflow_ui_url="https://config-manager.example.test",
        config_store_api_url="http://config-store:9000",
        dhcp_api_url="http://dhcp:9000",
        nautobot_url="http://nautobot",
        nautobot_read_only_token="token",
        nautobot_verify=True,
        nautobot_auth_mode="jwt",
        nautobot_token_fallback_enabled=False,
        max_response_bytes=1000,
    )


async def _current_workflow_tool_schemas() -> dict[str, Any]:
    """Collect both schemas for every workflow tool the production server registers."""
    # Same registry the server builds, so the two views cannot disagree.
    registry = build_workflow_registry()
    workflows = {
        workflow.tool_name: workflow for workflow in discover_mcp_workflows(registry.mcp_workflows)
    }
    server = create_mcp_server(_settings(), MCPOAuthSettings(enabled=False))
    registered = {tool.name: tool for tool in await server.list_tools()}

    missing = sorted(set(workflows) - set(registered))
    assert not missing, f"MCP workflows not registered as tools: {missing}"

    schemas = {
        tool_name: {
            "workflow": workflow.workflow_name,
            "input_schema": workflow.input_schema,
            "tool_input_schema": registered[tool_name].inputSchema,
        }
        for tool_name, workflow in workflows.items()
    }
    normalized: dict[str, Any] = json.loads(json.dumps(schemas))
    return normalized


async def test_mcp_workflow_tool_schemas_match_snapshot() -> None:
    """Every MCP workflow tool keeps its exact ``inputSchema`` and ``input_schema``."""
    current = await _current_workflow_tool_schemas()

    if _UPDATE_SNAPSHOTS:
        _SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    if not _SNAPSHOT.exists():
        pytest.fail(
            f"Missing {_SNAPSHOT.name}; capture it with NVCM_UPDATE_SNAPSHOTS=1", pytrace=False
        )
    snapshot: dict[str, Any] = json.loads(_SNAPSHOT.read_text())

    problems: list[str] = []
    added = sorted(set(current) - set(snapshot))
    removed = sorted(set(snapshot) - set(current))
    if added or removed:
        problems.append(f"MCP workflow tool set changed: added={added} removed={removed}")
    for tool_name in sorted(set(snapshot) & set(current)):
        for key in sorted(set(snapshot[tool_name]) | set(current[tool_name])):
            expected = snapshot[tool_name].get(key)
            actual = current[tool_name].get(key)
            if expected != actual:
                problems.append(f"{tool_name}.{key}: changed\n{_json_diff(expected, actual)}")

    if problems:
        pytest.fail(
            f"MCP workflow tool schemas changed ({len(problems)}):\n\n" + "\n\n".join(problems),
            pytrace=False,
        )
