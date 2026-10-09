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
"""Tests for ``GET /v1/workflow/{form_id}/form``.

``fixtures/workflow_forms.json`` records the v1 form envelope of every built-in API
workflow except those in ``fixtures/workflow_form_exclusions.json``, keyed by workflow
class name. Each exclusion maps a workflow to the reason it has no launcher form. The
snapshot keeps the server's key order, because the form renderer lays fields out in
``schema.properties`` order. Regenerate it only after an intentional change with::

    NVCM_UPDATE_SNAPSHOTS=1 uv run pytest src/tests/temporal/api/test_workflow_form.py
"""

import difflib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from pytest_mock import MockerFixture
from temporalio import workflow as temporal_workflow
from temporalio.service import RPCError, RPCStatusCode

from nv_config_manager.temporal.api import workflow_v1
from nv_config_manager.temporal.api.dynamic_endpoints import register_dynamic_endpoints
from nv_config_manager.temporal.api.main import app
from nv_config_manager.temporal.api.workflow_catalog import (
    WORKFLOW_API_CATALOG,
    WORKFLOW_FORM_CATALOG,
    WORKFLOW_REGISTRY,
    WORKFLOW_TYPE_CATALOG,
)
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WorkflowPluginDescriptor,
    WorkflowRegistry,
)
from nv_config_manager_workflows.registration.form_catalog import (
    WORKFLOW_FORM_ID_PATTERN,
    WorkflowFormCatalog,
)
from nv_config_manager_workflows.stage import StageMixin
from nv_config_manager_workflows.ui import OptionSource, WorkflowFormContractError, api_options
from nv_config_manager_workflows.workflows.backup import BackupWorkflow

_SNAPSHOT = Path(__file__).with_name("fixtures") / "workflow_forms.json"
_EXCLUSIONS: dict[str, str] = json.loads(
    (Path(__file__).with_name("fixtures") / "workflow_form_exclusions.json").read_text()
)
_UPDATE_SNAPSHOTS = os.environ.get("NVCM_UPDATE_SNAPSHOTS") == "1"
_MAX_DIFF_LINES = 60
_FORM_KEYS = ["schema", "ui_schema", "ui_schema_version", "requires"]


class _BrokenFormInput(BaseModel):
    """A third-party input whose form names a widget the contract does not know."""

    rjsf_ui_schema: ClassVar[dict[str, Any]] = {"target": {"ui:widget": "radio"}}

    target: str


class _MalformedSourceInput(BaseModel):
    """A plugin input whose option source is malformed; importing it must still succeed."""

    rjsf_ui_schema: ClassVar[dict[str, Any]] = {
        "target": api_options(OptionSource("v1/targets", "", "name"))
    }

    target: str


@temporal_workflow.defn
class _ThirdPartyWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Third Party"
    workflow_description = "A plugin workflow whose form is invalid"
    workflow_input_class = _BrokenFormInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/acme/third_party"
    workflow_form_enabled = True
    workflow_form_id = "third-party"

    @temporal_workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def forms(client: TestClient) -> dict[str, Any]:
    """Return the form of every API-enabled workflow, keyed by class name."""
    forms: dict[str, Any] = {}
    for workflow in sorted(WORKFLOW_FORM_CATALOG.forms, key=lambda item: item.__name__):
        name = workflow.__name__
        form_id = WORKFLOW_FORM_CATALOG.form_ids[workflow]
        rsp = client.get(f"/v1/workflow/{form_id}/form")
        assert rsp.status_code == 200, (name, rsp.text)
        forms[name] = rsp.json()
    return forms


@pytest.fixture
def third_party_registry() -> WorkflowRegistry:
    return WorkflowRegistry.build(
        {
            "acme": WorkflowPluginDescriptor(name="acme", workflows=(_ThirdPartyWorkflow,)),
        }
    )


