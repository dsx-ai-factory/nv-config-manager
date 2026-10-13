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
"""Form projection of input-model JSON Schemas and placement of the form markers."""

import json
from enum import StrEnum
from typing import Annotated, Any

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

from nv_config_manager_workflows.ui import (
    FormExcluded,
    FormSchema,
    ServerOwned,
    WorkflowFormContractError,
    project_form_schema,
)
from nv_config_manager_workflows.ui.form_schema import (
    _KNOWN_ROOT_KEYWORDS,
    _ROOT_KEYWORD_GROUPS,
    _validate_omitted_root_constraints,
)

ServerOwnedUser = Annotated[str, ServerOwned()]


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Address(BaseModel):
    host: str
    port: int | None = None


class ExampleInput(BaseModel):
    name: str
    note: str | None = Field(default=None, description="Optional note.")
    color: Color = Color.RED
    tags: list[str] = Field(default=["a"], min_length=0)
    address: Address | None = None
    addresses: list[Address] = []
    retries: int = 3
    user: ServerOwnedUser = ""
    trigger: Annotated[Color, FormSchema(default=Color.BLUE)]
    internal: Annotated[str | None, FormExcluded()] = None
    statuses: Annotated[list[str], FormSchema(min_items=1, max_items=3)] = ["Active"]
    rd: Annotated[int, FormSchema(minimum=0, maximum=65535)] = 1
    pkey: Annotated[str | None, FormSchema(pattern=r"^0x[0-9a-f]{1,4}$", max_length=6)] = None


def test_root_keyword_groups_are_disjoint() -> None:
    categorized = [keyword for group in _ROOT_KEYWORD_GROUPS for keyword in group]

    assert len(categorized) == len(_KNOWN_ROOT_KEYWORDS)


@pytest.mark.parametrize(
    "dialect",
    [
        "https://json-schema.org/draft/2020-12/schema",
        "https://json-schema.org/draft/2020-12/schema#",
    ],
)
def test_supported_root_schema_dialect_spellings(dialect: str) -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    _validate_omitted_root_constraints(Model, {"$schema": dialect}, {"internal"})


@pytest.fixture(scope="module")
def projected() -> dict[str, Any]:
    return project_form_schema(ExampleInput)


def test_optional_fields_collapse_and_drop_null_defaults(projected: dict[str, Any]) -> None:
    note = projected["properties"]["note"]
    assert note == {"description": "Optional note.", "title": "Note", "type": "string"}
    assert projected["properties"]["address"] == {"$ref": "#/$defs/Address", "title": "Address"}
    assert projected["$defs"]["Address"]["properties"]["port"] == {
        "title": "Port",
        "type": "integer",
    }


def test_required_nullable_fields_and_collection_items_keep_null() -> None:
    class NullableInput(BaseModel):
        required_note: str | None
        optional_note: str | None = None
        values: list[str | None]

    schema = project_form_schema(NullableInput)

    assert schema["required"] == ["required_note", "values"]
    assert schema["properties"]["required_note"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert schema["properties"]["optional_note"] == {
        "title": "Optional Note",
        "type": "string",
    }
    assert schema["properties"]["values"]["items"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


def test_only_optional_uses_of_a_nullable_alias_collapse() -> None:
    type NullableText = str | None

    class NullableAliasInput(BaseModel):
        optional_note: NullableText = None
        required_note: NullableText
        values: list[NullableText]

    schema = project_form_schema(NullableAliasInput)

    assert schema["properties"]["optional_note"] == {
        "title": "Optional Note",
        "type": "string",
    }
    assert schema["properties"]["required_note"] == {
        "$ref": "#/$defs/NullableText",
        "title": "Required Note",
    }
    assert schema["properties"]["values"]["items"] == {"$ref": "#/$defs/NullableText"}
    assert schema["$defs"]["NullableText"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


def test_every_property_is_titled_and_defaults_and_requiredness_are_kept(
    projected: dict[str, Any],
) -> None:
    properties = projected["properties"]
    assert all("title" in prop for prop in properties.values())
    assert properties["color"] == {"$ref": "#/$defs/Color", "default": "red", "title": "Color"}
    assert properties["tags"]["default"] == ["a"]
    assert properties["addresses"]["items"] == {"$ref": "#/$defs/Address"}
    assert projected["required"] == ["name", "trigger"]


def test_marked_properties_are_removed(projected: dict[str, Any]) -> None:
    assert "user" not in projected["properties"]
    assert "internal" not in projected["properties"]
    assert list(projected["properties"])[:3] == ["name", "note", "color"]


@pytest.mark.parametrize(
    ("dependency", "pointer"),
    [
        ({"visible": ["internal"]}, "#/dependentRequired/visible/0"),
        ({"internal": ["visible"]}, "#/dependentRequired/internal"),
    ],
    ids=["value", "trigger"],
)
def test_omitted_fields_reject_root_dependencies_that_reference_them(
    dependency: dict[str, list[str]], pointer: str
) -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"dependentRequired": dependency})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=rf"cannot omit form property 'internal'.*{pointer}",
    ):
        project_form_schema(Model)


