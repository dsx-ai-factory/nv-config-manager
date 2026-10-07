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
"""Additive-only compatibility guards for the Temporal Workflow API.

``make openapi-check`` accepts any contract change once the spec is regenerated,
so it does not enforce compatibility. These tests do: every path, method, and
component schema in the released baseline must still exist unchanged. New
paths, methods, component schemas, and optional component properties are allowed.

Baselines live in ``fixtures/``:

``openapi_baseline.json``
    Verbatim copy of ``docs/api-specs/temporal.openapi.json`` on ``main`` at
    ``6379a065``.
    It is never rewritten by a test run. Change it only as a deliberate,
    reviewed contract change, by copying the regenerated spec::

        make openapi
        cp docs/api-specs/temporal.openapi.json \\
            src/tests/temporal/api/fixtures/openapi_baseline.json

``workflow_metadata_baseline.json``
    ``GET /v1/workflow/metadata`` captured with deterministic RBAC roles. The
    live response must be a superset: every baseline workflow (keyed by
    ``name``) must be present with every baseline key and value unchanged.
    Regenerate it after an intentional change with::

        NVCM_UPDATE_SNAPSHOTS=1 uv run pytest src/tests/temporal/api/test_api_compat.py
"""

import difflib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from nv_config_manager.temporal.api import dynamic_endpoints
from nv_config_manager.temporal.api.main import app

_FIXTURES = Path(__file__).with_name("fixtures")
_OPENAPI_BASELINE = _FIXTURES / "openapi_baseline.json"
_METADATA_BASELINE = _FIXTURES / "workflow_metadata_baseline.json"
_UPDATE_SNAPSHOTS = os.environ.get("NVCM_UPDATE_SNAPSHOTS") == "1"
_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
_MAX_DIFF_LINES = 60


def _normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON so values compare the way the published spec does."""
    normalized: dict[str, Any] = json.loads(json.dumps(spec))
    return normalized


def _json_diff(baseline: Any, current: Any) -> str:
    """Return a bounded unified diff of two JSON values."""
    diff = list(
        difflib.unified_diff(
            json.dumps(baseline, indent=2, sort_keys=True).splitlines(),
            json.dumps(current, indent=2, sort_keys=True).splitlines(),
            fromfile="baseline",
            tofile="current",
            n=2,
            lineterm="",
        )
    )
    if len(diff) > _MAX_DIFF_LINES:
        omitted = len(diff) - _MAX_DIFF_LINES
        diff = [*diff[:_MAX_DIFF_LINES], f"... ({omitted} more diff lines)"]
    return "\n".join(f"    {line}" for line in diff)


def _fail_on(problems: list[str], summary: str) -> None:
    """Fail once with every compatibility problem, each with its own diff."""
    if problems:
        pytest.fail(f"{summary} ({len(problems)}):\n\n" + "\n\n".join(problems), pytrace=False)


def _read_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _write_snapshot(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _without_added_optional_properties(
    baseline: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Return ``current`` without top-level properties that are new and not required."""
    added = set(current.get("properties", {})) - set(baseline.get("properties", {}))
    optional = added - set(current.get("required", []))
    if not optional:
        return current
    return {
        **current,
        "properties": {
            key: value for key, value in current["properties"].items() if key not in optional
        },
    }


@pytest.fixture
def openapi_baseline() -> dict[str, Any]:
    return _read_json(_OPENAPI_BASELINE)


@pytest.fixture
def current_openapi() -> dict[str, Any]:
    """Generate the Temporal API spec from the app, bypassing FastAPI's schema cache.

    ``scripts/generate_openapi.py`` calls the same ``app.openapi()`` (which installs
    the bearer-auth security policy) and only adds a display title in ``info`` before
    writing sorted JSON; neither affects the sections compared here.
    """
    cached = app.openapi_schema
    app.openapi_schema = None
    try:
        spec = app.openapi()
    finally:
        app.openapi_schema = cached
    return _normalize(spec)


