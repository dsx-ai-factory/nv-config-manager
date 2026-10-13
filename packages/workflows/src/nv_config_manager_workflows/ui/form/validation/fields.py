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
"""Validation of root directives and individual workflow-form fields."""

from collections.abc import Mapping
from typing import Any, cast

from nv_config_manager_workflows.ui.form.option_sources import (
    check_endpoint,
    check_params,
    require_text,
)
from nv_config_manager_workflows.ui.form.projection import json_type
from nv_config_manager_workflows.ui.form.validation.context import FormValidationContext
from nv_config_manager_workflows.ui.form.validation.query_compatibility import (
    QUERY_ALIASES,
    QUERY_COERCIONS,
    QUERY_SEPARATORS,
)
from nv_config_manager_workflows.ui.form.validation.variant_rows import check_variant_rows

IMPLICIT_SCOPE_PREFIX = "implicit:"

_TEXT_KEYS = ("ui:title", "ui:help", "ui:description", "ui:placeholder")


def check_root(context: FormValidationContext, key: str, value: Any) -> None:
    """Validate one root-level UI-schema directive."""
    root_keys = context.vocabulary.root_keys
    if key not in root_keys:
        raise context.fail(f"unsupported root key {key!r}; supported: {sorted(root_keys)}")
    if key == "ui:order":
        _check_order(context, value)
        return
    allowed = context.vocabulary.defs["uiSchema"]["properties"][key]["properties"]
    if not isinstance(value, dict) or not set(value) <= set(allowed):
        raise context.fail(f"{key} must be a mapping with keys from {sorted(allowed)}")
    if "submitText" in value:
        require_text(value["submitText"], f"{context.name} {key}.submitText")
    if "hideSchemaDescriptions" in value and not isinstance(value["hideSchemaDescriptions"], bool):
        raise context.fail(f"{key}.hideSchemaDescriptions must be a boolean")


def _check_order(context: FormValidationContext, order: Any) -> None:
    """Check unique projected names and coverage in ``ui:order``."""
    if not isinstance(order, list) or len(set(map(str, order))) != len(order):
        raise context.fail(f"ui:order must be a list of unique names; got {order!r}")
    for name in order:
        if name != "*":
            context.require_property(name, "ui:order")
    missing = [name for name in context.properties if name not in order]
    if "*" not in order and missing:
        raise context.fail(
            f"ui:order omits {missing} and has no '*'; list every property or add '*'"
        )


