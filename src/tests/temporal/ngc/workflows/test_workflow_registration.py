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
import glob
import inspect
from pathlib import Path

import yaml

import nv_config_manager.temporal.ngc.workflows as workflows
from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS
from nv_config_manager.temporal.ngc.workflows.deploy import TenantDeployWorkflow


def _load_all_workflow_classes():
    """Load all workflow classes from the workflows module."""
    workflow_classes = []
    workflow_path = Path(inspect.getsourcefile(workflows)).parent
    for path in workflow_path.glob("*.py"):
        if path.stem == "__init__":
            continue
        module = getattr(workflows, path.stem)

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if hasattr(obj, "run") and "Mixin" not in obj.__name__:
                workflow_classes.append(obj)

    return workflow_classes


def test_workflow_registration():
    """Test that all workflows are registered."""
    workflow_classes = _load_all_workflow_classes()
    for workflow_class in workflow_classes:
        assert workflow_class in REGISTERED_WORKFLOWS, (
            f"Workflow {workflow_class.__name__} not registered"
        )


def test_tenant_deploy_is_worker_internal():
    """Keep Tenant Deploy executable as a child without exposing a public start surface."""
    assert TenantDeployWorkflow in REGISTERED_WORKFLOWS
    assert TenantDeployWorkflow.get_workflow_api_endpoint() is None
    assert not TenantDeployWorkflow.has_complete_metadata()


def test_workflow_rbac_exists():
    """Test that all workflows have a corresponding RBAC mapping in all rbac files."""
    workflow_classes = _load_all_workflow_classes()
    workflow_names = {cls.__name__ for cls in workflow_classes}

    # Find all values-rbac.* files in the helm chart directory
    chart_dir = Path(__file__).parent.parent.parent.parent.parent.parent / "deploy" / "helm"
    rbac_files = glob.glob(str(chart_dir / "values-rbac*.yaml"))

    assert rbac_files, "No values-rbac*.yaml files found"

    for rbac_file in rbac_files:
        with open(rbac_file) as f:
            rbac_config = yaml.safe_load(f)

        assert "rbac" in rbac_config, f"No 'rbac' section found in {rbac_file}"
        assert "workflows" in rbac_config["rbac"], f"No 'workflows' section found in {rbac_file}"

        # Extract workflow names from the RBAC config
        rbac_workflow_names = {w["name"] for w in rbac_config["rbac"]["workflows"]}

        # Check if all workflows are in the RBAC config
        missing_workflows = workflow_names - rbac_workflow_names
        assert not missing_workflows, f"Workflows missing from {rbac_file}: {missing_workflows}"

        # Check if all workflows in RBAC config have read_roles and execute_roles
        for workflow in rbac_config["rbac"]["workflows"]:
            assert "read_roles" in workflow, (
                f"No 'read_roles' for {workflow['name']} in {rbac_file}"
            )
            assert "execute_roles" in workflow, (
                f"No 'execute_roles' for {workflow['name']} in {rbac_file}"
            )
            assert workflow["read_roles"], (
                f"Empty 'read_roles' for {workflow['name']} in {rbac_file}"
            )
            assert workflow["execute_roles"], (
                f"Empty 'execute_roles' for {workflow['name']} in {rbac_file}"
            )
