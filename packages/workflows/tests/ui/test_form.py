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
"""Registration checks of ``rjsf_ui_schema`` declarations and the v1 ``/form`` envelope."""

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from nv_config_manager_workflows.ui import (
    Dependency,
    OptionSource,
    ServerOwned,
    WorkflowFormContractError,
    api_options,
    build_form,
    device_field,
    location_field,
    supported_capabilities,
    wire_schema,
)
from nv_config_manager_workflows.ui import form as form_module

_PACKAGE_UI = Path(form_module.__file__).parent
_REPO_UI_LIB = Path(__file__).resolve().parents[4] / "ui" / "src" / "lib"

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
TENANTS = OptionSource("/v1/parameter/tenant", "name", "name")
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


def test_api_options_can_disable_the_picker_when_no_matches_are_returned() -> None:
    field = api_options(
        OptionSource("/v1/options", "label", "value", response="options-v1"),
        disable_when_no_matches=True,
    )

    envelope = _build({"overlay": field})

    assert envelope["ui_schema"]["overlay"]["ui:options"]["disableWhenNoMatches"] is True
    Draft202012Validator(wire_schema()).validate(envelope)


def test_api_options_accepts_a_referenced_array_of_strings() -> None:
    type StringList = list[str]

    class ReferencedInput(BaseModel):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"values": api_options(TENANTS)}

        values: StringList

    envelope = build_form(ReferencedInput)

    assert envelope["schema"]["properties"]["values"]["$ref"] == "#/$defs/StringList"


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


def test_the_server_adds_shipped_query_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _model({"overlay": api_options(OVERLAYS), "site": LOCATION_FIELD})
    key = (f"{model.__module__}.{model.__qualname__}", "overlay")
    monkeypatch.setitem(form_module.QUERY_ALIASES, key, ("overlay_id",))

    envelope = build_form(model)

    assert envelope["ui_schema"]["overlay"]["ui:options"]["queryAliases"] == ["overlay_id"]


def test_the_server_adds_a_shipped_multi_value_query_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model({"devices": api_options(TENANTS)})
    key = (f"{model.__module__}.{model.__qualname__}", "devices")
    monkeypatch.setitem(form_module.QUERY_SEPARATORS, key, ",")

    envelope = build_form(model)

    assert envelope["ui_schema"]["devices"]["ui:options"]["querySeparator"] == ","
    assert envelope["requires"] == [
        "core-field.api-options.v1",
        "prefill.query-separator.v1",
    ]


def test_repeated_parameters_of_one_owner_are_allowed() -> None:
    shared = device_field(DEVICES, filters=("tenant", "status"), filter_scope="fabric")
    _build(
        {
            "device_id": shared,
            "devices": {**shared, "ui:options": {**shared["ui:options"], "queryParam": "devices"}},
        }
    )


_SHARED = device_field(DEVICES, filters=("tenant",), filter_scope="shared", query_param=None)


