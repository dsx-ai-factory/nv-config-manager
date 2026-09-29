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

from nv_config_manager.temporal.api import dynamic_endpoints, workflow_v1
from nv_config_manager.temporal.api.dynamic_endpoints import register_dynamic_endpoints
from nv_config_manager.temporal.api.workflow_catalog import build_workflow_api_catalog
from nv_config_manager.temporal.ngc.workflows.deploy import DeployWorkflow
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.builtin import (
    BUILTIN_PLUGIN_NAME,
    builtin_plugin,
)
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import WorkflowConflictError
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
    workflow_description = "Conflicts with a service-owned endpoint"
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
    return WorkflowRegistry.build(
        {
            BUILTIN_PLUGIN_NAME: builtin_plugin(),
            descriptor.name: descriptor,
        }
    )


def test_catalog_appends_only_registry_api_workflows() -> None:
    catalog = build_workflow_api_catalog(_plugin_registry())

    assert DeployWorkflow in catalog
    assert _VisiblePluginWorkflow in catalog
    assert _HiddenPluginWorkflow not in catalog


def test_catalog_rejects_plugin_collisions_with_service_workflows() -> None:
    descriptor = WorkflowPluginDescriptor(
        name="api-catalog-collision-test",
        workflows=(_DeployEndpointCollisionWorkflow,),
    )
    registry = WorkflowRegistry.build(
        {
            BUILTIN_PLUGIN_NAME: builtin_plugin(),
            descriptor.name: descriptor,
        }
    )

    with pytest.raises(WorkflowConflictError, match="workflow API endpoint"):
        build_workflow_api_catalog(registry)


def test_dynamic_routes_include_only_api_enabled_plugin_workflows() -> None:
    catalog = build_workflow_api_catalog(_plugin_registry())
    router = APIRouter(prefix="/workflow")

    register_dynamic_endpoints(router, workflows=catalog)

    route_paths = {route.path for route in router.routes}
    assert "/workflow/plugin/visible" in route_paths
    assert "/workflow/plugin/hidden" not in route_paths


@pytest.mark.asyncio
async def test_types_and_metadata_include_only_api_enabled_plugin_workflows(
    mocker: MockerFixture,
) -> None:
    catalog = build_workflow_api_catalog(_plugin_registry())
    mocker.patch.object(workflow_v1, "WORKFLOW_API_CATALOG", catalog)
    rbac = MagicMock()
    rbac.get_workflow_roles.side_effect = lambda workflow_name: {
        "read_roles": {"reader", workflow_name},
        "execute_roles": {"executor", workflow_name},
    }
    mocker.patch.object(dynamic_endpoints, "RBACConfig", return_value=rbac)

    workflow_types = await workflow_v1.get_workflow_types()
    metadata = await workflow_v1.get_workflow_metadata()
    workflows_by_name = {item.name: item for item in metadata.workflows}

    assert _VisiblePluginWorkflow.__name__ in workflow_types
    assert _HiddenPluginWorkflow.__name__ not in workflow_types
    assert _VisiblePluginWorkflow.__name__ in workflows_by_name
    assert _HiddenPluginWorkflow.__name__ not in workflows_by_name
    visible = workflows_by_name[_VisiblePluginWorkflow.__name__]
    assert visible.endpoint == "/plugin/visible"
    assert visible.read_roles == [_VisiblePluginWorkflow.__name__, "reader"]
    assert visible.execute_roles == [_VisiblePluginWorkflow.__name__, "executor"]
    rbac.get_workflow_roles.assert_any_call(_VisiblePluginWorkflow.__name__)
