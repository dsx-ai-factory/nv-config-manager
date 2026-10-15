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
"""Canonical workflow-form contract resource and capability checks."""

import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from nv_config_manager_workflows.ui.form import contract as form_contract_module
from nv_config_manager_workflows.ui.form.contract import (
    UI_SCHEMA_VERSION,
    capability_manifest,
    supported_capabilities,
    wire_schema,
)

_PACKAGE_RESOURCES = Path(form_contract_module.__file__).parent / "resources"
_REPO_UI_LIB = Path(__file__).resolve().parents[4] / "ui" / "src" / "lib"


def test_derivable_capabilities_are_supported_by_the_manifest() -> None:
    derivable = {
        *form_contract_module.FIELD_CAPABILITIES.values(),
        form_contract_module.API_OPTIONS_WAITING_TEXT_CAPABILITY,
        form_contract_module.EXCLUSIVE_GROUPS_CAPABILITY,
        form_contract_module.FIELD_COMPARISON_CAPABILITY,
        form_contract_module.HELP_VARIANT_CAPABILITY,
        form_contract_module.HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY,
        form_contract_module.QUERY_COERCION_CAPABILITY,
        form_contract_module.QUERY_SEPARATOR_CAPABILITY,
    }

    assert derivable <= supported_capabilities()
    assert set(form_contract_module.FIELD_CAPABILITIES) == set(
        wire_schema()["$defs"]["fieldUiSchema"]["properties"]["ui:field"]["enum"]
    )


def test_importing_a_workflow_module_reads_no_contract_resource() -> None:
    """The contract files load on first use, not on every Temporal sandbox re-import."""
    script = "\n".join(
        [
            "import pkgutil",
            "read = []",
            "get_data = pkgutil.get_data",
            "pkgutil.get_data = lambda *args: read.append(args) or get_data(*args)",
            "import nv_config_manager_workflows.workflows.backup",
            "assert not [a for a in read if a[0].startswith('nv_config_manager_workflows')], read",
        ]
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_the_wire_schema_is_a_valid_draft_2020_12_schema() -> None:
    Draft202012Validator.check_schema(wire_schema())


def test_the_v1_contract_artifacts_match_the_backend_version() -> None:
    assert UI_SCHEMA_VERSION == wire_schema()["properties"]["ui_schema_version"]["const"]
    assert UI_SCHEMA_VERSION == capability_manifest()["ui_schema_version"]


@pytest.mark.skipif(not _REPO_UI_LIB.is_dir(), reason="the repository ui/ directory is absent")
@pytest.mark.parametrize(
    "name", ["workflow-form-v1.schema.json", "workflow-form-v1.capabilities.json"]
)
def test_the_ui_copies_of_the_contract_are_byte_identical(name: str) -> None:
    assert (_REPO_UI_LIB / name).read_bytes() == (_PACKAGE_RESOURCES / name).read_bytes()
