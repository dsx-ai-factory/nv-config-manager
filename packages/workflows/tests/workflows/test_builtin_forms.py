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
"""The v1 forms of the built-in API workflows.

The full envelopes are snapshotted in
``src/tests/temporal/api/fixtures/workflow_forms.json``.
"""

from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME, builtin_plugin
from nv_config_manager_workflows.registration.form_catalog import WorkflowFormCatalog
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.ui import QUERY_ALIASES, QUERY_SEPARATORS, wire_schema
from nv_config_manager_workflows.workflows.backup import BackupInput, BackupWorkflow, TriggerEnum
from nv_config_manager_workflows.workflows.ib_pkey_creation import IBPKeyCreationWorkflow
from nv_config_manager_workflows.workflows.ib_port_guid_discovery import (
    IBPortGuidDiscoveryWorkflow,
)
from nv_config_manager_workflows.workflows.infiniband_cable_validation import (
    InfinibandCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.lldp import PortLLDPInfoWorkflow
from nv_config_manager_workflows.workflows.spx_overlay import SpXOverlayTenantChangeWorkflow


@pytest.fixture(scope="module")
def registry() -> WorkflowRegistry:
    return WorkflowRegistry.build({BUILTIN_PLUGIN_NAME: builtin_plugin()})


@pytest.fixture(scope="module")
def form_catalog(registry: WorkflowRegistry) -> WorkflowFormCatalog:
    return WorkflowFormCatalog.build(registry)


def test_every_form_enabled_builtin_api_workflow_has_a_valid_form(
    registry: WorkflowRegistry, form_catalog: WorkflowFormCatalog
) -> None:
    validator = Draft202012Validator(wire_schema())
    form_enabled = {
        workflow
        for workflow in registry.api_workflows
        if workflow.get_workflow_form_enabled()
    }

    assert form_catalog.diagnostics == {}
    assert set(form_catalog.forms) == form_enabled
    for workflow, envelope in form_catalog.forms.items():
        errors = [error.message for error in validator.iter_errors(envelope)]
        assert errors == [], workflow.__name__


def test_every_shipped_query_alias_reaches_a_core_field(
    form_catalog: WorkflowFormCatalog,
) -> None:
    """Each alias names a core field of a built-in form, which serves it as ``queryAliases``."""
    served = {
        (f"{model.__module__}.{model.__qualname__}", name): tuple(
            entry["ui:options"].get("queryAliases", ())
        )
        for workflow, envelope in form_catalog.forms.items()
        if (model := workflow.get_workflow_input_class()) is not None
        for name, entry in envelope["ui_schema"].items()
        if not name.startswith("ui:") and "ui:field" in entry
    }

    assert {key: served.get(key) for key in QUERY_ALIASES} == dict(QUERY_ALIASES)


def test_every_shipped_query_separator_reaches_a_core_field(
    form_catalog: WorkflowFormCatalog,
) -> None:
    served = {
        (f"{model.__module__}.{model.__qualname__}", name): entry["ui:options"].get(
            "querySeparator"
        )
        for workflow, envelope in form_catalog.forms.items()
        if (model := workflow.get_workflow_input_class()) is not None
        for name, entry in envelope["ui_schema"].items()
        if not name.startswith("ui:") and "ui:field" in entry
    }

    assert {key: served.get(key) for key in QUERY_SEPARATORS} == dict(QUERY_SEPARATORS)


def test_backup_sends_a_hidden_api_trigger_and_picks_a_filtered_device(
    form_catalog: WorkflowFormCatalog,
) -> None:
    form = form_catalog.forms[BackupWorkflow]

    assert list(form["schema"]["properties"]) == ["device_id", "trigger"]
    assert form["schema"]["required"] == ["device_id", "trigger"]
    assert form["schema"]["properties"]["trigger"]["default"] == TriggerEnum.API.value
    assert form["ui_schema"]["trigger"] == {"ui:widget": "hidden"}
    device: dict[str, Any] = form["ui_schema"]["device_id"]
    assert device["ui:field"] == "device"
    assert device["ui:options"]["filters"] == ["site", "tenant", "status"]
    assert device["ui:options"]["queryParam"] == "device-id"
    assert device["ui:options"]["filterScope"] == "implicit:device_id"
    assert form["requires"] == ["core-field.device.v1"]
    # The API contract is unchanged: trigger stays required and has no default.
    assert BackupInput.model_json_schema()["required"] == ["device_id", "trigger"]


def test_spx_tenant_change_drives_the_device_from_its_site_field(
    form_catalog: WorkflowFormCatalog,
) -> None:
    form = form_catalog.forms[SpXOverlayTenantChangeWorkflow]
    ui_schema = form["ui_schema"]

    assert "namespace_tag" not in form["schema"]["properties"]
    assert ui_schema["ui:order"] == ["site", "overlay_id", "device_id", "port_names", "*"]
    assert ui_schema["site"]["ui:field"] == "location"
    assert ui_schema["site"]["ui:options"]["typeField"] == "site_type"
    assert ui_schema["site_type"] == {"ui:widget": "hidden"}
    assert ui_schema["device_id"]["ui:options"]["siteField"] == "site"
    assert ui_schema["device_id"]["ui:options"]["filters"] == ["site"]
    assert ui_schema["overlay_id"]["ui:options"]["source"]["depends_on"] == {
        "location": {"field": "site"},
        "location_type": {"field": "site_type", "required": False},
    }
    assert ui_schema["port_names"]["ui:options"]["source"]["endpoint"] == (
        "/v1/parameter/device/{device_id}/interfaces"
    )
    assert ui_schema["port_names"]["ui:options"]["querySeparator"] == ","
    assert form["requires"] == [
        "core-field.api-options.v1",
        "core-field.device.v1",
        "core-field.location.v1",
        "prefill.query-separator.v1",
    ]


@pytest.mark.parametrize(
    ("workflow", "query_params"),
    [
        (IBPortGuidDiscoveryWorkflow, {"ufm_device_id": None, "switch_device_ids": None}),
        (
            InfinibandCableValidationWorkflow,
            {"ufm_device_id": "ufm_device_id", "switch_device_ids": "device-id"},
        ),
    ],
)
def test_fabric_forms_share_one_site_filter_across_a_scalar_and_a_list_device(
    form_catalog: WorkflowFormCatalog,
    workflow: type[Any],
    query_params: dict[str, str | None],
) -> None:
    form = form_catalog.forms[workflow]
    properties = form["schema"]["properties"]

    assert properties["ufm_device_id"]["type"] == "string"
    assert properties["switch_device_ids"]["type"] == "array"
    assert properties["switch_device_ids"]["minItems"] == 1
    for name, query_param in query_params.items():
        options = form["ui_schema"][name]["ui:options"]
        assert options["filterScope"] == "fabric-devices"
        assert options["filters"] == ["site"]
        assert options.get("queryParam") == query_param
    # Each field keeps its own static device parameters inside the shared scope.
    sources = {name: form["ui_schema"][name]["ui:options"]["source"] for name in query_params}
    assert sources["ufm_device_id"]["params"] != sources["switch_device_ids"]["params"]


def test_lldp_requires_one_complete_lookup_mode(form_catalog: WorkflowFormCatalog) -> None:
    form = form_catalog.forms[PortLLDPInfoWorkflow]
    options = form["ui_schema"]["device_id"]["ui:options"]

    assert options["filters"] == ["site"]
    assert options["siteRequired"] is True
    assert "required" not in form["schema"]
    assert form["ui_schema"]["ui:globalOptions"]["exclusiveGroups"] == [
        {
            "fields": ["device_id", "interface"],
            "deviceFilters": ["device_id"],
            "requireComplete": True,
        },
        {"fields": ["remote_mac_address"], "requireComplete": True},
    ]
    assert form["requires"] == [
        "core-field.device.v1",
        "interaction.exclusive-groups.v1",
    ]


def test_ib_pkey_creation_sends_only_host_and_pkey(
    form_catalog: WorkflowFormCatalog,
) -> None:
    form = form_catalog.forms[IBPKeyCreationWorkflow]

    assert list(form["schema"]["properties"]) == ["host", "pkey"]
    assert "pattern" not in form["schema"]["properties"]["pkey"]
    assert form["ui_schema"]["ui:submitButtonOptions"] == {"submitText": "Create PKey"}
    assert form["requires"] == ["theme.hide-schema-descriptions.v1"]
