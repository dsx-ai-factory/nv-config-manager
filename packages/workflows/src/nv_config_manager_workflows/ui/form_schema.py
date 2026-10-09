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
"""Project a workflow input model's JSON Schema into the schema its form renders.

The projection only shapes what RJSF draws. ``model_json_schema()`` and with it
the API request body, MCP tool schemas, and generated clients are unchanged, and
Pydantic stays the authoritative validator of every submission.
"""

import math
import re
from typing import Annotated, Any, TypeAliasType, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.markers import (
    FORM_MARKERS,
    UNSET,
    FormExcluded,
    FormSchema,
    ServerOwned,
)

_KEYWORD_TYPES: dict[str, tuple[str, ...]] = {
    "minItems": ("array",),
    "maxItems": ("array",),
    "minLength": ("string",),
    "maxLength": ("string",),
    "pattern": ("string",),
    "minimum": ("integer", "number"),
    "maximum": ("integer", "number"),
}


class FormJsonSchema(GenerateJsonSchema):
    """Generate a model's JSON Schema for a form.

    - An optional field is just its type. Pydantic renders ``X | None`` as
      ``anyOf: [X, {"type": "null"}]``, which RJSF draws as a chooser between the
      two types. A form leaves an optional field empty instead, so the field keeps
      only ``X``, and a ``None`` default is dropped: the form omits the field and
      the model applies the default.
    - Every field has a title. Pydantic leaves it off fields whose type is a
      ``$ref`` (enums, models), and RJSF would label them with the raw field name.
    """

    def field_title_should_be_set(self, schema: object) -> bool:
        """Title every field, including enum- and model-typed ones."""
        return True

    def handle_ref_overrides(self, json_schema: JsonSchemaValue) -> JsonSchemaValue:
        """Keep a ``$ref`` property's title even when it repeats the definition's title."""
        return json_schema

    def nullable_schema(self, schema: core_schema.NullableSchema) -> JsonSchemaValue:
        """Return the non-null type alone."""
        return self.generate_inner(schema["schema"])

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        """Drop a ``None`` default, which the non-null type no longer allows."""
        json_schema = super().default_schema(schema)
        if "default" in json_schema and json_schema["default"] is None:
            del json_schema["default"]
        return json_schema


def field_marker(model: type[BaseModel], name: str) -> object | None:
    """Return the form marker in a top-level field's metadata, or ``None``.

    Raises:
        WorkflowFormContractError: The field carries more than one marker.
    """
    markers = [m for m in model.model_fields[name].metadata if isinstance(m, FORM_MARKERS)]
    if len(markers) > 1:
        raise WorkflowFormContractError(
            f"{model.__qualname__} field {name!r} carries more than one form marker: {markers!r}"
        )
    return markers[0] if markers else None


def omitted_properties(model: type[BaseModel]) -> set[str]:
    """Return the properties marked ``ServerOwned`` or ``FormExcluded``."""
    return {
        _property_name(name, info)
        for name, info in model.model_fields.items()
        if isinstance(field_marker(model, name), ServerOwned | FormExcluded)
    }


def project_form_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the form projection of ``model``'s JSON Schema.

    Optional fields collapse to their type, every property is titled,
    ``ServerOwned`` and ``FormExcluded`` properties are removed, and
    ``FormSchema`` keywords are merged into their properties.

    Raises:
        WorkflowFormContractError: A marker is misplaced, a marked field has no
            default, or a ``FormSchema`` keyword does not fit its property.
    """
    _reject_nested_markers(model)
    schema = model.model_json_schema(schema_generator=FormJsonSchema)
    properties: dict[str, Any] = schema.get("properties", {})
    definitions: dict[str, Any] = schema.get("$defs", {})
    for name, info in model.model_fields.items():
        marker = field_marker(model, name)
        where = f"{model.__qualname__} field {name!r}"
        prop_name = _property_name(name, info)
        if isinstance(marker, ServerOwned | FormExcluded):
            if info.is_required():
                raise WorkflowFormContractError(
                    f"{where} is {type(marker).__name__} but has no default; the request "
                    "body is validated before the server fills it, so the field needs a "
                    "Pydantic default or default_factory"
                )
            properties.pop(prop_name, None)
            if prop_name in schema.get("required", []):
                schema["required"].remove(prop_name)
        elif isinstance(marker, FormSchema):
            if prop_name not in properties:
                raise WorkflowFormContractError(
                    f"{where} carries FormSchema but has no {prop_name!r} property in the "
                    "model's JSON Schema"
                )
            _apply_form_schema(where, marker, info, properties[prop_name], definitions)
    if "required" in schema and not schema["required"]:
        del schema["required"]
    _prune_definitions(schema)
    return schema


def json_type(schema: dict[str, Any], definitions: dict[str, Any]) -> str | None:
    """Return a property's JSON ``type``, following a ``$ref``; ``None`` when ambiguous."""
    if "$ref" in schema:
        target = definitions.get(schema["$ref"].rsplit("/", 1)[-1])
        return None if target is None else json_type(target, definitions)
    value = schema.get("type")
    return value if isinstance(value, str) else None


