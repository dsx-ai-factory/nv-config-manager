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
"""Form-catalog checks of declarations and the v1 ``/form`` envelope."""

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel

from nv_config_manager_workflows.ui import (
    Dependency,
    FormOptionSource,
    OptionSource,
    ServerOwned,
    WorkflowFormContractError,
    api_options,
    build_form,
    device_field,
    location_field,
    wire_schema,
)

LOCATION = OptionSource("/v1/parameter/location", "name", "id", type_key="location_type")
DEVICES = OptionSource("/v1/parameter/device", "name", "id", params={"managed_only": True})
OVERLAYS = OptionSource(
    "/v1/parameter/overlay",
    "name",
    "name",
    depends_on={
        "location": Dependency("site"),
        "location_type": Dependency("site_type", required=False),
    },
    clear_on_change=True,
)
LOCATION_FIELD = location_field(LOCATION, type_field="site_type")


class ExampleInput(BaseModel):
    site: str
    site_type: str | None = None
    device_id: str
    devices: list[str] = []
    tenant_name: str | None = None
    overlay: str | None = None
    count: int = 1
    user: Annotated[str, ServerOwned()] = ""


def _model(ui: Any) -> type[BaseModel]:
    class Model(ExampleInput):
        rjsf_ui_schema: ClassVar[Any] = ui

    return Model


def _build(ui: Any, **kwargs: Any) -> dict[str, Any]:
    return build_form(_model(ui), **kwargs)


def test_a_full_declaration_builds_the_v1_envelope() -> None:
    envelope = _build(
        {
            "ui:order": ["site", "overlay", "device_id", "*"],
            "ui:submitButtonOptions": {"submitText": "Run"},
            "ui:globalOptions": {"hideSchemaDescriptions": True},
            "site": {**LOCATION_FIELD, "ui:title": "Site"},
            "site_type": {"ui:widget": "hidden"},
            "overlay": api_options(OVERLAYS),
            "device_id": device_field(DEVICES, filters=("site", "tenant"), site_field="site"),
            "devices": device_field(
                DEVICES, filters=("status",), filter_scope="fabric", query_param=None
            ),
            "count": {"ui:help": "How many.", "ui:options": {"rows": 2}},
        }
    )

    assert envelope["requires"] == [
        "core-field.api-options.v1",
        "core-field.device.v1",
        "core-field.location.v1",
        "theme.hide-schema-descriptions.v1",
    ]
    assert envelope["ui_schema"]["device_id"]["ui:options"] == {
        "source": {
            "endpoint": "/v1/parameter/device",
            "label_key": "name",
            "value_key": "id",
            "params": {"managed_only": True},
        },
        "filters": ["site", "tenant"],
        "filterSources": {
            "site": {
                "endpoint": "/v1/parameter/location",
                "label_key": "name",
                "value_key": "id",
                "type_key": "location_type",
                "params": {"location_type": ["Site", "Module"]},
            },
            "tenant": {
                "endpoint": "/v1/parameter/tenant",
                "label_key": "name",
                "value_key": "name",
                "params": {"managed_only": True},
            },
        },
        "siteRequired": True,
        "siteField": "site",
        "queryParam": "device-id",
        "filterScope": "implicit:device_id",
    }
    assert envelope["ui_schema"]["devices"]["ui:options"]["filterScope"] == "fabric"
    assert "queryParam" not in envelope["ui_schema"]["devices"]["ui:options"]
    assert envelope["ui_schema_version"] == 1
    assert "user" not in envelope["schema"]["properties"]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_selected_option_metadata_dependency_adds_its_capability() -> None:
    devices = OptionSource(
        "/v1/parameter/device",
        "name",
        "id",
        type_key="platform",
    )
    commands = OptionSource(
        "/v1/commands",
        "name",
        "name",
        depends_on={"platform": Dependency("device_id", option_key="platform")},
    )

    envelope = _build(
        {
            "device_id": device_field(devices),
            "overlay": api_options(commands),
        }
    )

    assert envelope["ui_schema"]["overlay"]["ui:options"]["source"]["depends_on"] == {
        "platform": {"field": "device_id", "option_key": "platform"}
    }
    assert "option-source.selected-option.v1" in envelope["requires"]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_help_variant_requires_the_matching_ui_capability() -> None:
    envelope = _build(
        {
            "site": {
                "ui:help": "This operation is destructive.",
                "ui:helpVariant": "destructive",
            }
        }
    )

    assert envelope["requires"] == ["theme.help-variant.v1"]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_an_undeclared_form_is_a_bare_projection() -> None:
    envelope = build_form(ExampleInput)

    assert envelope["ui_schema"] == {}
    assert envelope["requires"] == []
    assert list(envelope["schema"]["properties"]) == [
        "site",
        "site_type",
        "device_id",
        "devices",
        "tenant_name",
        "overlay",
        "count",
    ]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_ui_schema_declared_as_a_model_field_is_rejected() -> None:
    class MisdeclaredInput(BaseModel):
        rjsf_ui_schema: Mapping[str, object] = {"name": {"ui:title": "Name"}}

        name: str

    with pytest.raises(
        WorkflowFormContractError,
        match=(
            r"MisdeclaredInput\.rjsf_ui_schema is a Pydantic model field; declare form "
            r"metadata as rjsf_ui_schema: ClassVar\[Mapping\[str, object\]\] instead"
        ),
    ):
        build_form(MisdeclaredInput)


