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
"""Workflow form declarations never reach MCP tool schemas."""

import copy
import inspect
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any, ClassVar, Literal, cast

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, create_model

from nv_config_manager.mcp import tools
from nv_config_manager.mcp.settings import MCPSettings
from nv_config_manager.mcp.workflows import MCPWorkflow, discover_mcp_workflows
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WorkflowRegistry,
    builtin_plugin,
)
from nv_config_manager_workflows.ui import (
    Dependency,
    FormExcluded,
    FormSchema,
    OptionSource,
    ServerOwned,
    api_options,
    build_form,
    device_field,
    location_field,
)
from nv_config_manager_workflows.ui.markers import FORM_MARKERS
from nv_config_manager_workflows.workflow_references import (
    DEVICE_REFERENCE,
    DeviceReferences,
    LocationReference,
)

_LOCATION_SOURCE = OptionSource(
    "/v1/parameter/location",
    "name",
    "id",
    type_key="location_type",
    params={"location_type": ["Site", "Module"]},
)


class _FormDeclarations:
    """A form declaration, as a class variable Pydantic ignores."""

    rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"ui:order": ["*"]}


def _plain_input() -> type[BaseModel]:
    class SiteDiagnosticsInput(BaseModel):
        """Run diagnostics across a site."""

        site: LocationReference = Field(description="Site to diagnose.")
        site_type: str | None = None
        devices: DeviceReferences = Field(default_factory=list, description="Devices.")
        device: str | None = Field(default=None, description="One device.")
        status: list[str] = Field(default=["Active"], description="Device statuses.")
        mode: Literal["basic", "advanced"] = "basic"
        user: str = ""
        trigger: str

    return SiteDiagnosticsInput


def _declared_input() -> type[BaseModel]:
    class SiteDiagnosticsInput(BaseModel):
        """Run diagnostics across a site."""

        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "ui:order": ["site", "devices", "*"],
            "site": location_field(_LOCATION_SOURCE, type_field="site_type"),
            "site_type": {"ui:widget": "hidden"},
            "devices": device_field(
                OptionSource("/v1/parameter/device", "name", "id"),
                filters=("site", "tenant"),
                site_field="site",
            ),
            "status": api_options(
                OptionSource(
                    "/v1/parameter/status",
                    "name",
                    "name",
                    depends_on={"location": Dependency("site", required=False)},
                )
            ),
            "trigger": {"ui:widget": "hidden"},
        }

        site: LocationReference = Field(description="Site to diagnose.")
        site_type: str | None = None
        devices: DeviceReferences = Field(default_factory=list, description="Devices.")
        device: Annotated[str | None, FormExcluded(), DEVICE_REFERENCE] = Field(
            default=None, description="One device."
        )
        status: Annotated[list[str], FormSchema(min_items=1)] = Field(
            default=["Active"], description="Device statuses."
        )
        mode: Literal["basic", "advanced"] = "basic"
        user: Annotated[str, ServerOwned()] = ""
        trigger: Annotated[str, FormSchema(default="API")]

    return SiteDiagnosticsInput


def _declared_twin(model: type[BaseModel], *, marker_first: bool) -> type[BaseModel]:
    """Return a same-named subclass that marks every field and declares a form.

    Each field gets a ``FormSchema`` marker in place of any marker it already has.
    """
    overrides: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        unmarked = copy.copy(info)
        unmarked.metadata = [
            entry for entry in info.metadata if not isinstance(entry, FORM_MARKERS)
        ]
        marker = FormSchema()
        metadata = (marker, unmarked) if marker_first else (unmarked, marker)
        overrides[name] = Annotated[(info.annotation, *metadata)]
    # Pydantic accepts a plain class-variable mixin as an extra base; its stubs do not.
    bases = cast(tuple[type[BaseModel], ...], (_FormDeclarations, model))
    return create_model(
        model.__name__,
        __base__=bases,
        __doc__=model.__doc__,
        __module__=model.__module__,
        **overrides,
    )


