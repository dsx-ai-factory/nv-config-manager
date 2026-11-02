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
"""Static dependency contracts for package-owned workflows."""

import ast
import importlib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import nv_config_manager_workflows.workflows as workflow_exports
from nv_config_manager_workflows.activities.builtin import BUILTIN_ACTIVITIES
from nv_config_manager_workflows.activities.lock import LOCK_ACTIVITIES
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.contract import (
    activity_name,
    workflow_required_activity_names,
)
from nv_config_manager_workflows.registration.errors import WorkflowRequiredActivityError
from nv_config_manager_workflows.registration.validation import validate_workflow_catalog
from nv_config_manager_workflows.workflows.backup import BackupWorkflow
from nv_config_manager_workflows.workflows.bmc import (
    DCIM_STAGE_IDENTIFIERS_PATCH as BMC_DCIM_STAGE_IDENTIFIERS_PATCH,
)
from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.cable_validation import (
    CABLE_STATUS_UPDATE_PATCH_ID,
    CABLE_VALIDATION_CHILD_TIMEOUT_PATCH_ID,
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.deploy import DeployWorkflow, TenantDeployWorkflow
from nv_config_manager_workflows.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.ib_pkey_creation import (
    DCIM_STAGE_IDENTIFIERS_PATCH as CREATION_DCIM_STAGE_IDENTIFIERS_PATCH,
)
from nv_config_manager_workflows.workflows.ib_pkey_creation import IBPKeyCreationWorkflow
from nv_config_manager_workflows.workflows.ib_pkey_member_add import IBPKeyMemberAddWorkflow
from nv_config_manager_workflows.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteWorkflow,
)
from nv_config_manager_workflows.workflows.ib_pkey_member_update import (
    DCIM_STAGE_IDENTIFIERS_PATCH as UPDATE_DCIM_STAGE_IDENTIFIERS_PATCH,
)
from nv_config_manager_workflows.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateWorkflow,
)
from nv_config_manager_workflows.workflows.multi_deploy import (
    BatchDeployWorkflow,
    MultiDeployWorkflow,
)
from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.os_upgrade import SwitchOSUpgradeWorkflow
from nv_config_manager_workflows.workflows.reprovision import (
    REPROVISION_WORKFLOW_UPDATES_PATCH_ID,
    ReprovisionWorkflow,
)
from nv_config_manager_workflows.workflows.site_backup import SiteBackupWorkflow
from nv_config_manager_workflows.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.spx_overlay import (
    SpXOverlayAssignmentWorkflow,
    SpXOverlayTenantChangeWorkflow,
)

_WORKFLOW_ROOT = Path(__file__).parents[2] / "src" / "nv_config_manager_workflows" / "workflows"
_PKEY_HELPER_MODULE = "nv_config_manager_workflows.workflows._ib_pkey_helpers"

_LOCKED_PKEY_WORKFLOWS = (
    IBPKeyMemberAddWorkflow,
    IBPKeyMemberDeleteWorkflow,
    IBPKeyMemberUpdateWorkflow,
)


def _direct_activity_names(node: ast.AST) -> set[str]:
    """Collect statically named direct activity targets from an AST subtree."""
    names: set[str] = set()
    for call in ast.walk(node):
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Attribute)
            or call.func.attr not in {"execute_activity", "start_activity"}
            or not call.args
        ):
            continue
        target = call.args[0]
        if isinstance(target, ast.Name) and target.id != "activity_type":
            names.add(target.id)
        elif isinstance(target, ast.Constant) and isinstance(target.value, str):
            names.add(target.value)
    return names


def _named_call_names(node: ast.AST) -> set[str]:
    """Collect directly named call targets from an AST subtree."""
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def _function_activity_dependencies(tree: ast.Module) -> dict[str, set[str]]:
    """Resolve direct and transitive activity dependencies for module functions."""
    function_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    dependencies = {name: _direct_activity_names(node) for name, node in function_nodes.items()}

    while True:
        expanded_dependencies: dict[str, set[str]] = {}
        for name, node in function_nodes.items():
            expanded = set(dependencies[name])
            for called_name in _named_call_names(node):
                expanded.update(dependencies.get(called_name, ()))
            expanded_dependencies[name] = expanded
        if expanded_dependencies == dependencies:
            return dependencies
        dependencies = expanded_dependencies


