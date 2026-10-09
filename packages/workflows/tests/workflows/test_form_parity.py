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
"""Golden payload parity between legacy form pages and the generic form.

``ui/tests/e2e/fixtures/form-parity/<slug>.json`` holds, per scenario, the POST
body the legacy page sent and the one ``/workflows/new/<form_id>`` sends
(``ui/tests/e2e/formParity.spec.ts`` captures them). Byte equality is not
required: key order and omitted defaults differ legitimately. Both must validate
against the workflow's input model and be equal afterwards, and the generic
payload must also satisfy the workflow's ``/form`` schema, so form-only
strictness never rejects what the form itself sends.

A legacy UI bug the generic form deliberately does not reproduce goes in
``EXPECTED_DIFFERENCES`` with its reason; the test then requires the payloads to
differ, so the entry has to be removed once they agree.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel

from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME, builtin_plugin
from nv_config_manager_workflows.registration.registry import WorkflowRegistry

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _PACKAGE_ROOT.parent.parent
_UI_ROOT = _REPO_ROOT / "ui"
_FIXTURE_DIR = _UI_ROOT / "tests" / "e2e" / "fixtures" / "form-parity"
_LEGACY_WORKFLOW_REDIRECTS = _UI_ROOT / "src" / "config" / "legacy-workflow-redirects.json"
_WORKFLOW_FORMS = (
    _REPO_ROOT / "src" / "tests" / "temporal" / "api" / "fixtures" / "workflow_forms.json"
)
_IN_REPOSITORY = (_REPO_ROOT / "packages" / _PACKAGE_ROOT.name).resolve() == _PACKAGE_ROOT and (
    _UI_ROOT / "package.json"
).is_file()

pytestmark = pytest.mark.skipif(
    not _IN_REPOSITORY, reason="the repository ui/ directory is absent (isolated package run)"
)

# (fixture slug, scenario name) -> why the generic payload intentionally differs.
EXPECTED_DIFFERENCES: dict[tuple[str, str], str] = {
    (
        "backupworkflow",
        "manual site and device",
    ): (
        "the legacy Backup form sent user_domain 'nvidia.com'; the field is now FormExcluded, "
        "so the generic form omits it and the API retains its legacy missing-value fallback; "
        "the blank workflow_id and "
        "intended_config_commit_id it also sent are FormExcluded"
    ),
}


def _fixtures() -> dict[str, dict[str, Any]]:
    if not _IN_REPOSITORY:
        return {}
    return {path.stem: json.loads(path.read_text()) for path in sorted(_FIXTURE_DIR.glob("*.json"))}


def _scenarios() -> list[Any]:
    return [
        pytest.param(slug, fixture, scenario, id=f"{slug}:{scenario['name']}")
        for slug, fixture in _fixtures().items()
        for scenario in fixture["scenarios"]
    ]


def _input_models() -> dict[str, type[BaseModel] | None]:
    registry = WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})
    return {
        workflow.__name__: workflow.get_workflow_input_class()
        for workflow in registry.api_workflows
    }


@pytest.fixture(scope="module")
def input_models() -> dict[str, type[BaseModel] | None]:
    return _input_models()


def test_every_redirected_workflow_has_parity_scenarios() -> None:
    redirects = json.loads(_LEGACY_WORKFLOW_REDIRECTS.read_text())
    fixtures = _fixtures()
    covered = {fixture["workflow"] for fixture in fixtures.values() if fixture["scenarios"]}
    redirected = set(redirects)

    assert redirected <= covered, (
        f"redirected without parity scenarios: {sorted(redirected - covered)}"
    )
    for slug, fixture in fixtures.items():
        assert redirects[fixture["workflow"]] == slug


def test_expected_differences_name_captured_scenarios() -> None:
    captured = {
        (slug, scenario["name"])
        for slug, fixture in _fixtures().items()
        for scenario in fixture["scenarios"]
    }
    assert set(EXPECTED_DIFFERENCES) <= captured


@pytest.mark.parametrize(("slug", "fixture", "scenario"), _scenarios())
def test_legacy_and_generic_payloads_validate_to_the_same_input(
    slug: str,
    fixture: dict[str, Any],
    scenario: dict[str, Any],
    input_models: dict[str, type[BaseModel] | None],
) -> None:
    input_model = input_models.get(fixture["workflow"])
    assert input_model is not None, f"{fixture['workflow']} is not an API workflow with an input"
    assert input_model.__name__ == fixture["input_model"]
    for side in ("legacy_payload", "generic_payload"):
        assert isinstance(scenario[side], dict), f"{side} was never captured"

    legacy = input_model.model_validate(scenario["legacy_payload"])
    generic = input_model.model_validate(scenario["generic_payload"])
    form_schema = json.loads(_WORKFLOW_FORMS.read_text())[fixture["workflow"]]["schema"]
    form_errors = Draft202012Validator(form_schema).iter_errors(scenario["generic_payload"])
    assert [error.message for error in form_errors] == []

    reason = EXPECTED_DIFFERENCES.get((slug, scenario["name"]))
    if reason is None:
        assert generic == legacy
    else:
        assert generic != legacy, f"now equal; remove the expected difference ({reason})"
