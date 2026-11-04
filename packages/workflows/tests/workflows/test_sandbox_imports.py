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
"""Static checks that workflow modules pass their dependencies through the sandbox."""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).parents[1]
_WORKFLOWS_ROOT = _TESTS_ROOT.parent / "src" / "nv_config_manager_workflows" / "workflows"
_FIXTURE_PLUGIN_ROOT = _TESTS_ROOT / "fixtures" / "plugin" / "src" / "nvcm_fixture_plugin"
_SANDBOXED_ROOTS = frozenset({"__future__", "temporalio", *sys.stdlib_module_names})


def _defines_workflow(tree: ast.Module) -> bool:
    """Return whether a module declares a ``@workflow.defn`` class."""
    return any(
        isinstance(node, ast.ClassDef)
        and any(
            ast.unparse(decorator.func if isinstance(decorator, ast.Call) else decorator)
            == "workflow.defn"
            for decorator in node.decorator_list
        )
        for node in tree.body
    )


def _is_passthrough_block(node: ast.stmt) -> bool:
    """Return whether a statement is a ``workflow.unsafe.imports_passed_through()`` block."""
    return isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Call)
        and ast.unparse(item.context_expr.func) == "workflow.unsafe.imports_passed_through"
        for item in node.items
    )


def _imports_outside_passthrough(body: list[ast.stmt]) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield module-level imports that the sandbox would re-import for every run."""
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING"):
            continue
        elif isinstance(node, (ast.If, ast.Try)):
            yield from _imports_outside_passthrough(node.body)
            yield from _imports_outside_passthrough(node.orelse)
            for handler in getattr(node, "handlers", ()):
                yield from _imports_outside_passthrough(handler.body)
        elif isinstance(node, ast.With) and not _is_passthrough_block(node):
            yield from _imports_outside_passthrough(node.body)


def _imported_root(node: ast.Import | ast.ImportFrom) -> str | None:
    """Return the top-level package an import loads, or None for a relative import."""
    if isinstance(node, ast.ImportFrom):
        return None if node.level or node.module is None else node.module.split(".")[0]
    return node.names[0].name.split(".")[0]


def _workflow_module_paths() -> list[Path]:
    """Return the built-in and fixture-plugin modules that declare workflows."""
    return sorted(
        path
        for root in (_WORKFLOWS_ROOT, _FIXTURE_PLUGIN_ROOT)
        for path in root.glob("*.py")
        if _defines_workflow(ast.parse(path.read_text(), filename=str(path)))
    )


def test_workflow_modules_are_discovered() -> None:
    """The scan covers every built-in workflow module and the fixture plugin."""
    names = {path.name for path in _workflow_module_paths()}

    assert {"hello_world.py", "deploy.py", "cable_validation.py", "workflows.py"} <= names
    assert "__init__.py" not in names
    assert "builtin.py" not in names


@pytest.mark.parametrize("path", _workflow_module_paths(), ids=lambda path: path.name)
def test_workflow_module_imports_are_passed_through(path: Path) -> None:
    """Workflow modules import third-party and shared code only inside the passthrough block.

    The sandbox re-imports anything else for every workflow run and keeps a private
    copy for each cached run.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = [
        f"{path.name}:{node.lineno}: {ast.unparse(node)}"
        for node in _imports_outside_passthrough(tree.body)
        if _imported_root(node) not in _SANDBOXED_ROOTS
    ]

    assert violations == []


@pytest.mark.parametrize(
    ("source", "violations"),
    [
        ("from pydantic import BaseModel\n", ["from pydantic import BaseModel"]),
        ("from .models import Input\n", ["from .models import Input"]),
        ("import json\nfrom datetime import timedelta\nfrom temporalio import workflow\n", []),
        (
            "with workflow.unsafe.imports_passed_through():\n    from pydantic import BaseModel\n",
            [],
        ),
        ("if TYPE_CHECKING:\n    from pydantic import BaseModel\n", []),
        (
            "try:\n    import orjson\nexcept ImportError:\n    import json\n",
            ["import orjson"],
        ),
    ],
)
def test_imports_outside_passthrough_flags_only_sandboxed_imports(
    source: str, violations: list[str]
) -> None:
    """The check flags non-stdlib imports that sit outside a passthrough block."""
    found = [
        ast.unparse(node)
        for node in _imports_outside_passthrough(ast.parse(source).body)
        if _imported_root(node) not in _SANDBOXED_ROOTS
    ]

    assert found == violations