def check_field(context: FormValidationContext, name: str, entry: dict[str, Any]) -> None:
    """Validate the standard keys of one property's UI-schema entry."""
    vocabulary = context.vocabulary
    unknown = set(entry) - vocabulary.field_keys
    if unknown:
        raise context.fail(
            f"{name!r} uses unsupported keys {sorted(unknown)}; supported: "
            f"{sorted(vocabulary.field_keys)}"
        )
    for key in _TEXT_KEYS:
        if key in entry:
            require_text(entry[key], f"{context.name} {name!r} {key}")
    enum_names = entry.get("ui:enumNames")
    if enum_names is not None:
        if (
            not isinstance(enum_names, list)
            or not enum_names
            or any(not isinstance(label, str) or not label.strip() for label in enum_names)
        ):
            raise context.fail(f"{name!r} ui:enumNames must be a non-empty list of labels")
        prop = context.properties[name]
        enum_values = prop.get("enum", context.resolve_schema(prop).get("enum"))
        if not isinstance(enum_values, list):
            raise context.fail(f"{name!r} ui:enumNames requires enum values in its form schema")
        if len(enum_names) != len(enum_values):
            raise context.fail(
                f"{name!r} ui:enumNames has {len(enum_names)} labels for "
                f"{len(enum_values)} enum values"
            )
    help_variant = entry.get("ui:helpVariant")
    if help_variant is not None:
        if help_variant not in vocabulary.help_variants:
            raise context.fail(
                f"{name!r} ui:helpVariant {help_variant!r} is not one of "
                f"{sorted(vocabulary.help_variants)}"
            )
        if "ui:help" not in entry:
            raise context.fail(f"{name!r} ui:helpVariant requires ui:help")
    widget, field = entry.get("ui:widget"), entry.get("ui:field")
    if widget is not None and widget not in vocabulary.widgets:
        raise context.fail(
            f"{name!r} ui:widget {widget!r} is not one of {sorted(vocabulary.widgets)}"
        )
    if field is not None and field not in vocabulary.core_fields:
        raise context.fail(
            f"{name!r} ui:field {field!r} is not one of {sorted(vocabulary.core_fields)}"
        )
    if widget is not None and field is not None:
        raise context.fail(f"{name!r} sets both ui:widget and ui:field")
    if "ui:readonly" in entry and not isinstance(entry["ui:readonly"], bool):
        raise context.fail(f"{name!r} ui:readonly must be a boolean")
    options = entry.get("ui:options", {})
    if not isinstance(options, dict):
        raise context.fail(f"{name!r} ui:options must be a mapping")
    supported = vocabulary.author_option_keys[field]
    unknown = set(options) - supported
    if unknown:
        kind = f"a {field} field" if field else "a standard field"
        raise context.fail(
            f"{name!r} ui:options keys {sorted(unknown)} are not supported on {kind}; "
            f"supported: {sorted(supported)} (queryAliases, queryCoercion, and querySeparator "
            "are added by the server from its central compatibility map)"
        )
    if field is None and "rows" in options:
        rows = options["rows"]
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
            raise context.fail(f"{name!r} ui:options.rows must be a positive integer")
    if field is not None and "ui:options" not in entry:
        suffix = "" if field == "variantRows" else " with a source"
        raise context.fail(f"{name!r} ui:field {field!r} requires ui:options{suffix}")
    coercion = QUERY_COERCIONS.get((context.key, name))
    if coercion:
        prop = context.resolve_schema(context.properties[name])
        kind = json_type(prop, context.definitions)
        if coercion == "integer-prefix-or-default":
            default = prop.get("default")
            if kind != "integer" or isinstance(default, bool) or not isinstance(default, int):
                raise context.fail(
                    f"internal query coercion for {name!r} requires an integer with a default"
                )
        elif coercion == "case-insensitive-boolean":
            if kind != "boolean":
                raise context.fail(
                    f"internal query coercion for {name!r} requires a boolean property"
                )
        else:  # pragma: no cover - the central map is a repository-owned constant
            raise context.fail(f"internal query coercion for {name!r} is unsupported: {coercion}")
        entry.setdefault("ui:options", {})["queryCoercion"] = coercion


def check_core_field(context: FormValidationContext, name: str, entry: dict[str, Any]) -> None:
    """Validate a registered core field against the property it renders."""
    field = entry.get("ui:field")
    if field is None:
        return
    options = entry["ui:options"]
    if field == "variantRows":
        check_variant_rows(context, name, options)
        return
    prop = context.properties[name]
    dependencies = _check_source(
        context,
        name,
        options.get("source"),
        type_key=field in {"device", "location"},
        depends_on=field != "device",
        enriched=field == "apiOptions",
    )
    if field in {"apiOptions", "device"} and not context.is_string_or_strings(prop):
        raise context.fail(f"{name!r} is a {field} field, so it must be a string or string list")
    if field == "location":
        if json_type(prop, context.definitions) != "string":
            raise context.fail(f"{name!r} is a location field, so it must be a string")
        if "typeField" in options:
            type_field = context.require_property(
                options["typeField"], f"{name!r} typeField", wire_name=True
            )
            if (
                type_field == name
                or json_type(context.properties[type_field], context.definitions) != "string"
            ):
                raise context.fail(
                    f"{name!r} typeField {type_field!r} must be another string property"
                )
            if "type_key" not in options["source"]:
                raise context.fail(f"{name!r} declares a typeField, so its source needs type_key")
    if field == "device":
        dependencies.extend(_check_device(context, name, entry, options))
    elif field == "apiOptions":
        _check_api_options(context, name, prop, options)
    aliases = QUERY_ALIASES.get((context.key, name))
    if aliases:
        if field == "device" and "queryParam" not in options:
            raise context.fail(f"{name!r} has shipped query aliases but no queryParam")
        options["queryAliases"] = list(aliases)
    separator = QUERY_SEPARATORS.get((context.key, name))
    if separator:
        if field != "apiOptions" or json_type(prop, context.definitions) != "array":
            raise context.fail(
                f"internal query separator for {name!r} requires an apiOptions string list"
            )
        options["querySeparator"] = separator
    context.dependencies[name] = dependencies


