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
"""Model-aware semantic validation of workflow-form UI schemas."""

import copy
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, cast

import pytest
from pydantic import BaseModel, Field

from nv_config_manager_workflows.ui import (
    Dependency,
    FormSchema,
    OptionSource,
    ServerOwned,
    WorkflowFormContractError,
    api_options,
    device_field,
    location_field,
    project_form_schema,
)
from nv_config_manager_workflows.ui.form import validation as form_validation_module
from nv_config_manager_workflows.ui.form.validation import validate_ui_schema

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


def _validate(
    ui_schema: dict[str, Any], *, model: type[BaseModel] = ExampleInput
) -> dict[str, Any]:
    validated = copy.deepcopy(ui_schema)
    validate_ui_schema(model, project_form_schema(model), validated)
    return validated


def _validate_declared(model: type[BaseModel]) -> dict[str, Any]:
    return _validate(
        cast(dict[str, Any], getattr(model, "rjsf_ui_schema")),
        model=model,
    )


def test_api_options_accepts_a_referenced_array_of_strings() -> None:
    type StringList = list[str]
    ui_schema = {"values": api_options(TENANTS)}

    class ReferencedInput(BaseModel):
        values: StringList

    schema = project_form_schema(ReferencedInput)
    validate_ui_schema(
        ReferencedInput,
        schema,
        copy.deepcopy(ui_schema),
    )

    assert schema["properties"]["values"]["$ref"] == "#/$defs/StringList"


@pytest.mark.parametrize("waiting_text", ["", " ", cast(Any, 42)])
def test_api_options_waiting_text_must_be_nonblank_text(waiting_text: str) -> None:
    field = api_options(
        OptionSource("/v1/options", "label", "value", response="options-v1"),
        presentation="grouped-checkboxes",
        waiting_text=waiting_text,
    )

    with pytest.raises(WorkflowFormContractError, match="waitingText"):
        _validate({"devices": field})


def test_api_options_waiting_text_requires_grouped_checkboxes() -> None:
    field = api_options(
        OptionSource("/v1/options", "label", "value", response="options-v1"),
        waiting_text="Choose devices first.",
    )

    with pytest.raises(
        WorkflowFormContractError,
        match="waitingText is available only with grouped-checkboxes",
    ):
        _validate({"devices": field})


def test_enum_names_match_the_projected_enum() -> None:
    class Model(BaseModel):
        platform: Annotated[str, FormSchema(default="jira", enum=("jira", "custom"))] = ""

    _validate(
        {"platform": {"ui:widget": "select", "ui:enumNames": ["Jira", "Custom"]}},
        model=Model,
    )

    with pytest.raises(WorkflowFormContractError, match="has 1 labels for 2 enum values"):
        _validate(
            {"platform": {"ui:widget": "select", "ui:enumNames": ["Jira"]}},
            model=Model,
        )


def test_selected_option_dependency_requires_the_referenced_source_type_key() -> None:
    ui_schema = {
        "device_id": device_field(DEVICES),
        "overlay": api_options(
            OptionSource(
                "/v1/options",
                "name",
                "name",
                depends_on={"platform": Dependency("device_id", option_key="platform")},
            )
        ),
    }

    with pytest.raises(WorkflowFormContractError, match="must match 'device_id' source type_key"):
        _validate(ui_schema)


def test_the_server_adds_shipped_query_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _model({"overlay": api_options(OVERLAYS), "site": LOCATION_FIELD})
    key = (f"{model.__module__}.{model.__qualname__}", "overlay")
    monkeypatch.setitem(form_validation_module.QUERY_ALIASES, key, ("overlay_id",))

    ui_schema = _validate_declared(model)

    assert ui_schema["overlay"]["ui:options"]["queryAliases"] == ["overlay_id"]


def test_the_server_adds_a_shipped_multi_value_query_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model({"devices": api_options(TENANTS)})
    key = (f"{model.__module__}.{model.__qualname__}", "devices")
    monkeypatch.setitem(form_validation_module.QUERY_SEPARATORS, key, ",")

    ui_schema = _validate_declared(model)

    assert ui_schema["devices"]["ui:options"]["querySeparator"] == ","


@pytest.mark.parametrize(
    ("field", "coercion"),
    [
        ("count", "integer-prefix-or-default"),
        ("enabled", "case-insensitive-boolean"),
    ],
)
def test_the_server_adds_a_shipped_standard_field_query_coercion(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    coercion: str,
) -> None:
    class Model(BaseModel):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "count": {"ui:title": "Count"},
            "enabled": {"ui:title": "Enabled"},
        }

        count: int = 1
        enabled: bool = True

    key = (f"{Model.__module__}.{Model.__qualname__}", field)
    monkeypatch.setitem(form_validation_module.QUERY_COERCIONS, key, coercion)

    ui_schema = _validate_declared(Model)

    assert ui_schema[field]["ui:options"]["queryCoercion"] == coercion


def test_repeated_parameters_of_one_owner_are_allowed() -> None:
    shared = device_field(DEVICES, filters=("tenant", "status"), filter_scope="fabric")
    _validate(
        {
            "device_id": shared,
            "devices": {**shared, "ui:options": {**shared["ui:options"], "queryParam": "devices"}},
        }
    )


