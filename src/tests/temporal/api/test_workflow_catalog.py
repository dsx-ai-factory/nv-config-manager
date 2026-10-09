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
"""Tests for the Temporal API's validated workflow catalog."""

from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter
from pydantic import BaseModel
from pytest_mock import MockerFixture
from temporalio import workflow

from nv_config_manager.temporal.api import workflow_catalog, workflow_v1
from nv_config_manager.temporal.api.dynamic_endpoints import register_dynamic_endpoints
from nv_config_manager.temporal.api.workflow_catalog import WORKFLOW_REGISTRY
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.builtin import (
    BUILTIN_PLUGIN_NAME,
    builtin_plugin,
)
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import WorkflowConflictError
from nv_config_manager_workflows.registration.form_catalog import WorkflowFormCatalog
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.stage import StageMixin


class _PluginInput(BaseModel):
    value: str


@workflow.defn
class _VisiblePluginWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Visible Plugin Workflow"
    workflow_description = "A plugin workflow exposed through the API"
    workflow_input_class = _PluginInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/plugin/visible"
    workflow_form_enabled = True
    workflow_form_id = "visible-plugin"
    workflow_namespace = "plugin"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


@workflow.defn
class _HiddenPluginWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Hidden Plugin Workflow"
    workflow_description = "A plugin workflow reserved for internal use"
    workflow_input_class = _PluginInput
    workflow_api_endpoint = "/plugin/hidden"
    workflow_namespace = "plugin"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


@workflow.defn
class _DeployEndpointCollisionWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Deploy Endpoint Collision"
    workflow_description = "Conflicts with a built-in endpoint"
    workflow_input_class = _PluginInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/deploy"

    @workflow.run
    async def run(self, workflow_input: _PluginInput) -> None: ...


def _plugin_registry() -> WorkflowRegistry:
    descriptor = WorkflowPluginDescriptor(
        name="api-catalog-test",
        workflows=(_VisiblePluginWorkflow, _HiddenPluginWorkflow),
    )
    # A second plugin re-listing the same class objects must not duplicate them.
    relisting = WorkflowPluginDescriptor(
        name="api-catalog-test-relist",
        workflows=(_VisiblePluginWorkflow, _HiddenPluginWorkflow),
    )
    return WorkflowRegistry.build(
        {
            BUILTIN_PLUGIN_NAME: builtin_plugin(),
            descriptor.name: descriptor,
            relisting.name: relisting,
        }
    )


def test_module_registry_includes_the_builtin_plugin() -> None:
    assert BUILTIN_PLUGIN_NAME in {plugin.name for plugin in WORKFLOW_REGISTRY.plugin_diagnostics}


def test_catalog_rejects_plugin_collisions_with_builtin_workflows() -> None:
    descriptor = WorkflowPluginDescriptor(
        name="api-catalog-collision-test",
        workflows=(_DeployEndpointCollisionWorkflow,),
    )
    with pytest.raises(WorkflowConflictError, match="workflow API endpoint"):
        WorkflowRegistry.build(
            {
                BUILTIN_PLUGIN_NAME: builtin_plugin(),
                descriptor.name: descriptor,
            }
        )


def test_dynamic_routes_include_plugin_api_workflows_once() -> None:
    router = APIRouter(prefix="/workflow")

    register_dynamic_endpoints(router, workflows=_plugin_registry().api_workflows)

    route_paths = [route.path for route in router.routes]
    assert route_paths.count("/workflow/plugin/visible") == 1
    assert route_paths.count("/workflow/ngc/deploy") == 1
    assert "/workflow/plugin/hidden" not in route_paths


@pytest.mark.asyncio
async def test_metadata_includes_plugin_api_workflows_once_and_types_include_all(
    mocker: MockerFixture,
) -> None:
    registry = _plugin_registry()
    mocker.patch.object(workflow_v1, "WORKFLOW_API_CATALOG", tuple(registry.api_workflows))
    mocker.patch.object(workflow_v1, "WORKFLOW_TYPE_CATALOG", tuple(registry.all_workflows))
    mocker.patch.object(
        workflow_v1,
        "WORKFLOW_FORM_CATALOG",
        WorkflowFormCatalog.build(registry),
    )
    rbac = MagicMock()
    rbac.get_workflow_roles.side_effect = lambda workflow_name: {
        "read_roles": {"reader", workflow_name},
        "execute_roles": {"executor", workflow_name},
    }
    mocker.patch.object(workflow_catalog, "RBACConfig", return_value=rbac)

    workflow_types = await workflow_v1.get_workflow_types()
    metadata = await workflow_v1.get_workflow_metadata()
    metadata_names = [item.name for item in metadata.workflows]

    assert workflow_types.count(_VisiblePluginWorkflow.__name__) == 1
    assert metadata_names.count(_VisiblePluginWorkflow.__name__) == 1
    assert _HiddenPluginWorkflow.__name__ not in metadata_names
    # /types lists every registered workflow type, including API-disabled ones.
    assert workflow_types.count(_HiddenPluginWorkflow.__name__) == 1
    visible = metadata.workflows[metadata_names.index(_VisiblePluginWorkflow.__name__)]
    assert visible.endpoint == "/plugin/visible"
    assert visible.form_id == "visible-plugin"
    assert visible.read_roles == [_VisiblePluginWorkflow.__name__, "reader"]
    assert visible.execute_roles == [_VisiblePluginWorkflow.__name__, "executor"]
    assert visible.has_form is True
    rbac.get_workflow_roles.assert_any_call(_VisiblePluginWorkflow.__name__)
