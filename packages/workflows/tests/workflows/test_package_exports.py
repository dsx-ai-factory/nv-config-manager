# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for the lazily loaded nv_config_manager_workflows.workflows package surface."""

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import nv_config_manager_workflows.workflows as workflow_exports

_PACKAGE = workflow_exports.__name__
_INIT_PATH = Path(workflow_exports.__file__)


def _type_checking_exports() -> dict[str, str]:
    """Map each name imported under ``if TYPE_CHECKING`` in the package to its module."""
    tree = ast.parse(_INIT_PATH.read_text())
    block = next(
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    )
    exports: dict[str, str] = {}
    for node in block.body:
        assert isinstance(node, ast.ImportFrom)
        assert node.module is not None
        for alias in node.names:
            exports[alias.name] = node.module.removeprefix(f"{_PACKAGE}.")
    return exports


def test_lazy_exports_match_all_and_type_checking_imports() -> None:
    """__all__, the lazy export map, and the static imports list the same names."""
    assert set(workflow_exports._EXPORT_MODULES) == set(workflow_exports.__all__)
    assert workflow_exports._EXPORT_MODULES == _type_checking_exports()


@pytest.mark.parametrize("name", workflow_exports.__all__)
def test_export_resolves_to_its_defining_module(name: str) -> None:
    """Every exported name is the same object its defining module provides."""
    module = importlib.import_module(f"{_PACKAGE}.{workflow_exports._EXPORT_MODULES[name]}")

    assert getattr(workflow_exports, name) is getattr(module, name)


def test_unknown_attribute_raises_attribute_error() -> None:
    """Names outside the export map still raise AttributeError."""
    with pytest.raises(AttributeError, match="NotAWorkflow"):
        _ = workflow_exports.NotAWorkflow


def test_importing_one_workflow_module_does_not_import_the_others() -> None:
    """Importing a workflow module loads only that module and what it imports itself."""
    script = (
        "import sys\n"
        f"import {_PACKAGE}.hello_world\n"
        f"print('\\n'.join(m for m in sys.modules if m.startswith('{_PACKAGE}.')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == [f"{_PACKAGE}.hello_world"]