@pytest.mark.parametrize(
    ("filters", "filter_sources", "message"),
    [
        (("site",), {}, "filterSources keys must exactly match filters"),
        (
            ("site",),
            {
                "site": OptionSource(
                    "/v1/parameter/location",
                    "name",
                    "id",
                    params={"location_type": ["Site", "Module"]},
                ).to_wire()
            },
            "Site filter source needs type_key",
        ),
        (
            ("tenant",),
            {"tenant": OptionSource("/v1/{tenant_name}/tenant", "name", "name").to_wire()},
            "filter source endpoint must not depend on form properties",
        ),
        (
            ("tenant",),
            {
                "tenant": OptionSource(
                    "/v1/parameter/tenant",
                    "name",
                    "name",
                    depends_on={"tenant": Dependency("tenant_name")},
                ).to_wire()
            },
            "source keys \\['depends_on'\\] are not allowed",
        ),
    ],
)
def test_invalid_device_filter_sources_are_rejected(
    filters: tuple[Any, ...], filter_sources: dict[str, Any], message: str
) -> None:
    field = device_field(DEVICES, filters=filters)
    field["ui:options"]["filterSources"] = filter_sources

    with pytest.raises(WorkflowFormContractError, match=message):
        _validate({"device_id": field})


def test_device_fields_in_one_scope_must_share_filter_sources() -> None:
    first = device_field(DEVICES, filters=("tenant",), filter_scope="shared")
    second = device_field(DEVICES, filters=("tenant",), filter_scope="shared", query_param=None)
    second["ui:options"]["filterSources"]["tenant"]["endpoint"] = "/v1/other/tenant"

    with pytest.raises(WorkflowFormContractError, match="differ in .*filterSources"):
        _validate({"device_id": first, "devices": second})


_SHARED = device_field(DEVICES, filters=("tenant",), filter_scope="shared", query_param=None)


@pytest.mark.parametrize(
    ("ui", "message"),
    [
        ({"ui:theme": {}}, "unsupported root key 'ui:theme'"),
        ({"nope": {}}, "'nope', which is not a property"),
        ({"user": {"ui:title": "User"}}, "ServerOwned or FormExcluded"),
        ({"site": "text"}, "must be a mapping"),
        ({"site": {"ui:label": "Site"}}, "unsupported keys ['ui:label']"),
        ({"site": {"ui:title": " "}}, "must be a non-empty string"),
        ({"site": {"ui:enumNames": ["Site"]}}, "requires enum values"),
        ({"site": {"ui:enumNames": []}}, "must be a non-empty list"),
        (
            {"site": {"ui:helpVariant": "destructive"}},
            "ui:helpVariant requires ui:help",
        ),
        (
            {"site": {"ui:help": "Help", "ui:helpVariant": "urgent"}},
            "ui:helpVariant 'urgent' is not one of",
        ),
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
                    "exclusiveGroups": [
                        {"fields": ["site"], "requireComplete": True},
                        {"fields": ["overlay"]},
                    ]
                }
            },
            "requireComplete must be enabled on every group or none",
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
        _validate(ui)

    assert message in str(raised.value)


def test_a_property_name_outside_the_wire_pattern_cannot_be_declared() -> None:
    class Aliased(BaseModel):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {"my-a": {"ui:title": "A"}}

        a: str = Field(alias="my-a")

    with pytest.raises(WorkflowFormContractError, match="does not match the v1 property name"):
        _validate_declared(Aliased)


def test_a_standard_tenant_field_and_a_tenant_filter_collide() -> None:
    class WithTenant(ExampleInput):
        tenant: str | None = None
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "device_id": device_field(DEVICES, filters=("tenant",))
        }

    with pytest.raises(WorkflowFormContractError) as raised:
        _validate_declared(WithTenant)

    assert "'tenant' has more than one prefill owner: field:tenant, scope:implicit:device_id" in (
        str(raised.value)
    )


def test_a_read_only_property_is_not_a_url_prefill_owner() -> None:
    class WithReadOnlyTenant(ExampleInput):
        tenant: str = "fixed"
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "tenant": {"ui:readonly": True},
            "device_id": device_field(DEVICES, filters=("tenant",)),
        }

    _validate_declared(WithReadOnlyTenant)


def test_an_alias_colliding_with_another_property_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model({"overlay": api_options(OVERLAYS), "site": LOCATION_FIELD})
    key = (f"{model.__module__}.{model.__qualname__}", "overlay")
    monkeypatch.setitem(form_validation_module.QUERY_ALIASES, key, ("tenant_name",))

    with pytest.raises(
        WorkflowFormContractError, match="'tenant_name' has more than one prefill owner"
    ):
        _validate_declared(model)


def test_shipped_aliases_on_a_device_need_a_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _model({"device_id": device_field(DEVICES, query_param=None)})
    key = (f"{model.__module__}.{model.__qualname__}", "device_id")
    monkeypatch.setitem(form_validation_module.QUERY_ALIASES, key, ("device",))

    with pytest.raises(WorkflowFormContractError, match="no queryParam"):
        _validate_declared(model)


def test_hidden_properties_and_marked_fields_cannot_be_prefilled() -> None:
    """A hidden typeField is no URL owner, so a scope may own its spelling."""

    class Typed(ExampleInput):
        rjsf_ui_schema: ClassVar[Mapping[str, object]] = {
            "site": LOCATION_FIELD,
            "site_type": {"ui:widget": "hidden"},
            "device_id": device_field(DEVICES, filters=("site",), site_field="site"),
        }

    _validate_declared(Typed)
