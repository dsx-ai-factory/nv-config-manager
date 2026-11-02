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
"""Semantic validation for the composite ``variantRows`` core field."""

from typing import Any, cast

from nv_config_manager_workflows.ui.form.option_sources import require_text
from nv_config_manager_workflows.ui.form.projection import json_type
from nv_config_manager_workflows.ui.form.validation.context import FormValidationContext


def check_variant_rows(context: FormValidationContext, name: str, options: dict[str, Any]) -> None:
    """Validate a generic mutually-exclusive repeatable-row field."""
    allowed = {"ownedProperties", "modes", "minimumRows", "clearInactive", "warning"}
    if set(options) - allowed:
        raise context.fail(
            f"{name!r} variantRows options contain unsupported keys "
            f"{sorted(set(options) - allowed)}"
        )
    owned_properties_value = options.get("ownedProperties")
    if (
        not isinstance(owned_properties_value, list)
        or len(owned_properties_value) < 2
        or len(set(map(str, owned_properties_value))) != len(owned_properties_value)
    ):
        raise context.fail(
            f"{name!r} variantRows ownedProperties must be at least two unique properties"
        )
    owned_properties = [
        context.require_property(value, f"{name!r} variantRows ownedProperties", wire_name=True)
        for value in owned_properties_value
    ]
    if name not in owned_properties:
        raise context.fail(
            f"{name!r} is the variantRows anchor, so ownedProperties must include it"
        )
    minimum_rows = options.get("minimumRows")
    if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
        raise context.fail(f"{name!r} variantRows minimumRows must be a positive integer")
    if options.get("clearInactive") is not True:
        raise context.fail(f"{name!r} variantRows clearInactive must be true")
    if "warning" in options:
        require_text(options["warning"], f"{context.name} {name!r} variantRows warning")

    modes = options.get("modes")
    if not isinstance(modes, list) or len(modes) < 2:
        raise context.fail(f"{name!r} variantRows modes must contain at least two modes")
    mode_ids: set[str] = set()
    used_properties: set[str] = set()
    for index, mode in enumerate(modes):
        mode_properties = _check_variant_rows_mode(
            context, name, index, mode, set(owned_properties), mode_ids
        )
        overlap = used_properties & mode_properties
        if overlap:
            raise context.fail(
                f"{name!r} variantRows properties must belong to only one mode; "
                f"reused {sorted(overlap)}"
            )
        used_properties.update(mode_properties)
    if used_properties != set(owned_properties):
        raise context.fail(
            f"{name!r} variantRows columns must use every owned property exactly by mode; "
            f"owned {sorted(owned_properties)}, used {sorted(used_properties)}"
        )

    for property_name in owned_properties:
        previous = context.composite_owners.get(property_name)
        if previous is not None:
            raise context.fail(
                f"property {property_name!r} is owned by composite fields {previous!r} and {name!r}"
            )
        context.composite_owners[property_name] = name
        if (
            property_name != name
            and context.fields.get(property_name, {}).get("ui:widget") != "hidden"
        ):
            raise context.fail(
                f"{name!r} variantRows ownedProperties includes sibling "
                f"{property_name!r}, which must use "
                "ui:widget 'hidden' so RJSF does not render it twice"
            )
    context.dependencies[name] = []


def _check_variant_rows_mode(
    context: FormValidationContext,
    anchor: str,
    index: int,
    mode: object,
    owned_properties: set[str],
    mode_ids: set[str],
) -> set[str]:
    """Validate one object-array or parallel-array row mode."""
    where = f"{anchor!r} variantRows modes[{index}]"
    required = {"id", "label", "columns"}
    allowed = {*required, "description"}
    if not isinstance(mode, dict) or not required <= set(mode) or set(mode) - allowed:
        raise context.fail(f"{where} must contain id, label, and columns and only supported keys")
    mode_mapping = cast(dict[str, object], mode)
    mode_id = require_text(mode_mapping["id"], f"{context.name} {where}.id")
    if not context.vocabulary.property_name.fullmatch(mode_id):
        raise context.fail(f"{where}.id must be a plain property-style name")
    if mode_id in mode_ids:
        raise context.fail(f"{anchor!r} variantRows mode ids must be unique")
    mode_ids.add(mode_id)
    require_text(mode_mapping["label"], f"{context.name} {where}.label")
    if "description" in mode_mapping:
        require_text(mode_mapping["description"], f"{context.name} {where}.description")
    columns = mode_mapping["columns"]
    if not isinstance(columns, list) or not columns:
        raise context.fail(f"{where}.columns must be a non-empty list")
    keyed = [isinstance(item, dict) and "itemProperty" in item for item in columns]
    if any(keyed) and not all(keyed):
        raise context.fail(f"{where}.columns cannot mix columns with and without itemProperty")

    used: set[str] = set()
    item_properties: set[str] = set()
    object_array_property: str | None = None
    for column_index, column in enumerate(columns):
        array_property, item_property = _check_variant_rows_column(
            context, anchor, index, column_index, column, owned_properties
        )
        if array_property in used and item_property is None:
            raise context.fail(f"{where} uses arrayProperty {array_property!r} more than once")
        used.add(array_property)
        if item_property is not None:
            if object_array_property is None:
                object_array_property = array_property
            elif object_array_property != array_property:
                raise context.fail(
                    f"{where} columns with itemProperty must share one arrayProperty"
                )
            if item_property in item_properties:
                raise context.fail(f"{where} uses itemProperty {item_property!r} more than once")
            item_properties.add(item_property)
    return used