def test_baseline_operations_are_unchanged(
    openapi_baseline: dict[str, Any], current_openapi: dict[str, Any]
) -> None:
    """Every baseline path and method still exists with an identical operation object."""
    current_paths: dict[str, Any] = current_openapi.get("paths", {})
    problems: list[str] = []

    for path, baseline_item in openapi_baseline["paths"].items():
        current_item = current_paths.get(path)
        if current_item is None:
            problems.append(f"{path}: path removed (had {sorted(baseline_item)})")
            continue
        for key, baseline_value in baseline_item.items():
            label = f"{key.upper()} {path}" if key in _HTTP_METHODS else f"{path} [{key}]"
            if key not in current_item:
                problems.append(f"{label}: removed")
            elif current_item[key] != baseline_value:
                problems.append(
                    f"{label}: changed\n{_json_diff(baseline_value, current_item[key])}"
                )

    _fail_on(problems, "Existing Temporal API operations changed")


def test_baseline_component_schemas_are_unchanged(
    openapi_baseline: dict[str, Any], current_openapi: dict[str, Any]
) -> None:
    """Every baseline ``components.schemas`` entry still exists unchanged."""
    current_schemas: dict[str, Any] = current_openapi.get("components", {}).get("schemas", {})
    problems: list[str] = []

    for name, baseline_schema in openapi_baseline["components"]["schemas"].items():
        if name not in current_schemas:
            problems.append(f"components.schemas.{name}: removed")
            continue
        current_schema = _without_added_optional_properties(baseline_schema, current_schemas[name])
        if current_schema != baseline_schema:
            problems.append(
                f"components.schemas.{name}: changed\n{_json_diff(baseline_schema, current_schema)}"
            )

    _fail_on(problems, "Existing Temporal API component schemas changed")


def test_security_schemes_are_unchanged(
    openapi_baseline: dict[str, Any], current_openapi: dict[str, Any]
) -> None:
    """The authentication schemes advertised to generated clients are unchanged."""
    baseline_schemes = openapi_baseline["components"]["securitySchemes"]
    current_schemes = current_openapi.get("components", {}).get("securitySchemes")

    if current_schemes != baseline_schemes:
        diff = _json_diff(baseline_schemes, current_schemes)
        _fail_on(
            [f"components.securitySchemes: changed\n{diff}"],
            "Temporal API security schemes changed",
        )


@pytest.fixture
def deterministic_rbac(mocker: MockerFixture) -> None:
    """Give every workflow fixed roles so the captured response does not depend on rbac.yaml."""
    rbac = MagicMock()
    rbac.get_workflow_roles.side_effect = lambda workflow_name: {
        "read_roles": {"reader", workflow_name},
        "execute_roles": {"executor", workflow_name},
    }
    mocker.patch.object(dynamic_endpoints, "RBACConfig", return_value=rbac)


@pytest.mark.usefixtures("deterministic_rbac")
def test_workflow_metadata_is_a_superset_of_the_baseline() -> None:
    """Every baseline ``/metadata`` workflow keeps every key and value it had."""
    rsp = TestClient(app).get("/v1/workflow/metadata")
    assert rsp.status_code == 200
    current: dict[str, Any] = rsp.json()

    if _UPDATE_SNAPSHOTS:
        _write_snapshot(_METADATA_BASELINE, current)
    if not _METADATA_BASELINE.exists():
        pytest.fail(
            f"Missing {_METADATA_BASELINE.name}; capture it with NVCM_UPDATE_SNAPSHOTS=1",
            pytrace=False,
        )
    baseline = _read_json(_METADATA_BASELINE)

    problems = [f"response.{key}: removed" for key in baseline if key not in current]
    current_by_name = {entry["name"]: entry for entry in current.get("workflows", [])}
    for baseline_entry in baseline["workflows"]:
        name = baseline_entry["name"]
        current_entry = current_by_name.get(name)
        if current_entry is None:
            problems.append(f"{name}: workflow removed from /metadata")
            continue
        for key, baseline_value in baseline_entry.items():
            if key not in current_entry:
                problems.append(f"{name}.{key}: removed (was {baseline_value!r})")
            elif current_entry[key] != baseline_value:
                problems.append(
                    f"{name}.{key}: changed\n{_json_diff(baseline_value, current_entry[key])}"
                )

    _fail_on(problems, "/v1/workflow/metadata is no longer a superset of the baseline")