def _check_api_options(
    context: FormValidationContext,
    name: str,
    prop: dict[str, Any],
    options: Mapping[str, Any],
) -> None:
    """Validate enriched option presentation without prescribing request logic."""
    source = options["source"]
    enriched = source.get("response") == "options-v1"
    presentation = options.get("presentation", "select")
    if presentation not in {"select", "grouped-checkboxes"}:
        raise context.fail(
            f"{name!r} apiOptions presentation must be 'select' or "
            f"'grouped-checkboxes'; got {presentation!r}"
        )
    presentation_keys = {
        "presentation",
        "selectAll",
        "showDescriptions",
        "metaText",
        "disableWhenNoMatches",
    }
    if set(options) & presentation_keys and not enriched:
        raise context.fail(
            f"{name!r} uses enriched apiOptions presentation, so its source must set "
            "response 'options-v1'"
        )
    if enriched and (source.get("label_key") != "label" or source.get("value_key") != "value"):
        raise context.fail(
            f"{name!r} options-v1 source must use label_key 'label' and value_key 'value'"
        )
    if presentation == "grouped-checkboxes" and json_type(prop, context.definitions) != "array":
        raise context.fail(f"{name!r} uses grouped-checkboxes, so it must be an array property")
    if options.get("selectAll", False) is not False and presentation != "grouped-checkboxes":
        raise context.fail(
            f"{name!r} apiOptions selectAll is available only with grouped-checkboxes"
        )
    for flag in ("selectAll", "showDescriptions", "disableWhenNoMatches"):
        if flag in options and options[flag] is not True:
            raise context.fail(f"{name!r} apiOptions {flag} must be true when present")
    if "metaText" in options:
        meta = options["metaText"]
        if (
            not isinstance(meta, dict)
            or set(meta) != {"key", "label"}
            or meta.get("key") != "matching_device_count"
        ):
            raise context.fail(
                f"{name!r} apiOptions metaText must contain key 'matching_device_count' and a label"
            )
        require_text(meta.get("label"), f"{context.name} {name!r} apiOptions metaText.label")


def _check_device(
    context: FormValidationContext,
    name: str,
    entry: dict[str, Any],
    options: dict[str, Any],
) -> list[str]:
    """Validate a device field and fill its implicit filter scope."""
    filters = options.get("filters")
    allowed_filters = context.vocabulary.device_filters
    if (
        not isinstance(filters, list)
        or len(set(map(str, filters))) != len(filters)
        or not set(map(str, filters)) <= allowed_filters
    ):
        raise context.fail(
            f"{name!r} device filters must be unique values from {sorted(allowed_filters)}; "
            f"got {filters!r}"
        )
    if not isinstance(options.get("siteRequired"), bool):
        raise context.fail(f"{name!r} device siteRequired must be a boolean")
    filter_sources = options.get("filterSources")
    if not isinstance(filter_sources, dict):
        raise context.fail(f"{name!r} device filterSources must be a mapping")
    enabled_filters = set(cast(list[str], filters))
    if set(filter_sources) != enabled_filters:
        raise context.fail(
            f"{name!r} device filterSources keys must exactly match filters; "
            f"expected {sorted(enabled_filters)}, got {sorted(filter_sources)}"
        )
    dependencies: list[str] = []
    for filter_name in cast(list[str], filters):
        filter_source = filter_sources[filter_name]
        source_dependencies = _check_source(
            context,
            f"{name} filterSources[{filter_name!r}]",
            filter_source,
            type_key=filter_name == "site",
            depends_on=False,
            enriched=False,
        )
        if source_dependencies:
            raise context.fail(
                f"{name!r} device {filter_name!r} filter source endpoint must not "
                "depend on form properties"
            )
        if filter_name == "site" and (
            not isinstance(filter_source, dict) or "type_key" not in filter_source
        ):
            raise context.fail(f"{name!r} device Site filter source needs type_key")
    if "queryParam" in options:
        require_text(options["queryParam"], f"{context.name} {name!r} queryParam")
    scope = options.get("filterScope")
    if scope is None:
        options["filterScope"] = f"{IMPLICIT_SCOPE_PREFIX}{name}"
    else:
        require_text(scope, f"{context.name} {name!r} filterScope")
        if scope.startswith(IMPLICIT_SCOPE_PREFIX):
            raise context.fail(
                f"{name!r} filterScope {scope!r} uses the reserved prefix {IMPLICIT_SCOPE_PREFIX!r}"
            )
        if entry.get("ui:readonly"):
            raise context.fail(
                f"{name!r} is read-only in the shared filter scope {scope!r}; a device "
                "field with an explicit filterScope cannot be read-only"
            )
    site_field = options.get("siteField")
    if site_field is None:
        return dependencies
    if "site" not in filters:
        raise context.fail(f"{name!r} sets siteField, so its filters must include 'site'")
    site_entry = context.fields.get(
        context.require_property(site_field, f"{name!r} siteField", wire_name=True)
    )
    if (
        site_entry is None
        or site_entry.get("ui:field") != "location"
        or "typeField" not in site_entry.get("ui:options", {})
    ):
        raise context.fail(
            f"{name!r} siteField {site_field!r} must name a location field that declares "
            "a typeField"
        )
    dependencies.append(site_field)
    return dependencies


