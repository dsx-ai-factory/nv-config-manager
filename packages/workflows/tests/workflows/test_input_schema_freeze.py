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
"""Freeze the JSON schema of every API-enabled built-in workflow input.

The input models are the request bodies of the dynamic workflow endpoints, the
MCP tool schemas, and the Go request models. UI hint annotations must not change
them, so ``tests/fixtures/input_schemas.json`` records ``model_json_schema()``
for each API-enabled workflow in the built-in plugin, keyed by workflow class
name.

Dict equality and the sorted snapshot ignore key order, but the form renderer
lays fields out in ``properties`` declaration order. So
``tests/fixtures/input_schema_field_order.json`` separately records, per
workflow, the ordered ``properties`` keys and ``required`` list of the
top-level schema (``#``) and every ``$defs`` entry (``#/$defs/<Name>``).

Regenerate both fixtures only after an intentional change with::

    NVCM_UPDATE_SNAPSHOTS=1 uv run pytest \\
        packages/workflows/tests/workflows/test_input_schema_freeze.py
"""

import difflib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME, builtin_plugin
from nv_config_manager_workflows.registration.registry import WorkflowRegistry

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_SNAPSHOT = _FIXTURES / "input_schemas.json"
_FIELD_ORDER_SNAPSHOT = _FIXTURES / "input_schema_field_order.json"
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


def _current_input_schemas() -> dict[str, Any]:
    """Return each API-enabled built-in workflow's input schema, or None without an input."""
    registry = WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})
    schemas: dict[str, Any] = {}
    for workflow in registry.api_workflows:
        input_class = workflow.get_workflow_input_class()
        schemas[workflow.__name__] = input_class.model_json_schema() if input_class else None
    normalized: dict[str, Any] = json.loads(json.dumps(schemas))
    return normalized


def _field_order(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the ``properties`` and ``required`` order of a schema and each of its ``$defs``."""
    if schema is None:
        return None
    targets = {
        "#": schema,
        **{f"#/$defs/{name}": definition for name, definition in schema.get("$defs", {}).items()},
    }
    return {
        pointer: {
            "properties": list(target.get("properties", {})),
            "required": list(target.get("required", [])),
        }
        for pointer, target in targets.items()
        if "properties" in target or "required" in target
    }


def _load_snapshot(path: Path, current: dict[str, Any]) -> dict[str, Any]:
    """Return the stored snapshot, rewriting it from ``current`` first in update mode."""
    if _UPDATE_SNAPSHOTS:
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    if not path.exists():
        pytest.fail(f"Missing {path.name}; capture it with NVCM_UPDATE_SNAPSHOTS=1", pytrace=False)
    snapshot: dict[str, Any] = json.loads(path.read_text())
    return snapshot


@pytest.fixture(scope="module")
def schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(snapshot, current)`` input schemas."""
    current = _current_input_schemas()
    return _load_snapshot(_SNAPSHOT, current), current


@pytest.fixture(scope="module")
def field_orders(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(snapshot, current)`` field orders, read from the unsorted live schemas."""
    _, current_schemas = schemas
    current = {name: _field_order(schema) for name, schema in current_schemas.items()}
    return _load_snapshot(_FIELD_ORDER_SNAPSHOT, current), current


def test_api_enabled_workflow_set_is_unchanged(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """No API-enabled built-in workflow is added or removed without updating the snapshot."""
    snapshot, current = schemas
    added = sorted(set(current) - set(snapshot))
    removed = sorted(set(snapshot) - set(current))

    if added or removed:
        pytest.fail(
            f"API-enabled built-in workflows changed: added={added} removed={removed}",
            pytrace=False,
        )


def test_input_schemas_match_snapshot(schemas: tuple[dict[str, Any], dict[str, Any]]) -> None:
    """Every snapshotted workflow input keeps an identical ``model_json_schema()``."""
    snapshot, current = schemas
    problems = [
        f"{name}: input schema changed\n{_json_diff(snapshot[name], current[name])}"
        for name in sorted(set(snapshot) & set(current))
        if snapshot[name] != current[name]
    ]

    if problems:
        pytest.fail(
            f"Workflow input schemas changed ({len(problems)}):\n\n" + "\n\n".join(problems),
            pytrace=False,
        )


def test_input_field_order_matches_snapshot(
    field_orders: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """Every snapshotted workflow input keeps its ``properties`` and ``required`` order."""
    snapshot, current = field_orders
    problems = [
        f"{name}: field order changed\n{_json_diff(snapshot[name], current[name])}"
        for name in sorted(set(snapshot) & set(current))
        if snapshot[name] != current[name]
    ]

    if problems:
        pytest.fail(
            f"Workflow input field order changed ({len(problems)}):\n\n" + "\n\n".join(problems),
            pytrace=False,
        )
