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
"""Static package-isolation checks for reusable workflow code."""

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nv_config_manager_workflows"


def _is_service_module(module: str) -> bool:
    """Return whether an import targets the service package rather than this package."""
    return module == "nv_config_manager" or module.startswith("nv_config_manager.")


def test_workflows_package_does_not_import_service_modules() -> None:
    """Reusable workflow modules must install and import without nv_config_manager."""
    violations: list[str] = []

    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
            else:
                continue

            for module in modules:
                if _is_service_module(module):
                    relative_path = path.relative_to(_PACKAGE_ROOT)
                    violations.append(f"{relative_path}:{node.lineno}: {module}")

    assert violations == []