@pytest.mark.parametrize(
    ("ui", "message"),
    [
        ([], "must be a mapping"),
        ({"ui:theme": {}}, "unsupported root key 'ui:theme'"),
        ({"nope": {}}, "'nope', which is not a property"),
        ({"user": {"ui:title": "User"}}, "ServerOwned or FormExcluded"),
        ({"site": "text"}, "must be a mapping"),
        ({"site": {"ui:label": "Site"}}, "unsupported keys ['ui:label']"),
        ({"site": {"ui:title": " "}}, "must be a non-empty string"),
        ({"site": {"ui:widget": "radio"}}, "ui:widget 'radio' is not one of"),
        ({"site": {"ui:field": "tags", "ui:options": {}}}, "ui:field 'tags' is not one of"),
        ({"site": {**LOCATION_FIELD, "ui:widget": "text"}}, "sets both ui:widget and ui:field"),
        ({"site": {"ui:readonly": "yes"}}, "ui:readonly must be a boolean"),
        ({"count": {"ui:options": {"rows": 0}}}, "rows must be a positive integer"),
        ({"count": {"ui:options": {"source": {}}}}, "are not supported on a standard field"),
        ({"site": {"ui:field": "location"}}, "requires ui:options with a source"),
        ({"ui:order": ["site", "nope", "*"]}, "ui:order names 'nope'"),
        ({"ui:order": ["site", "site", "*"]}, "unique names"),
        ({"ui:order": ["site"]}, "has no '*'"),
        ({"ui:order": ["user", "*"]}, "ServerOwned or FormExcluded"),
        ({"ui:submitButtonOptions": {"label": "Go"}}, "ui:submitButtonOptions must be a mapping"),
        ({"ui:globalOptions": {"hideSchemaDescriptions": 1}}, "must be a boolean"),
        (
            {
                "ui:globalOptions": {
                    "exclusiveGroups": [
                        {"fields": ["site"], "deviceFilters": ["site"]},
                        {"fields": ["overlay"]},
                    ]
                }
            },
            "deviceFilters entry 'site' must be a device field",
        ),
        (
            {
                "ui:globalOptions": {
                    "fieldComparisons": [
                        {
                            "left": "count",
                            "operator": "lessThan",
                            "right": "site",
                            "message": "Count must be less than Site",
                        }
                    ]
                }
            },
            "right 'site' must be numeric",
        ),
        (
            {
                "overlay": {
                    **api_options(OVERLAYS),
                    "ui:options": {"source": {**OVERLAYS.to_wire(), "endpoint": "/v1/x?y=1"}},
                }
            },
            "must not contain '?'",
        ),
        (
            {
                "overlay": api_options(
                    OptionSource("/v1/x", "name", "name", depends_on={"q": Dependency("nope")})
                )
            },
            "dependency names 'nope'",
        ),
        (
            {
                "overlay": api_options(
                    OptionSource("/v1/x", "name", "name", depends_on={"q": Dependency("user")})
                )
            },
            "ServerOwned or FormExcluded",
        ),
        (
            {"overlay": api_options(OptionSource("/v1/{nope}/x", "name", "name"))},
            "endpoint placeholder names 'nope'",
        ),
        (
            {
                "overlay": {
                    **api_options(OVERLAYS),
                    "ui:options": {"source": {**OVERLAYS.to_wire(), "clear_on_change": False}},
                }
            },
            "clear_on_change must be true",
        ),
        (
            {
                "overlay": {
                    **api_options(
                        OptionSource("/v1/options", "label", "value", response="options-v1")
                    ),
                    "ui:options": {
                        "source": OptionSource(
                            "/v1/options", "label", "value", response="options-v1"
                        ).to_wire(),
                        "disableWhenNoMatches": False,
                    },
                }
            },
            "disableWhenNoMatches must be true",
        ),
        (
            {"overlay": api_options(OptionSource("/v1/{overlay}", "name", "name"))},
            "cycle: overlay -> overlay",
        ),
        (
            {
                "overlay": api_options(
                    OptionSource(
                        "/v1/x", "name", "name", depends_on={"t": Dependency("tenant_name")}
                    )
                ),
                "tenant_name": api_options(OptionSource("/v1/{overlay}", "name", "name")),
            },
            "cycle",
        ),
        ({"overlay": api_options(LOCATION)}, "source keys ['type_key'] are not allowed"),
        (
            {
                "overlay": {
                    **api_options(TENANTS),
                    "ui:options": {"source": TENANTS.to_wire(), "typeField": "site_type"},
                }
            },
            "['typeField'] are not supported on a apiOptions field",
        ),
        (
            {
                "overlay": {
                    **api_options(TENANTS),
                    "ui:options": {"source": TENANTS.to_wire(), "queryAliases": ["o"]},
                }
            },
            "added by the server",
        ),
        (
            {
                "devices": {
                    **api_options(TENANTS),
                    "ui:options": {
                        "source": TENANTS.to_wire(),
                        "querySeparator": ",",
                    },
                }
            },
            "added by the server",
        ),
        ({"count": api_options(TENANTS)}, "must be a string or string list"),
        ({"devices": location_field(LOCATION)}, "location field, so it must be a string"),
        ({"site": location_field(LOCATION, type_field="nope")}, "typeField names 'nope'"),
        ({"site": location_field(LOCATION, type_field="count")}, "must be another string property"),
        ({"site": location_field(LOCATION, type_field="site")}, "must be another string property"),
        ({"site": location_field(TENANTS, type_field="site_type")}, "its source needs type_key"),
        ({"count": device_field(DEVICES)}, "must be a string or string list"),
        ({"device_id": device_field(OVERLAYS)}, "source keys ['depends_on'] are not allowed"),
        (
            {"device_id": device_field(DEVICES, filters=cast(Any, ("site", "rack")))},
            "device filters must be unique",
        ),
        (
            {
                "device_id": {
                    "ui:field": "device",
                    "ui:options": {"source": DEVICES.to_wire(), "filters": []},
                }
            },
            "siteRequired must be a boolean",
        ),
        (
            {
                "site": LOCATION_FIELD,
                "device_id": device_field(DEVICES, filters=("tenant",), site_field="site"),
            },
            "filters must include 'site'",
        ),
        (
            {"device_id": device_field(DEVICES, filters=("site",), site_field="tenant_name")},
            "must name a location field",
        ),
        (
            {
                "site": location_field(LOCATION),
                "device_id": device_field(DEVICES, filters=("site",), site_field="site"),
            },
            "declares a typeField",
        ),
        ({"device_id": device_field(DEVICES, filter_scope="implicit:devices")}, "reserved prefix"),
        (
            {"device_id": {**device_field(DEVICES, filter_scope="shared"), "ui:readonly": True}},
            "cannot be read-only",
        ),
        (
            {
                "device_id": _SHARED,
                "devices": device_field(
                    DEVICES, filters=("status",), filter_scope="shared", query_param=None
                ),
            },
            "share filterScope 'shared' but differ",
        ),
        (
            {
                "site": LOCATION_FIELD,
                "device_id": device_field(
                    DEVICES, filters=("site", "tenant"), site_field="site", filter_scope="shared"
                ),
                "devices": device_field(
                    DEVICES, filters=("site", "tenant"), filter_scope="shared", query_param=None
                ),
            },
            "share filterScope 'shared' but differ",
        ),
        (
            {"device_id": {"ui:widget": "hidden"}},
            "neither a location typeField nor a property with a projected default",
        ),
        (
            {
                "device_id": device_field(DEVICES, filters=("tenant",), filter_scope="a"),
                "devices": device_field(
                    DEVICES, filters=("tenant",), filter_scope="b", query_param=None
                ),
            },
            "'tenant' has more than one prefill owner: scope:a, scope:b",
        ),
        (
            {"site": LOCATION_FIELD, "device_id": device_field(DEVICES, filters=("site",))},
            "'site' has more than one prefill owner: field:site, scope:implicit:device_id",
        ),
        (
            {"device_id": device_field(DEVICES, query_param="overlay")},
            "'overlay' has more than one prefill owner: field:device_id, field:overlay",
        ),
    ],
)
def test_invalid_declarations_are_rejected(ui: Any, message: str) -> None:
    with pytest.raises(WorkflowFormContractError) as raised:
        _build(ui)

    assert message in str(raised.value)