@pytest.mark.parametrize(
    ("keyword", "constraint", "pointer"),
    [
        (
            "dependentSchemas",
            {"visible": {"required": ["internal"]}},
            "#/dependentSchemas/visible/required/0",
        ),
        (
            "dependentSchemas",
            {"internal": {"required": ["visible"]}},
            "#/dependentSchemas/internal",
        ),
        (
            "dependencies",
            {"visible": ["internal"]},
            "#/dependencies/visible/0",
        ),
        (
            "dependencies",
            {"visible": {"required": ["internal"]}},
            "#/dependencies/visible/required/0",
        ),
    ],
    ids=["dependent-schema-value", "dependent-schema-trigger", "legacy-list", "legacy-schema"],
)
def test_omitted_fields_reject_schema_and_legacy_dependencies(
    keyword: str, constraint: dict[str, Any], pointer: str
) -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={keyword: constraint})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=rf"cannot omit form property 'internal'.*{pointer}",
    ):
        project_form_schema(Model)


def test_omitted_fields_reject_references_inside_root_applicators() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(
            json_schema_extra={
                "allOf": [
                    {
                        "if": {"required": ["enabled"]},
                        "then": {"required": ["internal"]},
                    }
                ]
            }
        )

        enabled: bool = False
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'internal'.*#/allOf/0/then/required/0",
    ):
        project_form_schema(Model)


@pytest.mark.parametrize("keyword", ["if", "then", "else"])
def test_ignored_standalone_conditionals_do_not_constrain_omitted_fields(keyword: str) -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={keyword: {"required": ["internal"]}})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    projected = project_form_schema(Model)

    assert projected[keyword] == {"required": ["internal"]}
    assert "internal" not in projected["properties"]


def test_root_applicators_that_only_reference_visible_fields_are_preserved() -> None:
    one_of = [{"required": ["first"]}, {"required": ["second"]}]

    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"oneOf": one_of})

        first: str | None = None
        second: str | None = None
        internal: Annotated[str, FormExcluded()] = "server-default"

    projected = project_form_schema(Model)

    assert projected["oneOf"] == one_of
    assert "internal" not in projected["properties"]


def test_nested_properties_with_an_omitted_root_property_name_are_not_rejected() -> None:
    class Child(BaseModel):
        internal: str

    class Model(BaseModel):
        child: Child
        internal: Annotated[str, FormExcluded()] = "server-default"

    projected = project_form_schema(Model)

    assert projected["$defs"]["Child"]["required"] == ["internal"]
    assert "internal" not in projected["properties"]


def test_child_instance_applicators_are_outside_the_root_constraint_walk() -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    child = {
        "type": "object",
        "properties": {"internal": {"type": "string"}},
        "required": ["internal"],
        "minProperties": 1,
    }
    schema = {
        "type": "object",
        "properties": {"child": child},
        "additionalProperties": child,
        "patternProperties": {"^child": child},
    }

    _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_omitted_constraint_references_use_the_validation_alias() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"dependentRequired": {"visible": ["private"]}})

        visible: str
        internal: Annotated[str, FormExcluded()] = Field(
            default="server-default", validation_alias="private"
        )

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'private'.*#/dependentRequired/visible/0",
    ):
        project_form_schema(Model)