def test_ui_schema_declaration_must_be_a_mapping() -> None:
    with pytest.raises(WorkflowFormContractError, match="must be a mapping"):
        _build([])


def test_api_options_can_disable_the_picker_when_no_matches_are_returned() -> None:
    field = api_options(
        OptionSource("/v1/options", "label", "value", response="options-v1"),
        disable_when_no_matches=True,
    )

    envelope = _build({"overlay": field})

    assert envelope["ui_schema"]["overlay"]["ui:options"]["disableWhenNoMatches"] is True
    Draft202012Validator(wire_schema()).validate(envelope)


def test_a_symbolic_option_source_requires_workflow_provider_context() -> None:
    source = FormOptionSource("fabric-profiles")

    with pytest.raises(WorkflowFormContractError, match="unresolved FormOptionSource"):
        _build({"overlay": api_options(source)})


def test_a_symbolic_option_source_is_compiled_before_form_validation() -> None:
    source = FormOptionSource(
        "fabric-profiles",
        params={"kind": "production"},
        depends_on={"site": Dependency("site")},
        clear_on_change=True,
    )

    envelope = _build(
        {"overlay": api_options(source)},
        compile_option_source=lambda value: OptionSource(
            f"/v1/workflow/Example/form-options/{value.name}",
            "label",
            "value",
            params=value.params,
            depends_on=value.depends_on,
            clear_on_change=value.clear_on_change,
            response="options-v1",
        ),
    )

    assert envelope["ui_schema"]["overlay"]["ui:options"]["source"] == {
        "endpoint": "/v1/workflow/Example/form-options/fabric-profiles",
        "label_key": "label",
        "value_key": "value",
        "params": {"kind": "production"},
        "depends_on": {"site": {"field": "site"}},
        "clear_on_change": True,
        "response": "options-v1",
    }
    assert envelope["requires"] == [
        "core-field.api-options.enriched.v1",
        "core-field.api-options.v1",
    ]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_exclusive_groups_resolve_device_filter_owners_and_require_capability() -> None:
    envelope = _build(
        {
            "ui:globalOptions": {
                "exclusiveGroups": [
                    {"fields": ["device_id"], "deviceFilters": ["device_id"]},
                    {"fields": ["overlay"]},
                ]
            },
            "device_id": device_field(DEVICES, filters=("status",)),
        }
    )

    assert envelope["requires"] == [
        "core-field.device.v1",
        "interaction.exclusive-groups.v1",
    ]
    assert envelope["ui_schema"]["device_id"]["ui:options"]["filterScope"] == ("implicit:device_id")
    Draft202012Validator(wire_schema()).validate(envelope)


def test_numeric_field_comparison_requires_capability() -> None:
    class NumericInput(BaseModel):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "ui:globalOptions": {
                "fieldComparisons": [
                    {
                        "left": "minimum",
                        "operator": "lessThan",
                        "right": "maximum",
                        "message": "Minimum must be less than maximum",
                    }
                ]
            }
        }

        minimum: int
        maximum: int

    envelope = build_form(NumericInput)

    assert envelope["requires"] == ["validation.field-comparison.v1"]
    Draft202012Validator(wire_schema()).validate(envelope)


def test_a_workflow_without_input_has_an_empty_form() -> None:
    assert build_form(None) == {
        "schema": {},
        "ui_schema": {},
        "ui_schema_version": 1,
        "requires": [],
    }