@pytest.fixture
def third_party_form_catalog(third_party_registry: WorkflowRegistry) -> WorkflowFormCatalog:
    return WorkflowFormCatalog.build(third_party_registry)


def _render(data: Any) -> str:
    # No sort_keys: properties order is the form's field order.
    return json.dumps(data, indent=2) + "\n"


def _bounded_diff(expected: str, actual: str) -> str:
    diff = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="snapshot",
            tofile="current",
            n=2,
            lineterm="",
        )
    )
    if len(diff) > _MAX_DIFF_LINES:
        omitted = len(diff) - _MAX_DIFF_LINES
        diff = [*diff[:_MAX_DIFF_LINES], f"... ({omitted} more diff lines)"]
    return "\n".join(f"    {line}" for line in diff)


def _refs(node: Any) -> Iterator[str]:
    """Yield every ``$ref`` value in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def _resolves(document: dict[str, Any], ref: str) -> bool:
    """Return whether ``ref`` is a JSON pointer into ``document`` that names a value."""
    if not ref.startswith("#/"):
        return False
    target: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or token not in target:
            return False
        target = target[token]
    return True


@pytest.fixture
def missing_executions(mocker: MockerFixture) -> None:
    """Make Temporal report every workflow execution as not found."""

    def handle(workflow_id: str) -> MagicMock:
        missing = MagicMock(id=workflow_id)
        missing.describe = AsyncMock(
            side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
        )
        return missing

    temporal = MagicMock()
    temporal.get_workflow_handle.side_effect = handle
    mocker.patch.object(workflow_v1, "get_client", AsyncMock(return_value=temporal))


def test_form_of_an_api_workflow(client: TestClient) -> None:
    rsp = client.get("/v1/workflow/backup/form")

    assert rsp.status_code == 200
    body = rsp.json()
    assert list(body) == _FORM_KEYS
    assert body == WORKFLOW_FORM_CATALOG.forms[BackupWorkflow]
    assert body["ui_schema_version"] == 1
    assert body["requires"] == ["core-field.device.v1"]
    assert "user" not in body["schema"]["properties"]


def test_form_openapi_uses_stable_identifiers(client: TestClient) -> None:
    openapi = client.app.openapi()

    operation = openapi["paths"]["/v1/workflow/{form_id}/form"]["get"]
    assert operation["operationId"] == "get_workflow_form"
    form_id_schema = openapi["components"]["schemas"]["WorkflowMetadata"]["properties"]["form_id"]
    assert {candidate.get("pattern") for candidate in form_id_schema["anyOf"]} == {
        WORKFLOW_FORM_ID_PATTERN,
        None,
    }


@pytest.mark.parametrize("name", ["no-such-workflow", "backupworkflow", "metadata"])
def test_unknown_workflow_has_no_form(client: TestClient, name: str) -> None:
    rsp = client.get(f"/v1/workflow/{name}/form")

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow '{name}' not found"}


def test_malformed_workflow_form_id_is_rejected(client: TestClient) -> None:
    rsp = client.get("/v1/workflow/NoSuchWorkflow/form")

    assert rsp.status_code == 422
    assert rsp.json()["detail"][0]["loc"] == ["path", "form_id"]


@pytest.mark.parametrize(
    ("name", "form_id"),
    [
        ("TenantDeployWorkflow", "tenant-deploy"),
        ("BatchDeployWorkflow", "batch-deploy"),
    ],
)
def test_api_disabled_workflow_has_no_form(client: TestClient, name: str, form_id: str) -> None:
    """A registered but API-disabled workflow is indistinguishable from an unknown name."""
    assert name in {workflow.__name__ for workflow in WORKFLOW_TYPE_CATALOG}
    assert name not in {workflow.__name__ for workflow in WORKFLOW_API_CATALOG}

    rsp = client.get(f"/v1/workflow/{form_id}/form")

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow '{form_id}' not found"}


@pytest.mark.parametrize("name", sorted(_EXCLUSIONS))
def test_form_disabled_workflow_has_no_form(client: TestClient, name: str) -> None:
    """Form policy does not disable the workflow's existing execution endpoint."""
    workflow = next(workflow for workflow in WORKFLOW_API_CATALOG if workflow.__name__ == name)
    assert workflow not in WORKFLOW_FORM_CATALOG.forms

    form_id = "form-disabled"
    rsp = client.get(f"/v1/workflow/{form_id}/form")

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow '{form_id}' not found"}
    execution = client.post(f"/v1/workflow{workflow.get_workflow_api_endpoint()}", json=[])
    assert execution.status_code == 422


