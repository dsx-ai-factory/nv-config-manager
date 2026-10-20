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
"""Render workflow RBAC inheritance, explicit revocation, and rollout checksums."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_BASE: dict[str, Any] = {
    "rbac": {
        "admin_roles": ["admins"],
        "workflows": [
            {
                "name": "DiagnosticsWorkflow",
                "read_roles": ["readers"],
                "execute_roles": ["operators"],
            },
            {"name": "BackupWorkflow", "read_roles": ["all"], "execute_roles": ["service"]},
        ],
    }
}


def _render(tmp_path: Path, *layers: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")
    command = [
        "helm",
        "template",
        "test",
        str(_CHART),
        "--values",
        str(_CHART / "values-ci.yaml"),
        "--show-only",
        "templates/temporal.yaml",
    ]
    yaml = YAML(typ="safe", pure=True)
    for index, layer in enumerate(layers):
        path = tmp_path / f"values-{index}.yaml"
        with path.open("w") as stream:
            yaml.dump(layer, stream)
        command.extend(["--values", str(path)])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(result: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    assert result.returncode == 0, result.stderr
    return [doc for doc in YAML(typ="safe", pure=True).load_all(io.StringIO(result.stdout)) if doc]


def _rbac(documents: list[dict[str, Any]]) -> dict[str, Any]:
    configmap = next(
        doc
        for doc in documents
        if doc["kind"] == "ConfigMap" and "rbac.yaml" in doc.get("data", {})
    )
    config = YAML(typ="safe", pure=True).load(configmap["data"]["rbac.yaml"])
    assert isinstance(config, dict)
    return config


def _workflow(config: dict[str, Any], name: str) -> dict[str, Any]:
    return next(workflow for workflow in config["workflows"] if workflow["name"] == name)


@pytest.mark.parametrize("overrides", [{}, {"rbac": {"workflowOverrides": {}}}])
def test_existing_workflow_list_is_unchanged(tmp_path: Path, overrides: dict[str, Any]) -> None:
    assert _rbac(_documents(_render(tmp_path, _BASE, overrides))) == _BASE["rbac"]


def test_cell_workflow_list_still_replaces_the_shared_list(tmp_path: Path) -> None:
    cell = {"rbac": {"workflows": [_BASE["rbac"]["workflows"][1]]}}
    config = _rbac(_documents(_render(tmp_path, _BASE, cell)))
    assert config == {"admin_roles": ["admins"], **cell["rbac"]}


def test_cell_override_inherits_other_roles_and_workflows(tmp_path: Path) -> None:
    result = _render(
        tmp_path,
        _BASE,
        {
            "rbac": {
                "workflowOverrides": {"DiagnosticsWorkflow": {"execute_roles": ["cell-operators"]}}
            }
        },
    )
    config = _rbac(_documents(result))
    assert _workflow(config, "DiagnosticsWorkflow") == {
        "name": "DiagnosticsWorkflow",
        "read_roles": ["readers"],
        "execute_roles": ["cell-operators"],
    }
    assert _workflow(config, "BackupWorkflow") == _BASE["rbac"]["workflows"][1]
    assert config["admin_roles"] == ["admins"]
    assert "workflowOverrides" not in config


def test_override_maps_merge_across_values_files(tmp_path: Path) -> None:
    config = _rbac(
        _documents(
            _render(
                tmp_path,
                _BASE,
                {
                    "rbac": {
                        "workflowOverrides": {
                            "DiagnosticsWorkflow": {"read_roles": ["regional-readers"]},
                            "BackupWorkflow": {"execute_roles": ["regional-service"]},
                        }
                    }
                },
                {
                    "rbac": {
                        "workflowOverrides": {
                            "DiagnosticsWorkflow": {"execute_roles": ["cell-operators"]}
                        }
                    }
                },
            )
        )
    )
    assert _workflow(config, "DiagnosticsWorkflow")["read_roles"] == ["regional-readers"]
    assert _workflow(config, "DiagnosticsWorkflow")["execute_roles"] == ["cell-operators"]
    assert _workflow(config, "BackupWorkflow")["execute_roles"] == ["regional-service"]


@pytest.mark.parametrize("field", ["read_roles", "execute_roles"])
def test_empty_role_list_revokes_instead_of_inheriting(tmp_path: Path, field: str) -> None:
    config = _rbac(
        _documents(
            _render(
                tmp_path,
                _BASE,
                {"rbac": {"workflowOverrides": {"DiagnosticsWorkflow": {field: []}}}},
            )
        )
    )
    assert _workflow(config, "DiagnosticsWorkflow")[field] == []


def test_disabled_workflow_is_omitted_without_changing_others(tmp_path: Path) -> None:
    config = _rbac(
        _documents(
            _render(
                tmp_path,
                _BASE,
                {"rbac": {"workflowOverrides": {"DiagnosticsWorkflow": {"enabled": False}}}},
            )
        )
    )
    assert config == {"admin_roles": ["admins"], "workflows": [_BASE["rbac"]["workflows"][1]]}


def test_new_workflows_have_no_implicit_grants_and_stable_order(tmp_path: Path) -> None:
    config = _rbac(
        _documents(
            _render(
                tmp_path,
                _BASE,
                {
                    "rbac": {
                        "workflowOverrides": {
                            "ZWorkflow": {"execute_roles": ["cell-operators"]},
                            "AWorkflow": {"read_roles": ["cell-readers"]},
                        }
                    }
                },
            )
        )
    )
    assert [w["name"] for w in config["workflows"]] == [
        "DiagnosticsWorkflow",
        "BackupWorkflow",
        "AWorkflow",
        "ZWorkflow",
    ]
    assert _workflow(config, "ZWorkflow")["read_roles"] == []
    assert _workflow(config, "AWorkflow")["execute_roles"] == []


def test_overrides_work_without_a_base_list(tmp_path: Path) -> None:
    config = _rbac(
        _documents(
            _render(
                tmp_path,
                {
                    "rbac": {
                        "workflowOverrides": {
                            "DiagnosticsWorkflow": {"execute_roles": ["operators"]}
                        }
                    }
                },
            )
        )
    )
    assert config == {
        "workflows": [
            {
                "name": "DiagnosticsWorkflow",
                "read_roles": [],
                "execute_roles": ["operators"],
            }
        ]
    }


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ([], "must be a mapping keyed by workflow name"),
        ({"": {}}, "workflow names must not be empty"),
        ({"DiagnosticsWorkflow": []}, "DiagnosticsWorkflow must be a mapping"),
        ({"DiagnosticsWorkflow": {"execute_role": ["operators"]}}, "unsupported field"),
        ({"DiagnosticsWorkflow": {"enabled": "false"}}, "enabled must be a boolean"),
        (
            {"DiagnosticsWorkflow": {"execute_roles": "operators"}},
            "execute_roles must be a list of strings",
        ),
        ({"DiagnosticsWorkflow": {"read_roles": [True]}}, "read_roles must be a list of strings"),
    ],
)
def test_invalid_overrides_fail_rendering(tmp_path: Path, overrides: Any, error: str) -> None:
    result = _render(tmp_path, _BASE, {"rbac": {"workflowOverrides": overrides}})
    assert result.returncode != 0
    assert error in result.stderr


def test_duplicate_base_workflows_are_rejected_when_using_overrides(tmp_path: Path) -> None:
    result = _render(
        tmp_path,
        {
            "rbac": {
                "workflows": [_BASE["rbac"]["workflows"][0], _BASE["rbac"]["workflows"][0]],
                "workflowOverrides": {},
            }
        },
    )
    assert result.returncode != 0
    assert 'contains duplicate workflow "DiagnosticsWorkflow"' in result.stderr


def test_permission_changes_update_api_and_worker_rollout_checksums(tmp_path: Path) -> None:
    before = _documents(_render(tmp_path, _BASE))
    after = _documents(
        _render(
            tmp_path,
            _BASE,
            {"rbac": {"workflowOverrides": {"DiagnosticsWorkflow": {"execute_roles": []}}}},
        )
    )

    def checksums(documents: list[dict[str, Any]]) -> dict[str, str]:
        return {
            doc["metadata"]["name"]: doc["spec"]["template"]["metadata"]["annotations"][
                "checksum/rbac-config"
            ]
            for doc in documents
            if doc["kind"] == "Deployment"
            and "checksum/rbac-config" in doc["spec"]["template"]["metadata"].get("annotations", {})
        }

    original, updated = checksums(before), checksums(after)
    assert len(original) == 2
    assert original.keys() == updated.keys()
    assert len(set(updated.values())) == 1
    assert all(original[name] != updated[name] for name in original)