def test_omitted_constraint_references_follow_cyclic_local_refs() -> None:
    class Model(BaseModel):
        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {
        "$defs": {
            "RootRule": {
                "allOf": [
                    {"$ref": "#/$defs/RootRule"},
                    {"required": ["internal"]},
                ]
            }
        },
        "allOf": [{"$ref": "#/$defs/RootRule"}],
    }
    before = json.dumps(schema, sort_keys=True)
    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'internal'.*#/\$defs/RootRule/allOf/1/required/0",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})
    assert json.dumps(schema, sort_keys=True) == before


def test_safe_root_ref_cycle_terminates_without_rejection() -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {
        "$defs": {
            "RootRule": {
                "allOf": [
                    {"$ref": "#/$defs/RootRule"},
                    {"required": ["visible"]},
                ]
            }
        },
        "$ref": "#/$defs/RootRule",
    }

    _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_omitted_constraint_validation_inspects_ref_siblings() -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {
        "$defs": {"VisibleRule": {"required": ["visible"]}},
        "allOf": [
            {
                "$ref": "#/$defs/VisibleRule",
                "required": ["internal"],
            }
        ],
    }

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'internal'.*#/allOf/0/required/0",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_omitted_constraint_validation_rejects_embedded_schema_resources() -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {
        "$defs": {
            "Rule": {"required": ["visible"]},
            "Safe": {
                "$id": "urn:embedded",
                "$defs": {"Rule": {"required": ["internal"]}},
                "$ref": "#/$defs/Rule",
            },
        },
        "allOf": [{"$ref": "#/$defs/Safe"}],
    }

    with pytest.raises(
        WorkflowFormContractError,
        match=r"embedded JSON Schema resources are unsupported at #/\$defs/Safe/\$id",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_percent_encoded_same_document_json_pointer_is_followed() -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {
        "$defs": {"Rule": {"required": ["internal"]}},
        "allOf": [{"$ref": "#/%24defs/Rule"}],
    }

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'internal'.*#/\$defs/Rule/required/0",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})


@pytest.mark.parametrize("reference", ["other.json#/Rule", "#named-anchor", "#/$defs/Bad~2Name"])
def test_unsupported_or_invalid_root_references_fail_closed(reference: str) -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {"$ref": reference}

    with pytest.raises(
        WorkflowFormContractError,
        match=r"only valid same-document JSON Pointer references are supported at #/\$ref",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_unknown_root_scope_keywords_fail_closed() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"customAssertion": {"required": ["internal"]}})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=r"unrecognized root-scope JSON Schema keyword at #/customAssertion",
    ):
        project_form_schema(Model)


def test_root_scope_subset_validation_only_applies_when_properties_are_omitted() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"customAnnotation": "preserved"})

        visible: str

    assert project_form_schema(Model)["customAnnotation"] == "preserved"


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("$anchor", "rule"),
        ("$dynamicAnchor", "rule"),
        ("$dynamicRef", "#rule"),
        ("$recursiveAnchor", "rule"),
        ("$recursiveRef", "#"),
        ("$vocabulary", {"urn:custom": True}),
    ],
)
def test_unsupported_root_scope_resolution_machinery_fails_closed(keyword: str, value: Any) -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {keyword: value}

    with pytest.raises(
        WorkflowFormContractError,
        match=r"root-scope JSON Schema keyword .* is unsupported at #/",
    ):
        _validate_omitted_root_constraints(Model, schema, {"internal"})


def test_malformed_known_root_scope_keywords_fail_closed() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"allOf": {}})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=r"invalid JSON Schema at #/allOf .*not of type 'array'",
    ):
        project_form_schema(Model)


def test_omitted_constraint_error_pointer_escapes_the_validation_alias() -> None:
    alias = "private/value~id"

    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"dependentRequired": {alias: ["visible"]}})

        visible: str
        internal: Annotated[str, FormExcluded()] = Field(
            default="server-default", validation_alias=alias
        )

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'private/value~id'.*"
        r"#/dependentRequired/private~1value~0id",
    ):
        project_form_schema(Model)


