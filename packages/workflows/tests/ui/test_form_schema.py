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
from pydantic import BaseModel, Field

from nv_config_manager_workflows.ui import (
    FormExcluded,
    FormSchema,
    ServerOwned,
    WorkflowFormContractError,
    project_form_schema,
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


def test_form_schema_keywords_and_default_are_applied(projected: dict[str, Any]) -> None:
    properties = projected["properties"]
    assert properties["trigger"]["default"] == "blue"
    assert (properties["statuses"]["minItems"], properties["statuses"]["maxItems"]) == (1, 3)
    assert (properties["rd"]["minimum"], properties["rd"]["maximum"]) == (0, 65535)
    assert properties["pkey"]["pattern"] == r"^0x[0-9a-f]{1,4}$"
    assert properties["pkey"]["maxLength"] == 6


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
        (str, FormSchema(pattern="("), "not a valid regular expression"),
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