def _check_source(
    context: FormValidationContext,
    name: str,
    source: Any,
    *,
    type_key: bool,
    depends_on: bool,
    enriched: bool,
) -> list[str]:
    """Validate an option source and return its property dependencies."""
    where = f"{context.name} {name!r} source"
    if not isinstance(source, dict):
        raise context.fail(f"{name!r} ui:options.source must be a mapping")
    allowed = set(context.vocabulary.source_keys)
    if not type_key:
        allowed.discard("type_key")
    if not depends_on:
        allowed.discard("depends_on")
    if not enriched:
        allowed.discard("response")
    unknown = set(source) - allowed
    if unknown:
        raise context.fail(f"{name!r} source keys {sorted(unknown)} are not allowed here")
    dependencies = [
        context.require_property(placeholder, f"{name!r} endpoint placeholder")
        for placeholder in check_endpoint(source.get("endpoint"), f"{where}.endpoint")
    ]
    require_text(source.get("label_key"), f"{where}.label_key")
    require_text(source.get("value_key"), f"{where}.value_key")
    if "type_key" in source:
        require_text(source["type_key"], f"{where}.type_key")
    if "response" in source and source["response"] != "options-v1":
        raise context.fail(f"{name!r} source response must be 'options-v1'")
    check_params(source.get("params", {}), f"{where}.params")
    if source.get("clear_on_change", True) is not True:
        raise context.fail(f"{name!r} source clear_on_change must be true when present")
    declared = source.get("depends_on", {})
    if not isinstance(declared, dict):
        raise context.fail(f"{name!r} source depends_on must be a mapping")
    for param, dependency in declared.items():
        require_text(param, f"{where}.depends_on key")
        if (
            not isinstance(dependency, dict)
            or not set(dependency) <= {"field", "required", "option_key"}
            or dependency.get("required", False) is not False
        ):
            raise context.fail(
                f"{name!r} depends_on[{param!r}] must be "
                "{field, required?: false, option_key?: string}"
            )
        dependency_field = context.require_property(
            dependency.get("field"), f"{name!r} dependency", wire_name=True
        )
        dependencies.append(dependency_field)
        if "option_key" not in dependency:
            continue
        option_key = require_text(
            dependency["option_key"], f"{where}.depends_on[{param!r}].option_key"
        )
        dependency_entry = context.fields.get(dependency_field, {})
        dependency_source = dependency_entry.get("ui:options", {}).get("source", {})
        if dependency_entry.get("ui:field") not in {"device", "location"}:
            raise context.fail(
                f"{name!r} depends_on[{param!r}] option_key requires {dependency_field!r} "
                "to be a device or location field"
            )
        if dependency_source.get("type_key") != option_key:
            raise context.fail(
                f"{name!r} depends_on[{param!r}] option_key {option_key!r} must match "
                f"{dependency_field!r} source type_key"
            )
    return dependencies


__all__ = ["check_core_field", "check_field", "check_root"]