def test_root_default_cannot_restore_an_omitted_property() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(
            json_schema_extra={"default": {"visible": "shown", "internal": "server-default"}}
        )

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=r"cannot omit form property 'internal'.*#/default/internal",
    ):
        project_form_schema(Model)


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("minProperties", 1),
        ("maxProperties", 2),
        ("const", {"visible": "fixed"}),
        ("enum", [{"visible": "fixed"}]),
    ],
)
def test_omitted_fields_reject_whole_root_object_assertions(keyword: str, value: Any) -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={keyword: value})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    with pytest.raises(
        WorkflowFormContractError,
        match=rf"root JSON Schema constraint at #/{keyword} evaluates the whole object",
    ):
        project_form_schema(Model)


def test_direct_root_required_is_projected_with_its_omitted_property() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(json_schema_extra={"required": ["visible", "internal"]})

        visible: str
        internal: Annotated[str, FormExcluded()] = "server-default"

    projected = project_form_schema(Model)

    assert projected["required"] == ["visible"]


def test_form_schema_keywords_and_default_are_applied(projected: dict[str, Any]) -> None:
    properties = projected["properties"]
    assert properties["trigger"]["default"] == "blue"
    assert (properties["statuses"]["minItems"], properties["statuses"]["maxItems"]) == (1, 3)
    assert (properties["rd"]["minimum"], properties["rd"]["maximum"]) == (0, 65535)
    assert properties["pkey"]["pattern"] == r"^0x[0-9a-f]{1,4}$"
    assert properties["pkey"]["maxLength"] == 6


def test_form_schema_can_require_a_defaulted_field_without_changing_the_model() -> None:
    class Model(BaseModel):
        first: str
        namespace: Annotated[str, FormSchema(required=True)] = "spectrumx"
        last: str

    assert Model.model_validate({"first": "a", "last": "z"}).namespace == "spectrumx"
    assert Model.model_json_schema()["required"] == ["first", "last"]

    projected = project_form_schema(Model)

    assert projected["required"] == ["first", "namespace", "last"]
    assert projected["properties"]["namespace"]["default"] == "spectrumx"


def test_form_schema_required_must_be_a_boolean() -> None:
    marker = FormSchema(required="yes")  # type: ignore[arg-type]

    class Model(BaseModel):
        namespace: Annotated[str, marker] = "spectrumx"

    with pytest.raises(WorkflowFormContractError, match="required must be a boolean"):
        project_form_schema(Model)