def _imported_helper_aliases(tree: ast.Module) -> dict[str, str]:
    """Map locally imported PKey helper names to their original names."""
    return {
        imported.asname or imported.name: imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == _PKEY_HELPER_MODULE
        for imported in node.names
        if imported.name != "*"
    }


def _helper_activity_names(
    node: ast.AST,
    helper_aliases: dict[str, str],
    helper_dependencies: dict[str, set[str]],
) -> set[str]:
    """Collect activity dependencies of PKey helpers called by an AST subtree."""
    activity_names: set[str] = set()
    for called_name in _named_call_names(node):
        helper_name = helper_aliases.get(called_name)
        if helper_name is not None:
            activity_names.update(helper_dependencies.get(helper_name, ()))
    return activity_names


def _module_workflow_classes(module: ModuleType) -> set[type[WorkflowMetadataMixin]]:
    """Return workflow classes defined by a leaf module, excluding imported children."""
    return {
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and issubclass(value, WorkflowMetadataMixin)
    }


def test_activity_calls_are_covered_by_required_activity_declarations() -> None:
    """Direct and shared-helper calls cannot bypass dependency validation."""
    helper_path = _WORKFLOW_ROOT / "_ib_pkey_helpers.py"
    helper_dependencies = _function_activity_dependencies(
        ast.parse(helper_path.read_text(), filename=str(helper_path))
    )

    violations: list[str] = []
    for path in sorted(_WORKFLOW_ROOT.glob("*.py")):
        if path.name.startswith("_") or path.name in {"builtin.py"}:
            continue
        module = importlib.import_module(f"nv_config_manager_workflows.workflows.{path.stem}")
        workflow_classes = {
            workflow_class.__name__: workflow_class
            for workflow_class in _module_workflow_classes(module)
        }
        tree = ast.parse(path.read_text(), filename=str(path))
        helper_aliases = _imported_helper_aliases(tree)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name not in workflow_classes:
                continue
            workflow_class = workflow_classes[node.name]
            declared = set(workflow_required_activity_names(workflow_class))
            required = _direct_activity_names(node) | _helper_activity_names(
                node,
                helper_aliases,
                helper_dependencies,
            )
            missing = required - declared
            if missing:
                violations.append(f"{path.name}:{node.name}: {sorted(missing)}")

    assert violations == []
    assert {
        "record_ib_pkey_in_dcim",
        "record_ib_pkey_in_nautobot",
    } <= set(workflow_required_activity_names(IBPKeyCreationWorkflow))


def test_helper_activity_analysis_resolves_transitive_calls_and_import_aliases() -> None:
    """Helper wrappers and import aliases retain their activity dependencies."""
    helper_tree = ast.parse(
        """
async def activity_wrapper():
    await workflow.execute_activity("wrapped_activity")

async def nested_wrapper():
    await activity_wrapper()
"""
    )
    workflow_tree = ast.parse(
        f"""
from {_PKEY_HELPER_MODULE} import nested_wrapper as aliased_wrapper

class ExampleWorkflow:
    async def run(self):
        await aliased_wrapper()

class UnrelatedWorkflow:
    async def run(self):
        pass
"""
    )
    workflow_nodes = {
        node.name: node for node in workflow_tree.body if isinstance(node, ast.ClassDef)
    }
    helper_aliases = _imported_helper_aliases(workflow_tree)
    helper_dependencies = _function_activity_dependencies(helper_tree)

    assert _helper_activity_names(
        workflow_nodes["ExampleWorkflow"],
        helper_aliases,
        helper_dependencies,
    ) == {"wrapped_activity"}
    assert (
        _helper_activity_names(
            workflow_nodes["UnrelatedWorkflow"],
            helper_aliases,
            helper_dependencies,
        )
        == set()
    )