def _workflow(input_class: type[BaseModel]) -> MCPWorkflow:
    return MCPWorkflow(
        tool_name="run_site_diagnostics",
        workflow_name="SiteDiagnosticsWorkflow",
        description="Run diagnostics across a site.",
        endpoint="/diagnostics/site",
        input_class=input_class,
    )


async def _registered_input_schema(workflow: MCPWorkflow, settings: MCPSettings) -> str:
    """Register the workflow starter and return the tool input schema FastMCP serves."""
    server = FastMCP("test")
    tools._register_workflow_starter(server, settings, workflow)
    registered = next(tool for tool in await server.list_tools() if tool.name == workflow.tool_name)
    return json.dumps(registered.inputSchema)


@pytest.fixture
def settings() -> MCPSettings:
    return MCPSettings(
        workflow_api_url="http://workflow:9000",
        workflow_ui_url="https://config-manager.example.test",
        config_store_api_url="http://config-store:9000",
        dhcp_api_url="http://dhcp:9000",
        nautobot_url="http://nautobot",
        nautobot_read_only_token="token",
        nautobot_verify=True,
        nautobot_auth_mode="jwt",
        nautobot_token_fallback_enabled=False,
        max_response_bytes=10_000,
        nautobot_mcp_enabled=True,
    )


def test_the_declared_model_really_declares_a_form() -> None:
    form = build_form(_declared_input())

    assert set(form["schema"]["properties"]) == {
        "site",
        "site_type",
        "devices",
        "status",
        "mode",
        "trigger",
    }
    assert form["requires"] == [
        "core-field.api-options.v1",
        "core-field.device.v1",
        "core-field.location.v1",
    ]


def test_the_form_class_variable_is_not_a_model_field() -> None:
    declared = _declared_input()

    assert list(declared.model_fields) == list(_plain_input().model_fields)
    assert json.dumps(declared.model_json_schema()) == json.dumps(
        _plain_input().model_json_schema()
    )


def test_declarations_do_not_change_the_workflow_input_schema() -> None:
    plain = _workflow(_plain_input())
    declared = _workflow(_declared_input())

    assert json.dumps(declared.input_schema) == json.dumps(plain.input_schema)


def test_declarations_do_not_change_the_tool_signature_parameters() -> None:
    plain = tools._workflow_tool_signature(_workflow(_plain_input())).parameters
    declared = tools._workflow_tool_signature(_workflow(_declared_input())).parameters

    assert [(name, parameter.kind) for name, parameter in declared.items()] == [
        (name, parameter.kind) for name, parameter in plain.items()
    ]


async def test_declarations_do_not_change_the_fastmcp_tool_input_schema(
    settings: MCPSettings,
) -> None:
    plain = await _registered_input_schema(_workflow(_plain_input()), settings)
    declared = await _registered_input_schema(_workflow(_declared_input()), settings)

    assert declared == plain


MCP_WORKFLOWS = discover_mcp_workflows(
    WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()}).mcp_workflows
)


def test_mcp_workflows_are_discovered() -> None:
    assert MCP_WORKFLOWS


@pytest.mark.parametrize("marker_first", [True, False], ids=["marker-first", "marker-last"])
@pytest.mark.parametrize(
    "workflow", MCP_WORKFLOWS, ids=[workflow.tool_name for workflow in MCP_WORKFLOWS]
)
async def test_marking_a_real_mcp_workflow_input_leaves_its_schemas_unchanged(
    workflow: MCPWorkflow, marker_first: bool, settings: MCPSettings
) -> None:
    declared = replace(
        workflow, input_class=_declared_twin(workflow.input_class, marker_first=marker_first)
    )

    assert list(declared.input_class.model_fields) == list(workflow.input_class.model_fields)
    assert [
        (name, parameter.kind, parameter.default is inspect.Parameter.empty)
        for name, parameter in tools._workflow_tool_signature(declared).parameters.items()
    ] == [
        (name, parameter.kind, parameter.default is inspect.Parameter.empty)
        for name, parameter in tools._workflow_tool_signature(workflow).parameters.items()
    ]
    assert json.dumps(declared.input_schema) == json.dumps(workflow.input_schema)
    assert await _registered_input_schema(declared, settings) == await _registered_input_schema(
        workflow, settings
    )