def _property_name(name: str, info: FieldInfo) -> str:
    """Return a field's property name in the validation-mode JSON Schema.

    Like Pydantic: the ``validation_alias`` when it is a string, else the
    ``alias``, else the field name.
    """
    if isinstance(info.validation_alias, str):
        return info.validation_alias
    return info.alias or name


def _apply_form_schema(
    where: str,
    marker: FormSchema,
    info: FieldInfo,
    prop: dict[str, Any],
    definitions: dict[str, Any],
) -> None:
    """Merge a ``FormSchema`` marker's keywords and default into its property."""
    kind = json_type(prop, definitions)
    for keyword, value in marker.keywords().items():
        if kind not in _KEYWORD_TYPES[keyword]:
            raise WorkflowFormContractError(
                f"{where} FormSchema sets {keyword}, which applies to "
                f"{' or '.join(_KEYWORD_TYPES[keyword])} properties; the property's type is "
                f"{kind!r}"
            )
        if keyword == "pattern":
            if not isinstance(value, str):
                raise WorkflowFormContractError(f"{where} FormSchema pattern must be a string")
            try:
                re.compile(value)
            except re.error as error:
                raise WorkflowFormContractError(
                    f"{where} FormSchema pattern {value!r} is not a valid regular expression: "
                    f"{error}"
                ) from error
        elif keyword in {"minimum", "maximum"}:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                raise WorkflowFormContractError(
                    f"{where} FormSchema {keyword} must be a finite number"
                )
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WorkflowFormContractError(
                f"{where} FormSchema {keyword} must be a non-negative integer"
            )
        prop[keyword] = value
    if marker.default is not UNSET:
        adapter: TypeAdapter[Any] = TypeAdapter(info.rebuild_annotation())
        try:
            value = adapter.validate_python(marker.default)
        except ValidationError as error:
            raise WorkflowFormContractError(
                f"{where} FormSchema default {marker.default!r} is not valid for the field: {error}"
            ) from error
        default = adapter.dump_python(value, mode="json")
        if default is None:
            raise WorkflowFormContractError(
                f"{where} FormSchema default must not be None; leave an optional field "
                "empty without a form default instead"
            )
        prop["default"] = default


def _reject_nested_markers(model: type[BaseModel]) -> None:
    """Reject a marker anywhere but a top-level field's own metadata."""
    for name, info in model.model_fields.items():
        if _carries_marker(info.annotation, set()):
            raise WorkflowFormContractError(
                f"{model.__qualname__} field {name!r} has a form marker inside its type "
                f"{info.annotation!r}; ServerOwned, FormExcluded, and FormSchema belong only "
                "in a top-level field's own Annotated metadata, never in a list item, a "
                "union member, or a nested model's field"
            )


def _carries_marker(annotation: object, seen: set[object]) -> bool:
    """Return whether a type carries a form marker anywhere, nested models included."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(
            any(isinstance(m, FORM_MARKERS) for m in info.metadata)
            or _carries_marker(info.annotation, seen)
            for info in annotation.model_fields.values()
        )
    if get_origin(annotation) is Annotated:
        inner, *metadata = get_args(annotation)
        return any(isinstance(m, FORM_MARKERS) for m in metadata) or _carries_marker(inner, seen)
    alias = get_origin(annotation) or annotation
    if isinstance(alias, TypeAliasType) and alias not in seen:
        seen.add(alias)
        if _carries_marker(alias.__value__, seen):
            return True
    return any(_carries_marker(argument, seen) for argument in get_args(annotation))


def _prune_definitions(schema: dict[str, Any]) -> None:
    """Drop ``$defs`` entries no longer referenced once properties were removed."""
    definitions: dict[str, Any] = schema.get("$defs", {})
    reachable: set[str] = set()
    pending = [{key: value for key, value in schema.items() if key != "$defs"}]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in reachable and name in definitions:
                    reachable.add(name)
                    pending.append(definitions[name])
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    for name in set(definitions) - reachable:
        del definitions[name]
    if "$defs" in schema and not definitions:
        del schema["$defs"]


__all__ = [
    "FormJsonSchema",
    "field_marker",
    "json_type",
    "omitted_properties",
    "project_form_schema",
]