def test_projection_leaves_the_model_json_schema_unchanged() -> None:
    before = json.dumps(ExampleInput.model_json_schema())
    project_form_schema(ExampleInput)
    schema = ExampleInput.model_json_schema()

    assert json.dumps(schema) == before
    assert "user" in schema["properties"]
    assert schema["properties"]["note"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "minItems" not in schema["properties"]["statuses"]
    assert "trigger" in schema["required"]


def test_definitions_used_only_by_omitted_fields_are_pruned() -> None:
    class Enriched(BaseModel):
        device_id: str
        device: Annotated[Address | None, FormExcluded()] = None

    projected = project_form_schema(Enriched)

    assert "$defs" not in projected
    assert "$defs" in Enriched.model_json_schema()


def test_a_top_level_annotated_alias_marker_is_accepted() -> None:
    class Aliased(BaseModel):
        user: ServerOwnedUser = ""

    assert "user" not in project_form_schema(Aliased)["properties"]


def test_server_owned_rejects_fields_the_http_boundary_cannot_fill() -> None:
    class Model(BaseModel):
        owner: Annotated[str, ServerOwned()] = ""
        user_domain: Annotated[str, ServerOwned()] = ""

    with pytest.raises(WorkflowFormContractError, match="supports only 'user'"):
        project_form_schema(Model)


def _nested_list() -> type[BaseModel]:
    class Model(BaseModel):
        users: list[Annotated[str, ServerOwned()]] = []

    return Model


def _nested_union() -> type[BaseModel]:
    class Model(BaseModel):
        user: ServerOwnedUser | None = None

    return Model


def _nested_model() -> type[BaseModel]:
    class Inner(BaseModel):
        secret: Annotated[str, FormExcluded()] = ""

    class Model(BaseModel):
        inner: Inner | None = None

    return Model


@pytest.mark.parametrize(
    "factory", [_nested_list, _nested_union, _nested_model], ids=["list", "union", "model"]
)
def test_nested_markers_are_rejected(factory: Any) -> None:
    with pytest.raises(WorkflowFormContractError, match="form marker inside its type"):
        project_form_schema(factory())


@pytest.mark.parametrize("marker", [ServerOwned(), FormExcluded()], ids=["server", "excluded"])
def test_a_marked_field_without_a_default_is_rejected(marker: object) -> None:
    class Model(BaseModel):
        value: Annotated[str, marker]

    with pytest.raises(WorkflowFormContractError, match="has no default"):
        project_form_schema(Model)


def test_two_markers_on_one_field_are_rejected() -> None:
    class Model(BaseModel):
        value: Annotated[str, ServerOwned(), FormSchema(min_length=1)] = ""

    with pytest.raises(WorkflowFormContractError, match="more than one form marker"):
        project_form_schema(Model)


@pytest.mark.parametrize(
    ("annotation", "marker", "message"),
    [
        (str, FormSchema(min_items=1), "minItems"),
        (list[str], FormSchema(min_length=1), "minLength"),
        (bool, FormSchema(minimum=0), "minimum"),
        (int, FormSchema(default="many"), "not valid for the field"),
        (Color, FormSchema(default="green"), "not valid for the field"),
        (str | None, FormSchema(default=None), "default must not be None"),
    ],
)
def test_form_schema_keywords_must_fit_the_property(
    annotation: Any, marker: FormSchema, message: str
) -> None:
    class Model(BaseModel):
        value: Annotated[annotation, marker]

    with pytest.raises(WorkflowFormContractError, match=message):
        project_form_schema(Model)


def test_form_schema_preserves_ecmascript_patterns_without_python_compilation() -> None:
    pattern = r"^(?<guid>[0-9a-f]+)$"

    class Model(BaseModel):
        value: Annotated[str, FormSchema(pattern=pattern)]

    assert project_form_schema(Model)["properties"]["value"]["pattern"] == pattern


@pytest.mark.parametrize("keyword", ["minimum", "maximum"])
@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_form_schema_numeric_bounds_must_be_finite(keyword: str, value: float) -> None:
    marker = FormSchema(minimum=value) if keyword == "minimum" else FormSchema(maximum=value)

    class Model(BaseModel):
        number: Annotated[float, marker]

    with pytest.raises(WorkflowFormContractError, match=f"{keyword} must be a finite number"):
        project_form_schema(Model)


def test_form_schema_numeric_bounds_allow_large_integers() -> None:
    minimum = 10**400

    class Model(BaseModel):
        number: Annotated[int, FormSchema(minimum=minimum)]

    assert project_form_schema(Model)["properties"]["number"]["minimum"] == minimum


def test_markers_apply_to_a_field_under_its_validation_alias() -> None:
    class Model(BaseModel):
        user: Annotated[str, ServerOwned()] = Field(default="", validation_alias="owner")
        count: Annotated[int, FormSchema(minimum=1)] = Field(default=1, validation_alias="how_many")

    properties = project_form_schema(Model)["properties"]

    assert list(properties) == ["how_many"]
    assert properties["how_many"]["minimum"] == 1


@pytest.mark.parametrize("marker", [ServerOwned(), FormExcluded()], ids=["server", "excluded"])
@pytest.mark.parametrize(
    "validation_alias",
    [AliasChoices("owner", "user"), AliasPath("identity", "user")],
    ids=["choices", "path"],
)
def test_omitted_fields_reject_structured_validation_aliases(
    marker: object, validation_alias: AliasChoices | AliasPath
) -> None:
    class Model(BaseModel):
        user: Annotated[str, marker] = Field(default="", validation_alias=validation_alias)

    with pytest.raises(
        WorkflowFormContractError,
        match="ServerOwned and FormExcluded support only string validation aliases",
    ):
        project_form_schema(Model)
