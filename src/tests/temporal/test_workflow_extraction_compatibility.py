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
"""Compatibility contracts for package-owned workflow definitions."""

import ast
import importlib
from pathlib import Path
from types import ModuleType

import pytest

from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.hello_world import (
    HelloWorld,
    HelloWorldApproval,
    HelloWorldRunning,
)

_NGC_MODULE_NAMES = (
    "_ib_pkey_helpers",
    "backup",
    "bmc",
    "cable_validation",
    "config_diff",
    "connected_host",
    "cumulus_hardware_validation",
    "deploy",
    "device_password_rotation",
    "diagnostics",
    "ib_pkey_creation",
    "ib_pkey_member_add",
    "ib_pkey_member_delete",
    "ib_pkey_member_update",
    "ib_port_guid_discovery",
    "infiniband_cable_validation",
    "infiniband_get_unhealthy_ports",
    "infiniband_mlnx_os_upgrade",
    "lldp",
    "multi_deploy",
    "nvlinkswitch_firmware_upgrade",
    "os_upgrade",
    "reprovision",
    "site_backup",
    "site_password_rotation",
    "spx_overlay",
)
_SERVICE_WORKFLOW_ROOT = (
    Path(__file__).parents[2] / "nv_config_manager" / "temporal" / "ngc" / "workflows"
)


def _module_pair(name: str) -> tuple[ModuleType, ModuleType]:
    return (
        importlib.import_module(f"nv_config_manager.temporal.ngc.workflows.{name}"),
        importlib.import_module(f"nv_config_manager_workflows.workflows.{name}"),
    )


@pytest.mark.parametrize("module_name", _NGC_MODULE_NAMES)
def test_ngc_workflow_facades_export_canonical_objects(module_name: str) -> None:
    """Every supported legacy symbol is the exact package-owned object."""
    legacy_module, canonical_module = _module_pair(module_name)

    assert set(canonical_module.__all__) <= set(legacy_module.__all__)
    for name in canonical_module.__all__:
        assert getattr(legacy_module, name) is getattr(canonical_module, name)


def test_hello_world_facade_exports_canonical_objects() -> None:
    """The legacy Hello World leaf remains an identity-preserving facade."""
    legacy_module = importlib.import_module(
        "nv_config_manager.temporal.hello_world.workflows.hello_world_workflow"
    )
    canonical_module = importlib.import_module("nv_config_manager_workflows.workflows.hello_world")

    for name in legacy_module.__all__:
        assert getattr(legacy_module, name) is getattr(canonical_module, name)


@pytest.mark.parametrize(
    ("package_name", "expected_workflows"),
    [
        (
            "nv_config_manager.temporal.ngc.workflows",
            set(BUILTIN_WORKFLOWS) - {HelloWorld, HelloWorldApproval},
        ),
        (
            "nv_config_manager.temporal.hello_world.workflows",
            {HelloWorld, HelloWorldApproval, HelloWorldRunning},
        ),
    ],
    ids=["ngc", "hello-world"],
)
def test_service_workflow_package_roots_export_canonical_workflows(
    package_name: str, expected_workflows: set[type]
) -> None:
    """Each legacy package root re-exports exactly its share of the package-owned workflows."""
    legacy_root = importlib.import_module(package_name)
    canonical_root = importlib.import_module("nv_config_manager_workflows.workflows")

    assert {getattr(legacy_root, name) for name in legacy_root.__all__} == expected_workflows
    for name in legacy_root.__all__:
        assert getattr(legacy_root, name) is getattr(canonical_root, name)


def test_service_workflow_modules_define_no_temporal_workflows() -> None:
    """New workflow definitions belong only in the reusable package."""
    violations: list[str] = []
    roots = (
        _SERVICE_WORKFLOW_ROOT,
        _SERVICE_WORKFLOW_ROOT.parents[1] / "hello_world" / "workflows",
    )

    for root in roots:
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "workflow"
                        and target.attr in {"defn", "run"}
                    ):
                        violations.append(f"{path.name}:{node.lineno}: @{target.attr}")

    assert violations == []