@pytest.mark.parametrize(
    "workflow_class",
    _LOCKED_PKEY_WORKFLOWS,
    ids=lambda workflow_class: workflow_class.__name__,
)
def test_locked_pkey_workflow_requires_every_lock_activity(
    workflow_class: type[WorkflowMetadataMixin],
) -> None:
    """Decorator-issued lock calls participate in each workflow's dependency contract."""
    lock_activity_names = {
        name for activity in LOCK_ACTIVITIES if (name := activity_name(activity)) is not None
    }
    assert len(lock_activity_names) == len(LOCK_ACTIVITIES)
    assert lock_activity_names <= set(workflow_required_activity_names(workflow_class))


@pytest.mark.parametrize(
    "workflow_class",
    _LOCKED_PKEY_WORKFLOWS,
    ids=lambda workflow_class: workflow_class.__name__,
)
@pytest.mark.parametrize(
    "missing_lock_activity",
    LOCK_ACTIVITIES,
    ids=lambda lock_activity: activity_name(lock_activity),
)
def test_locked_pkey_workflow_is_rejected_when_a_lock_activity_is_missing(
    workflow_class: type[WorkflowMetadataMixin],
    missing_lock_activity: Callable[..., Any],
) -> None:
    """Worker startup rejects each locked workflow when any lock handler is absent."""
    missing_name = activity_name(missing_lock_activity)
    assert missing_name is not None
    available_activities = tuple(
        activity for activity in BUILTIN_ACTIVITIES if activity is not missing_lock_activity
    )

    with pytest.raises(
        WorkflowRequiredActivityError,
        match=f'requires activity "{missing_name}"',
    ):
        validate_workflow_catalog((workflow_class,), activities=available_activities)


def test_child_workflow_dependencies_are_canonical_builtins() -> None:
    """Every direct child dependency resolves to the one canonical class object."""
    child_dependencies = {
        BatchDeployWorkflow: (BackupWorkflow,),
        DeployWorkflow: (BackupWorkflow,),
        DevicePasswordRotationWorkflow: (BackupWorkflow,),
        MultiDeployWorkflow: (BatchDeployWorkflow,),
        NVLinkSwitchFirmwareUpgradeWorkflow: (BackupWorkflow,),
        ReprovisionWorkflow: (BackupWorkflow,),
        SiteBackupWorkflow: (BackupWorkflow,),
        SiteCableValidationWorkflow: (DeviceCableValidationWorkflow,),
        SitePasswordRotationWorkflow: (DevicePasswordRotationWorkflow,),
        SpXOverlayTenantChangeWorkflow: (
            SpXOverlayAssignmentWorkflow,
            TenantDeployWorkflow,
        ),
        SwitchOSUpgradeWorkflow: (BackupWorkflow,),
        TenantDeployWorkflow: (BackupWorkflow,),
    }

    assert set(child_dependencies) <= set(BUILTIN_WORKFLOWS)
    assert all(
        child in BUILTIN_WORKFLOWS
        for dependencies in child_dependencies.values()
        for child in dependencies
    )


def test_workflow_specific_patch_ids_are_frozen() -> None:
    """Moving module ownership cannot rename markers embedded in running histories."""
    assert BMC_DCIM_STAGE_IDENTIFIERS_PATCH == "dcim-stage-identifiers-v1"
    assert CREATION_DCIM_STAGE_IDENTIFIERS_PATCH == "dcim-stage-identifiers-v1"
    assert UPDATE_DCIM_STAGE_IDENTIFIERS_PATCH == "dcim-stage-identifiers-v1"
    assert CABLE_STATUS_UPDATE_PATCH_ID == "cable-validation-dcim-status-v1"
    assert CABLE_VALIDATION_CHILD_TIMEOUT_PATCH_ID == "cable-validation-child-timeout-v1"
    assert REPROVISION_WORKFLOW_UPDATES_PATCH_ID == "reprovision-workflow-updates-v1"


def test_public_workflow_surface_exports_every_canonical_class() -> None:
    """Callers can import every built-in workflow from the documented package surface."""
    assert all(
        getattr(workflow_exports, workflow_class.__name__) is workflow_class
        for workflow_class in BUILTIN_WORKFLOWS
    )