def test_an_invalid_third_party_form_is_unavailable(
    client: TestClient,
    mocker: MockerFixture,
    third_party_registry: WorkflowRegistry,
    third_party_form_catalog: WorkflowFormCatalog,
) -> None:
    mocker.patch.object(
        workflow_v1, "WORKFLOW_API_CATALOG", tuple(third_party_registry.api_workflows)
    )
    mocker.patch.object(workflow_v1, "WORKFLOW_FORM_CATALOG", third_party_form_catalog)

    default_metadata = client.get("/v1/workflow/metadata")
    metadata = client.get("/v1/workflow/metadata", params={"include": "form"})
    rsp = client.get("/v1/workflow/third-party/form")

    assert default_metadata.status_code == 200
    assert metadata.status_code == 200
    [default_entry] = default_metadata.json()["workflows"]
    [entry] = metadata.json()["workflows"]
    assert default_entry["namespace"] is None
    assert set(entry) == {*default_entry, "group", "form_id", "has_form"}
    assert entry["form_id"] == "third-party"
    assert entry["has_form"] is True
    assert rsp.status_code == 503
    detail = rsp.json()["detail"]
    assert {key: detail[key] for key in ("code", "plugin", "workflow")} == {
        "code": "workflow_form_unavailable",
        "plugin": "acme",
        "workflow": "_ThirdPartyWorkflow",
    }
    assert detail["message"] == workflow_v1._WORKFLOW_FORM_UNAVAILABLE_MESSAGE
    assert "ui:widget 'radio' is not one of" not in rsp.text


