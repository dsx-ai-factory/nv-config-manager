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
"""Tests for workflow-owned, dynamically generated form-option endpoints."""

import asyncio
from typing import Literal
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from nv_config_manager.temporal.api import form_option_endpoints
from nv_config_manager.temporal.api.form_option_endpoints import (
    build_workflow_form_surface,
)
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.form_catalog import (
    FormOptionProviderBinding,
    WorkflowFormCatalog,
)
from nv_config_manager_workflows.ui import (
    FormOptionProvider,
    FormOptionSource,
    OptionItem,
    OptionSourceResponse,
    WorkflowFormContractError,
)

_MODULE = "tests.temporal.api.test_form_option_endpoints"


class ProviderQuery(BaseModel):
    """A representative flat provider query."""

    model_config = ConfigDict(extra="forbid")

    site: str
    tenant: list[str] = []
    mode: Literal["brief", "full"] = "brief"


class ProviderWorkflow(WorkflowMetadataMixin):
    """Workflow identity used to build a stable provider URL."""

    workflow_form_enabled = True
    workflow_form_id = "provider"


class SecondProviderWorkflow(WorkflowMetadataMixin):
    """A second owner that shares the same resolver implementation."""

    workflow_form_enabled = True
    workflow_form_id = "second-provider"


class CollisionProviderWorkflow(WorkflowMetadataMixin):
    """A workflow whose form/source pair collided under flattened operation IDs."""

    workflow_form_enabled = True
    workflow_form_id = "provider-fabric"


async def valid_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Return query-derived options for HTTP serialization assertions."""
    return OptionSourceResponse(
        items=[
            OptionItem(
                label=query.site,
                value=",".join(query.tenant),
                description=query.mode,
            )
        ]
    )


async def failing_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Raise an error containing content that must not reach the client."""
    raise RuntimeError(f"secret failure at {query.site}")


async def invalid_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Return data that cannot satisfy the normalized response contract."""
    return {"items": [{"value": query.site}]}  # type: ignore[return-value]


async def constructed_invalid_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Return an existing model instance that bypassed normal construction."""
    return OptionSourceResponse.model_construct(items=[{"value": query.site}])


async def slow_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Take long enough for a test-controlled provider deadline to expire."""
    await asyncio.sleep(1)
    return OptionSourceResponse(items=[OptionItem(label=query.site, value=query.site)])


async def many_items_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Return more items than a test-controlled platform limit."""
    return OptionSourceResponse(
        items=[
            OptionItem(label=f"{query.site}-{position}", value=str(position))
            for position in range(2)
        ]
    )


def sync_resolver(query: ProviderQuery) -> OptionSourceResponse:
    """Intentionally violate the async provider contract."""
    return OptionSourceResponse(items=[OptionItem(label=query.site, value=query.site)])


def _binding(
    *,
    workflow: type[WorkflowMetadataMixin] = ProviderWorkflow,
    resolver: str = "valid_resolver",
    source: str = "fabric-profiles",
    plugin: str = "fixture-plugin",
) -> FormOptionProviderBinding:
    workflow_form_id = workflow.get_workflow_form_id()
    assert workflow_form_id is not None
    return FormOptionProviderBinding(
        workflow=workflow,
        workflow_form_id=workflow_form_id,
        plugin=plugin,
        source=source,
        endpoint=f"/v1/workflow/{workflow_form_id}/form-options/{source}",
        declaration=FormOptionProvider(
            resolver=f"{_MODULE}:{resolver}",
            query_model=f"{_MODULE}:ProviderQuery",
        ),
        uses=(FormOptionSource(source, params={"site": "site-a"}),),
    )


def _catalog(*bindings: FormOptionProviderBinding) -> WorkflowFormCatalog:
    workflows = {binding.workflow for binding in bindings}
    return WorkflowFormCatalog(
        forms={workflow: {"schema": {}} for workflow in workflows},
        form_ids={
            workflow: workflow_form_id
            for workflow in workflows
            if (workflow_form_id := workflow.get_workflow_form_id()) is not None
        },
        providers=bindings,
    )


def _client(
    surface_router,
    mocker,
    *,
    authorized: bool = True,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.user = "user@example.com"
        request.state.roles = {"executor"}
        return await call_next(request)

    if authorized:
        mocker.patch.object(form_option_endpoints, "require_workflow_execute_access")
    else:
        rbac = MagicMock()
        rbac.get_workflow_roles.return_value = {
            "read_roles": {"reader"},
            "execute_roles": {"different-role"},
        }
        mocker.patch(
            "nv_config_manager.temporal.api.workflow_authorization.RBACConfig",
            return_value=rbac,
        )
    app.include_router(surface_router, prefix="/v1/workflow")
    return TestClient(app)


def test_surface_generates_typed_get_endpoint_and_openapi(mocker) -> None:
    binding = _binding()
    surface = build_workflow_form_surface(_catalog(binding))
    client = _client(surface.router, mocker)
    path = "/v1/workflow/provider/form-options/fabric-profiles"

    response = client.get(
        path,
        params=[("site", "site-b"), ("tenant", "one"), ("tenant", "two")],
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "label": "site-b",
            "value": "one,two",
            "description": "brief",
        }
    ]
    operation = client.app.openapi()["paths"][path]["get"]
    assert operation["operationId"] == ("get_workflow_form_options_provider__fabric_profiles")
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "site",
        "tenant",
        "mode",
    }
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }
    for status in ("403", "502", "504"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }


