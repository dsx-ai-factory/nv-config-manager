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
_JUNIPER_CLIENT_PATH = Path("clients/device/juniper.py")
_FORBIDDEN_CONFIGURATION_CALLS = {
    "ConfigParser",
    "getenv",
    "load_config",
    "open",
    "read_bytes",
    "read_text",
}


def _is_service_module(module: str) -> bool:
    """Return whether an import targets the service package rather than this package."""
    return module == "nv_config_manager" or module.startswith("nv_config_manager.")


def _forbidden_configuration_call(node: ast.Call, relative_path: Path) -> str | None:
    """Return the forbidden call name, allowing only PyEZ's connection open."""
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
        if (
            name == "open"
            and relative_path == _JUNIPER_CLIENT_PATH
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "device"
        ):
            # PyEZ Device.open() establishes a NETCONF connection.
            return None
    else:
        return None
    return name if name in _FORBIDDEN_CONFIGURATION_CALLS else None


def test_workflows_package_has_no_service_configuration_dependencies() -> None:
    """Reusable workflows must not depend on service-owned configuration access."""
    violations: list[str] = []

    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif node.module is not None:
                    modules = [node.module]
                else:
                    modules = []

                for module in modules:
                    if (
                        _is_service_module(module)
                        or module == "configparser"
                        or module.startswith("configparser.")
                    ):
                        violations.append(f"{relative_path}:{node.lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")

    assert violations == []


def test_configuration_boundary_allows_only_pyez_device_open() -> None:
    """Only the PyEZ connection call may use the otherwise forbidden open name."""

    def parse_call(expression: str) -> ast.Call:
        node = ast.parse(expression, mode="eval").body
        assert isinstance(node, ast.Call)
        return node

    pyez_open = parse_call("device.open()")
    assert _forbidden_configuration_call(pyez_open, _JUNIPER_CLIENT_PATH) is None
    assert _forbidden_configuration_call(pyez_open, Path("other.py")) == "open"

    filesystem_opens = (
        "open(path)",
        "Path(path).open()",
        "path.open()",
        "os.open(path, flags)",
        "io.open(path)",
        "codecs.open(path)",
        "builtins.open(path)",
        "filesystem.open(path)",
    )
    for expression in filesystem_opens:
        call = parse_call(expression)
        assert _forbidden_configuration_call(call, _JUNIPER_CLIENT_PATH) == "open", expression