def test_metadata_marks_a_form_without_a_valid_form_id_unavailable(
    client: TestClient,
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(_ThirdPartyWorkflow, "workflow_form_id", None)
    registry = WorkflowRegistry.build(
        {"acme": WorkflowPluginDescriptor(name="acme", workflows=(_ThirdPartyWorkflow,))}
    )
    form_catalog = WorkflowFormCatalog.build(registry)
    mocker.patch.object(workflow_v1, "WORKFLOW_API_CATALOG", tuple(registry.api_workflows))
    mocker.patch.object(workflow_v1, "WORKFLOW_FORM_CATALOG", form_catalog)

    response = client.get("/v1/workflow/metadata", params={"include": "form"})

    assert response.status_code == 200
    [entry] = response.json()["workflows"]
    assert entry["form_id"] is None
    assert entry["has_form"] is False


def test_a_malformed_option_source_fails_only_its_form(
    client: TestClient, mocker: MockerFixture
) -> None:
    """Registration, not the plugin's import, rejects it: third-party 503, built-in fatal."""
    mocker.patch.object(_ThirdPartyWorkflow, "workflow_input_class", _MalformedSourceInput)
    registry = WorkflowRegistry.build(
        {"acme": WorkflowPluginDescriptor(name="acme", workflows=(_ThirdPartyWorkflow,))}
    )
    form_catalog = WorkflowFormCatalog.build(registry)
    mocker.patch.object(workflow_v1, "WORKFLOW_API_CATALOG", tuple(registry.api_workflows))
    mocker.patch.object(workflow_v1, "WORKFLOW_FORM_CATALOG", form_catalog)

    rsp = client.get("/v1/workflow/third-party/form")

    assert rsp.status_code == 503
    assert rsp.json()["detail"]["message"] == workflow_v1._WORKFLOW_FORM_UNAVAILABLE_MESSAGE
    assert "endpoint must start with a single '/'" not in rsp.text
    builtin_registry = WorkflowRegistry.build(
        {
            BUILTIN_PLUGIN_NAME: WorkflowPluginDescriptor(
                name=BUILTIN_PLUGIN_NAME, workflows=(_ThirdPartyWorkflow,)
            )
        }
    )
    with pytest.raises(WorkflowFormContractError, match="must start with a single '/'"):
        WorkflowFormCatalog.build(builtin_registry)


def test_an_invalid_third_party_form_keeps_its_execution_endpoint(
    third_party_registry: WorkflowRegistry,
) -> None:
    router = APIRouter()

    register_dynamic_endpoints(router, third_party_registry.api_workflows)

    assert "/acme/third_party" in {getattr(route, "path", None) for route in router.routes}


def test_every_form_has_the_v1_envelope_shape(forms: dict[str, Any]) -> None:
    assert len(forms) == len(WORKFLOW_FORM_CATALOG.forms)
    for name, form in forms.items():
        assert list(form) == _FORM_KEYS, name
        assert form["ui_schema_version"] == 1, name
        assert form["requires"] == sorted(form["requires"]), name


def test_every_schema_ref_resolves_inside_the_form(forms: dict[str, Any]) -> None:
    refs = [(name, ref) for name, form in forms.items() for ref in _refs(form["schema"])]
    unresolved = [
        f"{name}: {ref}" for name, ref in refs if not _resolves(forms[name]["schema"], ref)
    ]

    assert refs, "expected built-in workflow inputs to use $defs"
    assert unresolved == []


def test_builtin_form_catalog_matches_the_exclusion_policy() -> None:
    """Every built-in API workflow has a snapshotted form unless it is explicitly excluded."""
    builtin = {
        workflow.__name__
        for workflow in WORKFLOW_REGISTRY.api_workflows
        if WORKFLOW_REGISTRY.owner(workflow) == BUILTIN_PLUGIN_NAME
    }
    available = {
        workflow.__name__
        for workflow in WORKFLOW_FORM_CATALOG.forms
        if WORKFLOW_REGISTRY.owner(workflow) == BUILTIN_PLUGIN_NAME
    }
    diagnostics = [
        workflow.__name__
        for workflow in WORKFLOW_FORM_CATALOG.diagnostics
        if WORKFLOW_REGISTRY.owner(workflow) == BUILTIN_PLUGIN_NAME
    ]

    assert diagnostics == []
    assert set(_EXCLUSIONS) - builtin == set(), "stale exclusions"
    assert all(reason.strip() for reason in _EXCLUSIONS.values())
    assert builtin - available == set(_EXCLUSIONS)
    assert set(json.loads(_SNAPSHOT.read_text())) == available


def test_forms_match_snapshot(forms: dict[str, Any]) -> None:
    forms = {name: form for name, form in forms.items() if name not in _EXCLUSIONS}
    current = _render(forms)
    if _UPDATE_SNAPSHOTS:
        _SNAPSHOT.write_text(current)
    if not _SNAPSHOT.exists():
        pytest.fail(
            f"Missing {_SNAPSHOT.name}; capture it with NVCM_UPDATE_SNAPSHOTS=1", pytrace=False
        )

    stored_text = _SNAPSHOT.read_text()
    if stored_text == current:
        return
    stored: dict[str, Any] = json.loads(stored_text)
    problems = [f"{name}: added" for name in forms if name not in stored]
    problems += [f"{name}: removed" for name in stored if name not in forms]
    problems += [
        f"{name}: form changed\n{_bounded_diff(_render(stored[name]), _render(forms[name]))}"
        for name in forms
        if name in stored and _render(stored[name]) != _render(forms[name])
    ]
    pytest.fail(
        f"{_SNAPSHOT.name} is stale; regenerate it with NVCM_UPDATE_SNAPSHOTS=1:\n\n"
        + ("\n\n".join(problems) or "formatting differs"),
        pytrace=False,
    )


def test_catalog_routes_still_answer(client: TestClient) -> None:
    metadata = client.get("/v1/workflow/metadata")
    types = client.get("/v1/workflow/types")

    assert metadata.status_code == 200
    assert "DeployWorkflow" in {item["name"] for item in metadata.json()["workflows"]}
    assert types.status_code == 200
    assert "TenantDeployWorkflow" in types.json()


@pytest.mark.usefixtures("missing_executions")
@pytest.mark.parametrize(
    ("method", "path", "workflow_id"),
    [
        ("GET", "/v1/workflow/form", "form"),
        ("GET", "/v1/workflow/DeployWorkflow", "DeployWorkflow"),
        ("GET", "/v1/workflow/wf-1/tech-support/form", "wf-1"),
        ("GET", "/v1/workflow/DeployWorkflow/tech-support/switch-1", "DeployWorkflow"),
        ("POST", "/v1/workflow/DeployWorkflow/terminate", "DeployWorkflow"),
    ],
)
def test_execution_routes_still_receive_their_requests(
    client: TestClient, method: str, path: str, workflow_id: str
) -> None:
    """These paths still reach the execution handlers, which look the ID up in Temporal."""
    rsp = client.request(method, path)

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow with ID '{workflow_id}' not found"}


def test_dynamic_workflow_routes_still_validate_their_input(client: TestClient) -> None:
    for workflow in WORKFLOW_API_CATALOG:
        path = f"/v1/workflow{workflow.get_workflow_api_endpoint()}"

        rsp = client.post(path, json=[])

        assert rsp.status_code == 422, path
        assert rsp.json()["detail"][0]["loc"] == ["body"], path


def test_form_operation_is_published_under_its_operation_id() -> None:
    spec = app.openapi()
    path_item = spec["paths"]["/v1/workflow/{form_id}/form"]

    assert list(path_item) == ["get"]
    assert path_item["get"]["operationId"] == "get_workflow_form"
    [form_id_parameter] = path_item["get"]["parameters"]
    assert form_id_parameter["name"] == "form_id"
    assert form_id_parameter["description"] == (
        "Stable lowercase kebab-case workflow form identifier."
    )
    assert form_id_parameter["schema"]["pattern"] == "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
    assert path_item["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkflowFormResponse"
    }
    response_schema = spec["components"]["schemas"]["WorkflowFormResponse"]
    assert list(response_schema["properties"]) == _FORM_KEYS
    assert response_schema["required"] == _FORM_KEYS
    assert response_schema["properties"]["schema"] == {
        "additionalProperties": True,
        "title": "Schema",
        "type": "object",
    }
    assert response_schema["properties"]["ui_schema"] == {
        "$ref": "#/components/schemas/WorkflowFormUiSchema"
    }
    ui_schema = spec["components"]["schemas"]["WorkflowFormUiSchema"]
    assert ui_schema["additionalProperties"] is False
    assert set(ui_schema["properties"]) == {
        "ui:globalOptions",
        "ui:order",
        "ui:submitButtonOptions",
    }
    assert (
        ui_schema["patternProperties"]["^[A-Za-z_][A-Za-z0-9_]*$"]["additionalProperties"] is False
    )
    assert list(_refs(ui_schema)) == []
    assert response_schema["properties"]["ui_schema_version"]["const"] == 1
    assert path_item["get"]["responses"]["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkflowFormNotFoundResponse"
    }
    assert path_item["get"]["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkflowFormUnavailableResponse"
    }


def test_form_response_rejects_an_unsupported_schema_version() -> None:
    with pytest.raises(ValidationError, match="Input should be 1"):
        workflow_v1.WorkflowFormResponse.model_validate(
            {"schema": {}, "ui_schema": {}, "ui_schema_version": 2, "requires": []}
        )