def test_a_property_name_outside_the_wire_pattern_cannot_be_declared() -> None:
    class Aliased(BaseModel):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"my-a": {"ui:title": "A"}}

        a: str = Field(alias="my-a")

    with pytest.raises(WorkflowFormContractError, match="does not match the v1 property name"):
        build_form(Aliased)


def test_a_standard_tenant_field_and_a_tenant_filter_collide() -> None:
    class WithTenant(ExampleInput):
        tenant: str | None = None
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "device_id": device_field(DEVICES, filters=("tenant",))
        }

    with pytest.raises(WorkflowFormContractError) as raised:
        build_form(WithTenant)

    assert "'tenant' has more than one prefill owner: field:tenant, scope:implicit:device_id" in (
        str(raised.value)
    )


def test_an_alias_colliding_with_another_property_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model({"overlay": api_options(OVERLAYS), "site": LOCATION_FIELD})
    key = (f"{model.__module__}.{model.__qualname__}", "overlay")
    monkeypatch.setitem(form_module.QUERY_ALIASES, key, ("tenant_name",))

    with pytest.raises(
        WorkflowFormContractError, match="'tenant_name' has more than one prefill owner"
    ):
        build_form(model)


def test_shipped_aliases_on_a_device_need_a_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _model({"device_id": device_field(DEVICES, query_param=None)})
    key = (f"{model.__module__}.{model.__qualname__}", "device_id")
    monkeypatch.setitem(form_module.QUERY_ALIASES, key, ("device",))

    with pytest.raises(WorkflowFormContractError, match="no queryParam"):
        build_form(model)


def test_hidden_properties_and_marked_fields_cannot_be_prefilled() -> None:
    """A hidden typeField is no URL owner, so a scope may own its spelling."""

    class Typed(ExampleInput):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "site": LOCATION_FIELD,
            "site_type": {"ui:widget": "hidden"},
            "device_id": device_field(DEVICES, filters=("site",), site_field="site"),
        }

    build_form(Typed)


def test_derivable_capabilities_are_supported_by_the_manifest() -> None:
    derivable = {
        *form_module.FIELD_CAPABILITIES.values(),
        form_module.EXCLUSIVE_GROUPS_CAPABILITY,
        form_module.FIELD_COMPARISON_CAPABILITY,
        form_module.HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY,
        form_module.QUERY_SEPARATOR_CAPABILITY,
    }

    assert derivable <= supported_capabilities()
    assert set(form_module.FIELD_CAPABILITIES) == set(
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


@pytest.mark.skipif(not _REPO_UI_LIB.is_dir(), reason="the repository ui/ directory is absent")
@pytest.mark.parametrize(
    "name", ["workflow-form-v1.schema.json", "workflow-form-v1.capabilities.json"]
)
def test_the_ui_copies_of_the_contract_are_byte_identical(name: str) -> None:
    assert (_REPO_UI_LIB / name).read_bytes() == (_PACKAGE_UI / name).read_bytes()