def _check_variant_rows_column(
    context: FormValidationContext,
    anchor: str,
    mode_index: int,
    column_index: int,
    column: object,
    owned_properties: set[str],
) -> tuple[str, str | None]:
    """Validate one row column and return its array and optional item property."""
    where = f"{anchor!r} variantRows modes[{mode_index}].columns[{column_index}]"
    allowed = {
        "arrayProperty",
        "itemProperty",
        "label",
        "kind",
        "placeholder",
        "required",
        "pattern",
        "choices",
    }
    if (
        not isinstance(column, dict)
        or set(column) - allowed
        or not {"arrayProperty", "label", "kind"} <= set(column)
    ):
        raise context.fail(
            f"{where} must contain arrayProperty, label, and kind and only supported keys"
        )
    column_mapping = cast(dict[str, object], column)
    array_property = context.require_property(
        column_mapping["arrayProperty"], f"{where}.arrayProperty", wire_name=True
    )
    if array_property not in owned_properties:
        raise context.fail(
            f"{where}.arrayProperty {array_property!r} is not listed in ownedProperties"
        )
    require_text(column_mapping["label"], f"{context.name} {where}.label")
    if "placeholder" in column_mapping:
        require_text(column_mapping["placeholder"], f"{context.name} {where}.placeholder")
    if "required" in column_mapping and not isinstance(column_mapping["required"], bool):
        raise context.fail(f"{where}.required must be a boolean")

    kind = column_mapping["kind"]
    if kind not in {"text", "select"}:
        raise context.fail(f"{where}.kind must be 'text' or 'select'")
    choices = column_mapping.get("choices")
    if kind == "select":
        if not isinstance(choices, list) or not choices:
            raise context.fail(f"{where} select column needs a non-empty choices list")
        values: set[str] = set()
        for option_index, choice in enumerate(choices):
            if not isinstance(choice, dict) or set(choice) != {"label", "value"}:
                raise context.fail(
                    f"{where}.choices[{option_index}] must contain exactly label and value"
                )
            choice_mapping = cast(dict[str, object], choice)
            require_text(choice_mapping["label"], f"{context.name} {where} option label")
            value = require_text(choice_mapping["value"], f"{context.name} {where} option value")
            if value in values:
                raise context.fail(f"{where} option values must be unique")
            values.add(value)
    elif choices is not None:
        raise context.fail(f"{where} text column cannot declare choices")
    if "pattern" in column_mapping:
        if kind != "text":
            raise context.fail(f"{where}.pattern is available only on text columns")
        pattern = column_mapping["pattern"]
        if not isinstance(pattern, str):
            raise context.fail(f"{where}.pattern must be a string")

    prop = context.properties[array_property]
    if json_type(prop, context.definitions) != "array":
        raise context.fail(f"{where}.arrayProperty {array_property!r} must be an array")
    item = context.resolve_schema(prop.get("items", {}))
    item_property_value = column_mapping.get("itemProperty")
    if item_property_value is None:
        if json_type(item, context.definitions) != "string":
            raise context.fail(f"{where} without itemProperty must name an array of strings")
        return array_property, None
    item_property = require_text(item_property_value, f"{context.name} {where}.itemProperty")
    if not context.vocabulary.property_name.fullmatch(item_property):
        raise context.fail(f"{where}.itemProperty must be a plain property-style name")
    if json_type(item, context.definitions) != "object":
        raise context.fail(f"{where} with itemProperty must name an array of objects")
    item_properties = item.get("properties", {})
    if (
        item_property not in item_properties
        or json_type(item_properties[item_property], context.definitions) != "string"
    ):
        raise context.fail(
            f"{where}.itemProperty {item_property!r} must name a string property of each item"
        )
    return array_property, item_property


__all__ = ["check_variant_rows"]