def test_unknown_query_parameter_uses_standard_422_shape(mocker) -> None:
    surface = build_workflow_form_surface(_catalog(_binding()))
    client = _client(surface.router, mocker)

    response = client.get(
        "/v1/workflow/provider/form-options/fabric-profiles",
        params={"site": "site-a", "unknown": "value"},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"][:2] == ["query", "unknown"]


@pytest.mark.parametrize(
    ("resolver", "detail"),
    [
        ("failing_resolver", "Option provider failed"),
        ("invalid_resolver", "Option provider returned invalid data"),
        ("constructed_invalid_resolver", "Option provider returned invalid data"),
    ],
)
def test_runtime_provider_failures_are_sanitized(mocker, resolver: str, detail: str) -> None:
    surface = build_workflow_form_surface(_catalog(_binding(resolver=resolver)))
    client = _client(surface.router, mocker)

    response = client.get(
        "/v1/workflow/provider/form-options/fabric-profiles",
        params={"site": "do-not-echo"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    assert "do-not-echo" not in response.text


def test_timeout_includes_concurrency_wait(mocker) -> None:
    class WaitingLimiter:
        async def __aenter__(self):
            await asyncio.sleep(1)

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    mocker.patch.object(form_option_endpoints, "_provider_limiter", WaitingLimiter())
    mocker.patch.object(form_option_endpoints, "_PROVIDER_TIMEOUT_SECONDS", 0.001)
    surface = build_workflow_form_surface(_catalog(_binding(resolver="slow_resolver")))
    client = _client(surface.router, mocker)

    response = client.get(
        "/v1/workflow/provider/form-options/fabric-profiles",
        params={"site": "site-a"},
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "Option provider timed out"}


def test_result_limit_is_platform_controlled(mocker) -> None:
    mocker.patch.object(form_option_endpoints, "_PROVIDER_MAX_ITEMS", 1)
    surface = build_workflow_form_surface(_catalog(_binding(resolver="many_items_resolver")))
    client = _client(surface.router, mocker)

    response = client.get(
        "/v1/workflow/provider/form-options/fabric-profiles",
        params={"site": "site-a"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Option provider returned too many items"}


def test_endpoint_requires_the_workflow_execute_role(mocker) -> None:
    surface = build_workflow_form_surface(_catalog(_binding()))
    client = _client(surface.router, mocker, authorized=False)

    response = client.get(
        "/v1/workflow/provider/form-options/fabric-profiles",
        params={"site": "site-a"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorized to execute this workflow"}


def test_third_party_route_compilation_failure_withdraws_form_atomically() -> None:
    good = _binding()
    bad = _binding(resolver="sync_resolver", source="bad-source")

    surface = build_workflow_form_surface(_catalog(good, bad))

    assert ProviderWorkflow not in surface.catalog.forms
    assert ProviderWorkflow in surface.catalog.diagnostics
    assert not surface.catalog.providers
    assert not surface.router.routes


def test_builtin_route_compilation_failure_is_fatal() -> None:
    binding = _binding(resolver="sync_resolver", plugin=BUILTIN_PLUGIN_NAME)

    with pytest.raises(WorkflowFormContractError, match="resolver must be an async function"):
        build_workflow_form_surface(_catalog(binding))


def test_workflows_can_share_a_resolver_but_get_unique_routes() -> None:
    first = _binding()
    second = _binding(workflow=SecondProviderWorkflow)

    surface = build_workflow_form_surface(_catalog(first, second))
    app = FastAPI()
    app.include_router(surface.router, prefix="/v1/workflow")

    paths = app.openapi()["paths"]
    assert {path for path in paths if "/form-options/" in path} == {
        "/v1/workflow/provider/form-options/fabric-profiles",
        "/v1/workflow/second-provider/form-options/fabric-profiles",
    }
    assert (
        paths["/v1/workflow/second-provider/form-options/fabric-profiles"]["get"]["operationId"]
        == "get_workflow_form_options_second_provider__fabric_profiles"
    )


def test_operation_ids_preserve_the_form_and_source_boundary() -> None:
    first = _binding(source="fabric-profiles")
    second = _binding(workflow=CollisionProviderWorkflow, source="profiles")

    surface = build_workflow_form_surface(_catalog(first, second))
    app = FastAPI()
    app.include_router(surface.router, prefix="/v1/workflow")

    operation_ids = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
    }
    assert operation_ids == {
        "get_workflow_form_options_provider__fabric_profiles",
        "get_workflow_form_options_provider_fabric__profiles",
    }
