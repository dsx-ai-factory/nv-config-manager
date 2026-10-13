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
"""Root JSON Schema constraints on properties omitted from workflow forms."""

import json
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_workflows.ui import (
    FormExcluded,
    WorkflowFormContractError,
    project_form_schema,
)
from nv_config_manager_workflows.ui.form.projection_constraints import (
    _KNOWN_ROOT_KEYWORDS,
    _ROOT_KEYWORD_GROUPS,
    validate_omitted_root_constraints,
)


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

    validate_omitted_root_constraints(Model, {"$schema": dialect}, {"internal"})


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

    validate_omitted_root_constraints(Model, schema, {"internal"})


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
        validate_omitted_root_constraints(Model, schema, {"internal"})
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

    validate_omitted_root_constraints(Model, schema, {"internal"})


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
        validate_omitted_root_constraints(Model, schema, {"internal"})


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
        validate_omitted_root_constraints(Model, schema, {"internal"})


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
        validate_omitted_root_constraints(Model, schema, {"internal"})


@pytest.mark.parametrize("reference", ["other.json#/Rule", "#named-anchor", "#/$defs/Bad~2Name"])
def test_unsupported_or_invalid_root_references_fail_closed(reference: str) -> None:
    class Model(BaseModel):
        internal: Annotated[str, FormExcluded()] = "server-default"

    schema = {"$ref": reference}

    with pytest.raises(
        WorkflowFormContractError,
        match=r"only valid same-document JSON Pointer references are supported at #/\$ref",
    ):
        validate_omitted_root_constraints(Model, schema, {"internal"})


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
        validate_omitted_root_constraints(Model, schema, {"internal"})


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
