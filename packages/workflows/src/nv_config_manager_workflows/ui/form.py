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
"""Build and perform model-aware validation of a v1 workflow form.

An input model declares its form as ``rjsf_ui_schema: ClassVar[Mapping[str,
object]]``, a supported subset of an RJSF ``uiSchema``. :func:`build_form`
checks semantic relationships against the model, fills in server-owned options,
and derives the capabilities the form requires. The form catalog validates the
completed envelope against ``workflow-form-v1.schema.json`` before serving it.
"""

import copy
import json
import pkgutil
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, cast

from pydantic import BaseModel, PydanticUserError

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form_options import FormOptionSource
from nv_config_manager_workflows.ui.form_schema import (
    json_type,
    omitted_properties,
    project_form_schema,
)
from nv_config_manager_workflows.ui.option_sources import (
    OptionSource,
    check_endpoint,
    check_params,
    require_text,
)

type FormOptionSourceCompiler = Callable[[FormOptionSource], OptionSource]

UI_SCHEMA_VERSION = 1

RJSF_UI_SCHEMA_ATTRIBUTE = "rjsf_ui_schema"

IMPLICIT_SCOPE_PREFIX = "implicit:"

QUERY_ALIASES: Mapping[tuple[str, str], tuple[str, ...]] = {
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayCreationInput",
        "namespace_tag",
    ): ("namespace",),
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayDeletionInput",
        "namespace_tag",
    ): ("namespace",),
    (
        "nv_config_manager_workflows.workflows.device_password_rotation."
        "DevicePasswordRotationInput",
        "device_id",
    ): ("device",),
    (
        "nv_config_manager_workflows.workflows.infiniband_cable_validation."
        "InfinibandCableValidationInput",
        "ufm_device_id",
    ): ("device",),
    (
        "nv_config_manager_workflows.workflows.cable_validation.SiteCableValidationInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.cumulus_hardware_validation.ValidateHardwareInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.site_backup.SiteBackupInput",
        "roles",
    ): ("role",),
    (
        "nv_config_manager_workflows.workflows.site_password_rotation.SitePasswordRotationInput",
        "roles",
    ): ("role",),
}
"""Already-shipped URL aliases, keyed by (input model, property); emitted as ``queryAliases``.

Authors cannot declare aliases: new forms use property names and ``queryParam``.
"""

QUERY_SEPARATORS: Mapping[tuple[str, str], str] = {
    (
        "nv_config_manager_workflows.workflows.spx_overlay.SpXOverlayTenantChangeInput",
        "port_names",
    ): ",",
}
"""Already-shipped multi-value URL delimiters, keyed by (input model, property)."""


def _load_json(resource: str) -> dict[str, Any]:
    data = pkgutil.get_data(__package__ or __name__, resource)
    if data is None:  # pragma: no cover - the resource ships with the package
        raise RuntimeError(f"Packaged form contract resource {resource!r} is missing")
    return cast(dict[str, Any], json.loads(data))


@cache
def wire_schema() -> dict[str, Any]:
    """Return the canonical v1 wire schema of the ``/form`` response.

    It is read on first use, so importing a workflow module (for example on every
    Temporal sandbox re-import) does not parse it. Treat the result as read-only.
    """
    return _load_json("workflow-form-v1.schema.json")


@cache
def capability_manifest() -> dict[str, Any]:
    """Return the canonical v1 capability manifest the UI supports; treat it as read-only."""
    return _load_json("workflow-form-v1.capabilities.json")


def supported_capabilities() -> frozenset[str]:
    """Return the capabilities a v1 UI of the same release supports."""
    return frozenset(capability_manifest()["capabilities"])


FIELD_CAPABILITIES = {
    "apiOptions": "core-field.api-options.v1",
    "device": "core-field.device.v1",
    "location": "core-field.location.v1",
    "variantRows": "core-field.variant-rows.v1",
}

ENRICHED_API_OPTIONS_CAPABILITY = "core-field.api-options.enriched.v1"

HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY = "theme.hide-schema-descriptions.v1"

EXCLUSIVE_GROUPS_CAPABILITY = "interaction.exclusive-groups.v1"

QUERY_SEPARATOR_CAPABILITY = "prefill.query-separator.v1"

FIELD_COMPARISON_CAPABILITY = "validation.field-comparison.v1"

_TEXT_KEYS = ("ui:title", "ui:help", "ui:description", "ui:placeholder")


@dataclass(frozen=True)
class _Vocabulary:
    """The keys and values the wire schema allows, read from it once."""

    defs: dict[str, Any]
    root_keys: frozenset[str]
    field_keys: frozenset[str]
    widgets: frozenset[str]
    core_fields: frozenset[str]
    source_keys: frozenset[str]
    device_filters: frozenset[str]
    author_option_keys: dict[str | None, frozenset[str]]
    property_name: re.Pattern[str]


@cache
def _vocabulary() -> _Vocabulary:
    defs = wire_schema()["$defs"]
    field_properties = defs["fieldUiSchema"]["properties"]
    return _Vocabulary(
        defs=defs,
        root_keys=frozenset(defs["uiSchema"]["properties"]),
        field_keys=frozenset(field_properties),
        widgets=frozenset(field_properties["ui:widget"]["enum"]),
        core_fields=frozenset(field_properties["ui:field"]["enum"]),
        source_keys=frozenset(defs["optionSource"]["properties"]),
        device_filters=frozenset(
            defs["deviceFieldOptions"]["properties"]["filters"]["items"]["enum"]
        ),
        author_option_keys={
            "apiOptions": frozenset(defs["apiOptionsFieldOptions"]["properties"])
            - {"queryAliases", "querySeparator"},
            "location": frozenset(defs["locationFieldOptions"]["properties"]) - {"queryAliases"},
            "device": frozenset(defs["deviceFieldOptions"]["properties"]) - {"queryAliases"},
            "variantRows": frozenset(defs["variantRowsFieldOptions"]["properties"]),
            None: frozenset(defs["standardFieldOptions"]["properties"]),
        },
        property_name=re.compile(defs["propertyName"]["pattern"]),
    )


def declared_ui_schema(
    model: type[BaseModel],
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> dict[str, Any]:
    """Return a JSON-only deep copy of the model's ``rjsf_ui_schema``; ``{}`` when absent."""
    declared = getattr(model, RJSF_UI_SCHEMA_ATTRIBUTE, None)
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise WorkflowFormContractError(
            f"{model.__qualname__}.{RJSF_UI_SCHEMA_ATTRIBUTE} must be a mapping; got "
            f"{type(declared).__name__}"
        )
    return cast(
        dict[str, Any],
        _plain(
            declared,
            f"{model.__qualname__}.rjsf_ui_schema",
            compile_option_source=compile_option_source,
        ),
    )


def build_form(
    model: type[BaseModel] | None,
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> dict[str, Any]:
    """Return a model-aware checked v1 form envelope for a workflow input model.

    Raises:
        WorkflowFormContractError: The declaration has an invalid model relationship.
    """
    if model is None:
        schema: dict[str, Any] = {}
        ui_schema: dict[str, Any] = {}
    else:
        try:
            schema = project_form_schema(model)
            ui_schema = declared_ui_schema(model, compile_option_source=compile_option_source)
            _FormChecker(model, schema, ui_schema).check()
        except WorkflowFormContractError:
            raise
        except (TypeError, KeyError, ValueError, PydanticUserError) as error:
            # A wrongly typed declaration value (an unhashable widget, say) or a
            # Pydantic JSON Schema error is a
            # form problem too, so a third-party plugin's form stays isolated.
            raise WorkflowFormContractError(
                f"{model.__qualname__}: invalid form declaration ({type(error).__name__}: {error})"
            ) from error
    return {
        "schema": schema,
        "ui_schema": ui_schema,
        "ui_schema_version": UI_SCHEMA_VERSION,
        "requires": derive_requires(ui_schema),
    }


def derive_requires(ui_schema: Mapping[str, Any]) -> list[str]:
    """Return the sorted capabilities a validated ``ui_schema`` uses.

    Raises:
        WorkflowFormContractError: A derived capability is missing from the
            capability manifest, so the UI of this release could not render it.
    """
    required: set[str] = set()
    for key, entry in ui_schema.items():
        if key.startswith("ui:") or "ui:field" not in entry:
            continue
        field = entry["ui:field"]
        required.add(FIELD_CAPABILITIES[field])
        if field == "apiOptions" and entry["ui:options"]["source"].get("response") == "options-v1":
            required.add(ENRICHED_API_OPTIONS_CAPABILITY)
        if entry["ui:options"].get("querySeparator"):
            required.add(QUERY_SEPARATOR_CAPABILITY)
    if ui_schema.get("ui:globalOptions", {}).get("hideSchemaDescriptions") is True:
        required.add(HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY)
    if ui_schema.get("ui:globalOptions", {}).get("exclusiveGroups"):
        required.add(EXCLUSIVE_GROUPS_CAPABILITY)
    if ui_schema.get("ui:globalOptions", {}).get("fieldComparisons"):
        required.add(FIELD_COMPARISON_CAPABILITY)
    unsupported = required - supported_capabilities()
    if unsupported:
        raise WorkflowFormContractError(
            f"capabilities {sorted(unsupported)} are not in the v1 capability manifest"
        )
    return sorted(required)


class _FormChecker:
    """Check one model's ``ui_schema`` against its projected schema; fill server options."""

    def __init__(
        self, model: type[BaseModel], schema: dict[str, Any], ui_schema: dict[str, Any]
    ) -> None:
        self.model = model
        self.name = model.__qualname__
        self.key = f"{model.__module__}.{model.__qualname__}"
        self.properties: dict[str, Any] = schema.get("properties", {})
        self.definitions: dict[str, Any] = schema.get("$defs", {})
        self.omitted = omitted_properties(model)
        self.ui = ui_schema
        self.vocabulary = _vocabulary()
        self.fields: dict[str, dict[str, Any]] = {}
        self.dependencies: dict[str, list[str]] = {}
        self.composite_owners: dict[str, str] = {}

    def fail(self, message: str) -> WorkflowFormContractError:
        return WorkflowFormContractError(f"{self.name}.{RJSF_UI_SCHEMA_ATTRIBUTE}: {message}")

    def check(self) -> None:
        """Run every check; fill ``filterScope`` and ``queryAliases``."""
        for key, value in self.ui.items():
            if key.startswith("ui:"):
                self.check_root(key, value)
            else:
                self.require_property(key, f"key {key!r}", wire_name=True)
                if not isinstance(value, dict):
                    raise self.fail(f"{key!r} must be a mapping; got {value!r}")
                self.fields[key] = value
                self.check_field(key, value)
        for name, entry in self.fields.items():
            self.check_core_field(name, entry)
        self.check_hidden()
        self.check_scopes()
        self.check_exclusive_groups()
        self.check_field_comparisons()
        self.check_cycles()
        self.check_url_owners()

    def require_property(self, name: object, where: str, *, wire_name: bool = False) -> str:
        """Require ``name`` to be a projected property of the form.

        With ``wire_name``, the name must also match the wire schema's
        ``propertyName`` pattern, as ``ui_schema`` keys and the property names
        inside core-field options must.
        """
        if name in self.omitted:
            raise self.fail(
                f"{where} names {name!r}, which is ServerOwned or FormExcluded; omitted "
                "fields are not part of the form and must not appear in its declaration"
            )
        if not isinstance(name, str) or name not in self.properties:
            raise self.fail(
                f"{where} names {name!r}, which is not a property of the form schema; known "
                f"properties: {', '.join(self.properties) or '(none)'}"
            )
        if wire_name and not self.vocabulary.property_name.fullmatch(name):
            raise self.fail(
                f"{where} names {name!r}, which does not match the v1 property name pattern "
                f"{self.vocabulary.property_name.pattern!r}; give the field a plain alias"
            )
        return name

    def check_root(self, key: str, value: Any) -> None:
        root_keys = self.vocabulary.root_keys
        if key not in root_keys:
            raise self.fail(f"unsupported root key {key!r}; supported: {sorted(root_keys)}")
        if key == "ui:order":
            self.check_order(value)
            return
        allowed = self.vocabulary.defs["uiSchema"]["properties"][key]["properties"]
        if not isinstance(value, dict) or not set(value) <= set(allowed):
            raise self.fail(f"{key} must be a mapping with keys from {sorted(allowed)}")
        if "submitText" in value:
            require_text(value["submitText"], f"{self.name} {key}.submitText")
        if "hideSchemaDescriptions" in value and not isinstance(
            value["hideSchemaDescriptions"], bool
        ):
            raise self.fail(f"{key}.hideSchemaDescriptions must be a boolean")

    def check_order(self, order: Any) -> None:
        """Check ``ui:order``: unique projected names, all of them unless ``*`` is used."""
        if not isinstance(order, list) or len(set(map(str, order))) != len(order):
            raise self.fail(f"ui:order must be a list of unique names; got {order!r}")
        for name in order:
            if name != "*":
                self.require_property(name, "ui:order")
        missing = [name for name in self.properties if name not in order]
        if "*" not in order and missing:
            raise self.fail(
                f"ui:order omits {missing} and has no '*'; list every property or add '*'"
            )

    def check_field(self, name: str, entry: dict[str, Any]) -> None:
        """Check the standard keys of one property's entry."""
        vocabulary = self.vocabulary
        unknown = set(entry) - vocabulary.field_keys
        if unknown:
            raise self.fail(
                f"{name!r} uses unsupported keys {sorted(unknown)}; supported: "
                f"{sorted(vocabulary.field_keys)}"
            )
        for key in _TEXT_KEYS:
            if key in entry:
                require_text(entry[key], f"{self.name} {name!r} {key}")
        widget, field = entry.get("ui:widget"), entry.get("ui:field")
        if widget is not None and widget not in vocabulary.widgets:
            raise self.fail(
                f"{name!r} ui:widget {widget!r} is not one of {sorted(vocabulary.widgets)}"
            )
        if field is not None and field not in vocabulary.core_fields:
            raise self.fail(
                f"{name!r} ui:field {field!r} is not one of {sorted(vocabulary.core_fields)}"
            )
        if widget is not None and field is not None:
            raise self.fail(f"{name!r} sets both ui:widget and ui:field")
        if "ui:readonly" in entry and not isinstance(entry["ui:readonly"], bool):
            raise self.fail(f"{name!r} ui:readonly must be a boolean")
        options = entry.get("ui:options", {})
        if not isinstance(options, dict):
            raise self.fail(f"{name!r} ui:options must be a mapping")
        supported = vocabulary.author_option_keys[field]
        unknown = set(options) - supported
        if unknown:
            kind = f"a {field} field" if field else "a standard field"
            raise self.fail(
                f"{name!r} ui:options keys {sorted(unknown)} are not supported on {kind}; "
                f"supported: {sorted(supported)} (queryAliases and querySeparator are added by "
                "the server from its central compatibility map)"
            )
        if field is None and "rows" in options:
            rows = options["rows"]
            if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
                raise self.fail(f"{name!r} ui:options.rows must be a positive integer")
        if field is not None and "ui:options" not in entry:
            suffix = "" if field == "variantRows" else " with a source"
            raise self.fail(f"{name!r} ui:field {field!r} requires ui:options{suffix}")

    def check_core_field(self, name: str, entry: dict[str, Any]) -> None:
        """Check a core field's options against the property it renders."""
        field = entry.get("ui:field")
        if field is None:
            return
        options = entry["ui:options"]
        if field == "variantRows":
            self.check_variant_rows(name, options)
            return
        prop = self.properties[name]
        deps = self.check_source(
            name,
            options.get("source"),
            type_key=field == "location",
            depends_on=field != "device",
            enriched=field == "apiOptions",
        )
        if field in {"apiOptions", "device"} and not self.is_string_or_strings(prop):
            raise self.fail(f"{name!r} is a {field} field, so it must be a string or string list")
        if field == "location":
            if json_type(prop, self.definitions) != "string":
                raise self.fail(f"{name!r} is a location field, so it must be a string")
            if "typeField" in options:
                type_field = self.require_property(
                    options["typeField"], f"{name!r} typeField", wire_name=True
                )
                if (
                    type_field == name
                    or json_type(self.properties[type_field], self.definitions) != "string"
                ):
                    raise self.fail(
                        f"{name!r} typeField {type_field!r} must be another string property"
                    )
                if "type_key" not in options["source"]:
                    raise self.fail(f"{name!r} declares a typeField, so its source needs type_key")
        if field == "device":
            deps.extend(self.check_device(name, entry, options))
        elif field == "apiOptions":
            self.check_api_options(name, prop, options)
        aliases = QUERY_ALIASES.get((self.key, name))
        if aliases:
            if field == "device" and "queryParam" not in options:
                raise self.fail(f"{name!r} has shipped query aliases but no queryParam")
            options["queryAliases"] = list(aliases)
        separator = QUERY_SEPARATORS.get((self.key, name))
        if separator:
            if field != "apiOptions" or json_type(prop, self.definitions) != "array":
                raise self.fail(
                    f"internal query separator for {name!r} requires an apiOptions string list"
                )
            options["querySeparator"] = separator
        self.dependencies[name] = deps

    def check_api_options(
        self, name: str, prop: dict[str, Any], options: Mapping[str, Any]
    ) -> None:
        """Validate enriched option presentation without prescribing request logic."""
        source = options["source"]
        enriched = source.get("response") == "options-v1"
        presentation = options.get("presentation", "select")
        if presentation not in {"select", "grouped-checkboxes"}:
            raise self.fail(
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
            raise self.fail(
                f"{name!r} uses enriched apiOptions presentation, so its source must set "
                "response 'options-v1'"
            )
        if enriched and (source.get("label_key") != "label" or source.get("value_key") != "value"):
            raise self.fail(
                f"{name!r} options-v1 source must use label_key 'label' and value_key 'value'"
            )
        if presentation == "grouped-checkboxes" and json_type(prop, self.definitions) != "array":
            raise self.fail(f"{name!r} uses grouped-checkboxes, so it must be an array property")
        if options.get("selectAll", False) is not False and presentation != "grouped-checkboxes":
            raise self.fail(
                f"{name!r} apiOptions selectAll is available only with grouped-checkboxes"
            )
        for flag in ("selectAll", "showDescriptions", "disableWhenNoMatches"):
            if flag in options and options[flag] is not True:
                raise self.fail(f"{name!r} apiOptions {flag} must be true when present")
        if "metaText" in options:
            meta = options["metaText"]
            if (
                not isinstance(meta, dict)
                or set(meta) != {"key", "label"}
                or meta.get("key") != "matching_device_count"
            ):
                raise self.fail(
                    f"{name!r} apiOptions metaText must contain key "
                    "'matching_device_count' and a label"
                )
            require_text(meta.get("label"), f"{self.name} {name!r} apiOptions metaText.label")

    def check_variant_rows(self, name: str, options: dict[str, Any]) -> None:
        """Validate a generic mutually-exclusive repeatable-row field."""
        allowed = {"ownedProperties", "modes", "minimumRows", "clearInactive", "warning"}
        if set(options) - allowed:
            raise self.fail(
                f"{name!r} variantRows options contain unsupported keys "
                f"{sorted(set(options) - allowed)}"
            )
        owned_properties_value = options.get("ownedProperties")
        if (
            not isinstance(owned_properties_value, list)
            or len(owned_properties_value) < 2
            or len(set(map(str, owned_properties_value))) != len(owned_properties_value)
        ):
            raise self.fail(
                f"{name!r} variantRows ownedProperties must be at least two unique properties"
            )
        owned_properties = [
            self.require_property(value, f"{name!r} variantRows ownedProperties", wire_name=True)
            for value in owned_properties_value
        ]
        if name not in owned_properties:
            raise self.fail(
                f"{name!r} is the variantRows anchor, so ownedProperties must include it"
            )
        minimum_rows = options.get("minimumRows")
        if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
            raise self.fail(f"{name!r} variantRows minimumRows must be a positive integer")
        if options.get("clearInactive") is not True:
            raise self.fail(f"{name!r} variantRows clearInactive must be true")
        if "warning" in options:
            require_text(options["warning"], f"{self.name} {name!r} variantRows warning")

        modes = options.get("modes")
        if not isinstance(modes, list) or len(modes) < 2:
            raise self.fail(f"{name!r} variantRows modes must contain at least two modes")
        mode_ids: set[str] = set()
        used_properties: set[str] = set()
        for index, mode in enumerate(modes):
            mode_properties = self.check_variant_rows_mode(
                name, index, mode, set(owned_properties), mode_ids
            )
            overlap = used_properties & mode_properties
            if overlap:
                raise self.fail(
                    f"{name!r} variantRows properties must belong to only one mode; "
                    f"reused {sorted(overlap)}"
                )
            used_properties.update(mode_properties)
        if used_properties != set(owned_properties):
            raise self.fail(
                f"{name!r} variantRows columns must use every owned property exactly by mode; "
                f"owned {sorted(owned_properties)}, used {sorted(used_properties)}"
            )

        for property_name in owned_properties:
            previous = self.composite_owners.get(property_name)
            if previous is not None:
                raise self.fail(
                    f"property {property_name!r} is owned by composite fields "
                    f"{previous!r} and {name!r}"
                )
            self.composite_owners[property_name] = name
            if (
                property_name != name
                and self.fields.get(property_name, {}).get("ui:widget") != "hidden"
            ):
                raise self.fail(
                    f"{name!r} variantRows ownedProperties includes sibling "
                    f"{property_name!r}, which must use "
                    "ui:widget 'hidden' so RJSF does not render it twice"
                )
        self.dependencies[name] = []

    def check_variant_rows_mode(
        self,
        anchor: str,
        index: int,
        mode: object,
        owned_properties: set[str],
        mode_ids: set[str],
    ) -> set[str]:
        """Validate one object-array or parallel-array row mode."""
        where = f"{anchor!r} variantRows modes[{index}]"
        if not isinstance(mode, dict) or set(mode) != {"id", "label", "columns"}:
            raise self.fail(f"{where} must contain exactly id, label, and columns")
        mode_mapping = cast(dict[str, object], mode)
        mode_id = require_text(mode_mapping["id"], f"{self.name} {where}.id")
        if not self.vocabulary.property_name.fullmatch(mode_id):
            raise self.fail(f"{where}.id must be a plain property-style name")
        if mode_id in mode_ids:
            raise self.fail(f"{anchor!r} variantRows mode ids must be unique")
        mode_ids.add(mode_id)
        require_text(mode_mapping["label"], f"{self.name} {where}.label")
        columns = mode_mapping["columns"]
        if not isinstance(columns, list) or not columns:
            raise self.fail(f"{where}.columns must be a non-empty list")
        keyed = [isinstance(item, dict) and "itemProperty" in item for item in columns]
        if any(keyed) and not all(keyed):
            raise self.fail(f"{where}.columns cannot mix columns with and without itemProperty")

        used: set[str] = set()
        item_properties: set[str] = set()
        object_array_property: str | None = None
        for column_index, column in enumerate(columns):
            array_property, item_property = self.check_variant_rows_column(
                anchor, index, column_index, column, owned_properties
            )
            if array_property in used and item_property is None:
                raise self.fail(f"{where} uses arrayProperty {array_property!r} more than once")
            used.add(array_property)
            if item_property is not None:
                if object_array_property is None:
                    object_array_property = array_property
                elif object_array_property != array_property:
                    raise self.fail(
                        f"{where} columns with itemProperty must share one arrayProperty"
                    )
                if item_property in item_properties:
                    raise self.fail(f"{where} uses itemProperty {item_property!r} more than once")
                item_properties.add(item_property)
        return used

    def check_variant_rows_column(
        self,
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
            raise self.fail(
                f"{where} must contain arrayProperty, label, and kind and only supported keys"
            )
        column_mapping = cast(dict[str, object], column)
        array_property = self.require_property(
            column_mapping["arrayProperty"], f"{where}.arrayProperty", wire_name=True
        )
        if array_property not in owned_properties:
            raise self.fail(
                f"{where}.arrayProperty {array_property!r} is not listed in ownedProperties"
            )
        require_text(column_mapping["label"], f"{self.name} {where}.label")
        if "placeholder" in column_mapping:
            require_text(column_mapping["placeholder"], f"{self.name} {where}.placeholder")
        if "required" in column_mapping and not isinstance(column_mapping["required"], bool):
            raise self.fail(f"{where}.required must be a boolean")

        kind = column_mapping["kind"]
        if kind not in {"text", "select"}:
            raise self.fail(f"{where}.kind must be 'text' or 'select'")
        choices = column_mapping.get("choices")
        if kind == "select":
            if not isinstance(choices, list) or not choices:
                raise self.fail(f"{where} select column needs a non-empty choices list")
            values: set[str] = set()
            for option_index, choice in enumerate(choices):
                if not isinstance(choice, dict) or set(choice) != {"label", "value"}:
                    raise self.fail(
                        f"{where}.choices[{option_index}] must contain exactly label and value"
                    )
                choice_mapping = cast(dict[str, object], choice)
                require_text(choice_mapping["label"], f"{self.name} {where} option label")
                value = require_text(choice_mapping["value"], f"{self.name} {where} option value")
                if value in values:
                    raise self.fail(f"{where} option values must be unique")
                values.add(value)
        elif choices is not None:
            raise self.fail(f"{where} text column cannot declare choices")
        if "pattern" in column_mapping:
            if kind != "text":
                raise self.fail(f"{where}.pattern is available only on text columns")
            pattern = column_mapping["pattern"]
            if not isinstance(pattern, str):
                raise self.fail(f"{where}.pattern must be a string")

        prop = self.properties[array_property]
        if json_type(prop, self.definitions) != "array":
            raise self.fail(f"{where}.arrayProperty {array_property!r} must be an array")
        item = self.resolve_schema(prop.get("items", {}))
        item_property_value = column_mapping.get("itemProperty")
        if item_property_value is None:
            if json_type(item, self.definitions) != "string":
                raise self.fail(f"{where} without itemProperty must name an array of strings")
            return array_property, None
        item_property = require_text(item_property_value, f"{self.name} {where}.itemProperty")
        if not self.vocabulary.property_name.fullmatch(item_property):
            raise self.fail(f"{where}.itemProperty must be a plain property-style name")
        if json_type(item, self.definitions) != "object":
            raise self.fail(f"{where} with itemProperty must name an array of objects")
        item_properties = item.get("properties", {})
        if (
            item_property not in item_properties
            or json_type(item_properties[item_property], self.definitions) != "string"
        ):
            raise self.fail(
                f"{where}.itemProperty {item_property!r} must name a string property of each item"
            )
        return array_property, item_property

    def resolve_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a local schema after following its definition reference."""
        current = schema
        seen: set[str] = set()
        while "$ref" in current:
            name = current["$ref"].rsplit("/", 1)[-1]
            if name in seen or name not in self.definitions:
                break
            seen.add(name)
            current = self.definitions[name]
        return current

    def check_device(self, name: str, entry: dict[str, Any], options: dict[str, Any]) -> list[str]:
        """Check a device field's filters, scope, and site source; fill the implicit scope."""
        filters = options.get("filters")
        allowed_filters = self.vocabulary.device_filters
        if (
            not isinstance(filters, list)
            or len(set(map(str, filters))) != len(filters)
            or not set(map(str, filters)) <= allowed_filters
        ):
            raise self.fail(
                f"{name!r} device filters must be unique values from {sorted(allowed_filters)}; "
                f"got {filters!r}"
            )
        if not isinstance(options.get("siteRequired"), bool):
            raise self.fail(f"{name!r} device siteRequired must be a boolean")
        filter_sources = options.get("filterSources")
        if not isinstance(filter_sources, dict):
            raise self.fail(f"{name!r} device filterSources must be a mapping")
        enabled_filters = set(cast(list[str], filters))
        if set(filter_sources) != enabled_filters:
            raise self.fail(
                f"{name!r} device filterSources keys must exactly match filters; "
                f"expected {sorted(enabled_filters)}, got {sorted(filter_sources)}"
            )
        dependencies: list[str] = []
        for filter_name in cast(list[str], filters):
            filter_source = filter_sources[filter_name]
            source_dependencies = self.check_source(
                f"{name} filterSources[{filter_name!r}]",
                filter_source,
                type_key=filter_name == "site",
                depends_on=False,
                enriched=False,
            )
            if source_dependencies:
                raise self.fail(
                    f"{name!r} device {filter_name!r} filter source endpoint must not "
                    "depend on form properties"
                )
            if filter_name == "site" and (
                not isinstance(filter_source, dict) or "type_key" not in filter_source
            ):
                raise self.fail(f"{name!r} device Site filter source needs type_key")
        if "queryParam" in options:
            require_text(options["queryParam"], f"{self.name} {name!r} queryParam")
        scope = options.get("filterScope")
        if scope is None:
            options["filterScope"] = f"{IMPLICIT_SCOPE_PREFIX}{name}"
        else:
            require_text(scope, f"{self.name} {name!r} filterScope")
            if scope.startswith(IMPLICIT_SCOPE_PREFIX):
                raise self.fail(
                    f"{name!r} filterScope {scope!r} uses the reserved prefix "
                    f"{IMPLICIT_SCOPE_PREFIX!r}"
                )
            if entry.get("ui:readonly"):
                raise self.fail(
                    f"{name!r} is read-only in the shared filter scope {scope!r}; a device "
                    "field with an explicit filterScope cannot be read-only"
                )
        site_field = options.get("siteField")
        if site_field is None:
            return dependencies
        if "site" not in filters:
            raise self.fail(f"{name!r} sets siteField, so its filters must include 'site'")
        site_entry = self.fields.get(
            self.require_property(site_field, f"{name!r} siteField", wire_name=True)
        )
        if (
            site_entry is None
            or site_entry.get("ui:field") != "location"
            or "typeField" not in site_entry.get("ui:options", {})
        ):
            raise self.fail(
                f"{name!r} siteField {site_field!r} must name a location field that declares "
                "a typeField"
            )
        dependencies.append(site_field)
        return dependencies

    def check_source(
        self,
        name: str,
        source: Any,
        *,
        type_key: bool,
        depends_on: bool,
        enriched: bool,
    ) -> list[str]:
        """Check an option source's wire form; return the properties it depends on."""
        where = f"{self.name} {name!r} source"
        if not isinstance(source, dict):
            raise self.fail(f"{name!r} ui:options.source must be a mapping")
        allowed = set(self.vocabulary.source_keys)
        if not type_key:
            allowed.discard("type_key")
        if not depends_on:
            allowed.discard("depends_on")
        if not enriched:
            allowed.discard("response")
        unknown = set(source) - allowed
        if unknown:
            raise self.fail(f"{name!r} source keys {sorted(unknown)} are not allowed here")
        deps = [
            self.require_property(placeholder, f"{name!r} endpoint placeholder")
            for placeholder in check_endpoint(source.get("endpoint"), f"{where}.endpoint")
        ]
        require_text(source.get("label_key"), f"{where}.label_key")
        require_text(source.get("value_key"), f"{where}.value_key")
        if "type_key" in source:
            require_text(source["type_key"], f"{where}.type_key")
        if "response" in source and source["response"] != "options-v1":
            raise self.fail(f"{name!r} source response must be 'options-v1'")
        check_params(source.get("params", {}), f"{where}.params")
        if source.get("clear_on_change", True) is not True:
            raise self.fail(f"{name!r} source clear_on_change must be true when present")
        declared = source.get("depends_on", {})
        if not isinstance(declared, dict):
            raise self.fail(f"{name!r} source depends_on must be a mapping")
        for param, dependency in declared.items():
            require_text(param, f"{where}.depends_on key")
            if (
                not isinstance(dependency, dict)
                or not set(dependency) <= {"field", "required"}
                or dependency.get("required", False) is not False
            ):
                raise self.fail(
                    f"{name!r} depends_on[{param!r}] must be {{field, required?: false}}"
                )
            deps.append(
                self.require_property(
                    dependency.get("field"), f"{name!r} dependency", wire_name=True
                )
            )
        return deps

    def is_string_or_strings(self, prop: dict[str, Any]) -> bool:
        resolved = self.resolve_schema(prop)
        kind = json_type(resolved, self.definitions)
        if kind == "array":
            return json_type(resolved.get("items", {}), self.definitions) == "string"
        return kind == "string"

    def check_hidden(self) -> None:
        """Allow a hidden widget only on a location typeField or a property with a default."""
        type_fields = {
            entry["ui:options"]["typeField"]
            for entry in self.fields.values()
            if entry.get("ui:field") == "location" and "typeField" in entry["ui:options"]
        }
        for name, entry in self.fields.items():
            if (
                entry.get("ui:widget") == "hidden"
                and name not in type_fields
                and name not in self.composite_owners
                and "default" not in self.properties[name]
            ):
                raise self.fail(
                    f"{name!r} is hidden but is neither a location typeField nor a property "
                    "with a projected default; omit it with FormExcluded instead"
                )

    def check_scopes(self) -> None:
        """Require the device fields sharing a scope to agree on their filters."""
        seen: dict[str, tuple[str, tuple[Any, ...]]] = {}
        for name, entry in self.fields.items():
            if entry.get("ui:field") != "device":
                continue
            options = entry["ui:options"]
            signature = (
                options.get("siteField"),
                tuple(sorted(options["filters"])),
                json.dumps(options["filterSources"], sort_keys=True, separators=(",", ":")),
                options["siteRequired"],
            )
            scope = options["filterScope"]
            if scope in seen and seen[scope][1] != signature:
                raise self.fail(
                    f"device fields {seen[scope][0]!r} and {name!r} share filterScope {scope!r} "
                    "but differ in siteField, filters, filterSources, or siteRequired"
                )
            seen.setdefault(scope, (name, signature))

    def check_exclusive_groups(self) -> None:
        """Validate mutually exclusive fields and device-filter owners."""
        groups = self.ui.get("ui:globalOptions", {}).get("exclusiveGroups")
        if groups is None:
            return
        if not isinstance(groups, list) or len(groups) < 2:
            raise self.fail("ui:globalOptions.exclusiveGroups must contain at least two groups")
        used_fields: set[str] = set()
        used_device_filters: set[str] = set()
        complete_groups = 0
        for index, group in enumerate(groups):
            where = f"ui:globalOptions.exclusiveGroups[{index}]"
            if (
                not isinstance(group, dict)
                or not group
                or not set(group)
                <= {
                    "fields",
                    "deviceFilters",
                    "requireComplete",
                }
            ):
                raise self.fail(f"{where} must contain fields and/or deviceFilters")
            group_mapping = cast(dict[str, object], group)
            if group_mapping.get("requireComplete") is True:
                complete_groups += 1
            elif "requireComplete" in group_mapping:
                raise self.fail(f"{where}.requireComplete must be true")
            fields = group_mapping.get("fields", [])
            device_filters = group_mapping.get("deviceFilters", [])
            if (
                not isinstance(fields, list)
                or not isinstance(device_filters, list)
                or not fields
                and not device_filters
            ):
                raise self.fail(f"{where} must contain a non-empty fields or deviceFilters list")
            if len(set(map(str, fields))) != len(fields):
                raise self.fail(f"{where}.fields must be unique")
            if len(set(map(str, device_filters))) != len(device_filters):
                raise self.fail(f"{where}.deviceFilters must be unique")
            for field in fields:
                name = self.require_property(field, f"{where}.fields", wire_name=True)
                if name in used_fields:
                    raise self.fail(
                        f"exclusive group field {name!r} belongs to more than one group"
                    )
                used_fields.add(name)
            for device_filter in device_filters:
                name = self.require_property(
                    device_filter, f"{where}.deviceFilters", wire_name=True
                )
                if name not in fields:
                    raise self.fail(
                        f"{where}.deviceFilters entry {name!r} must also appear in fields"
                    )
                if self.fields.get(name, {}).get("ui:field") != "device":
                    raise self.fail(f"{where}.deviceFilters entry {name!r} must be a device field")
                if name in used_device_filters:
                    raise self.fail(
                        f"exclusive group device filter {name!r} belongs to more than one group"
                    )
                used_device_filters.add(name)
        if complete_groups not in {0, len(groups)}:
            raise self.fail(
                "exclusiveGroups requireComplete must be enabled on every group or none"
            )

    def check_field_comparisons(self) -> None:
        """Validate declarative comparisons between projected numeric fields."""
        comparisons = self.ui.get("ui:globalOptions", {}).get("fieldComparisons")
        if comparisons is None:
            return
        if not isinstance(comparisons, list) or not comparisons:
            raise self.fail("ui:globalOptions.fieldComparisons must be a non-empty list")
        for index, comparison in enumerate(comparisons):
            where = f"ui:globalOptions.fieldComparisons[{index}]"
            if not isinstance(comparison, dict) or set(comparison) != {
                "left",
                "operator",
                "right",
                "message",
            }:
                raise self.fail(f"{where} must contain left, operator, right, and message")
            comparison_mapping = cast(dict[str, object], comparison)
            left = self.require_property(
                comparison_mapping["left"], f"{where}.left", wire_name=True
            )
            right = self.require_property(
                comparison_mapping["right"], f"{where}.right", wire_name=True
            )
            if left == right:
                raise self.fail(f"{where} must compare two different fields")
            for side, name in (("left", left), ("right", right)):
                if json_type(self.properties[name], self.definitions) not in {
                    "integer",
                    "number",
                }:
                    raise self.fail(f"{where}.{side} {name!r} must be numeric")
            if comparison_mapping["operator"] != "lessThan":
                raise self.fail(f"{where}.operator must be 'lessThan'")
            require_text(comparison_mapping["message"], f"{self.name} {where}.message")

    def check_cycles(self) -> None:
        """Reject an option dependency on the field itself, directly or transitively."""
        finished: set[str] = set()

        def visit(name: str, path: list[str]) -> None:
            if name in path:
                cycle = " -> ".join([*path[path.index(name) :], name])
                raise self.fail(f"option dependencies form a cycle: {cycle}")
            if name in finished:
                return
            for target in self.dependencies.get(name, ()):
                visit(target, [*path, name])
            finished.add(name)

        for name in self.dependencies:
            visit(name, [])

    def check_url_owners(self) -> None:
        """Allow at most one prefill owner per URL query parameter."""
        owners: dict[str, set[str]] = {}

        def own(parameter: str, owner: str) -> None:
            owners.setdefault(parameter, set()).add(owner)

        for name in self.properties:
            entry = self.fields.get(name, {})
            if entry.get("ui:widget") == "hidden":
                continue
            field = entry.get("ui:field")
            options = entry.get("ui:options", {})
            owner = f"field:{name}"
            if field == "variantRows":
                continue
            if field is None:
                own(name, owner)
                continue
            if field == "device":
                if "queryParam" in options:
                    own(options["queryParam"], owner)
                scope = f"scope:{options['filterScope']}"
                for parameter in options["filters"]:
                    if parameter != "site" or "siteField" not in options:
                        own(parameter, scope)
            else:
                own(name, owner)
            for alias in options.get("queryAliases", ()):
                own(alias, owner)
        for parameter, claimants in owners.items():
            if len(claimants) > 1:
                raise self.fail(
                    f"URL parameter {parameter!r} has more than one prefill owner: "
                    f"{', '.join(sorted(claimants))}"
                )


def _plain(
    value: Any,
    where: str,
    *,
    compile_option_source: FormOptionSourceCompiler | None = None,
) -> Any:
    """Return a deep JSON copy of a declaration; reject non-JSON values."""
    if isinstance(value, FormOptionSource):
        if compile_option_source is None:
            raise WorkflowFormContractError(
                f"{where} holds unresolved FormOptionSource {value.name!r}; symbolic option "
                "sources require workflow provider context"
            )
        compiled = compile_option_source(value)
        if not isinstance(compiled, OptionSource):
            raise WorkflowFormContractError(
                f"{where} option-source compiler returned {type(compiled).__name__}, expected "
                "OptionSource"
            )
        return _plain(compiled.to_wire(), where, compile_option_source=compile_option_source)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise WorkflowFormContractError(f"{where} has a non-string key")
        return {
            key: _plain(
                item,
                f"{where}.{key}",
                compile_option_source=compile_option_source,
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_plain(item, where, compile_option_source=compile_option_source) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return copy.copy(value)
    raise WorkflowFormContractError(f"{where} holds a non-JSON value {value!r}")


__all__ = [
    "ENRICHED_API_OPTIONS_CAPABILITY",
    "EXCLUSIVE_GROUPS_CAPABILITY",
    "FIELD_COMPARISON_CAPABILITY",
    "FIELD_CAPABILITIES",
    "FormOptionSourceCompiler",
    "HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY",
    "IMPLICIT_SCOPE_PREFIX",
    "QUERY_ALIASES",
    "QUERY_SEPARATORS",
    "QUERY_SEPARATOR_CAPABILITY",
    "RJSF_UI_SCHEMA_ATTRIBUTE",
    "UI_SCHEMA_VERSION",
    "build_form",
    "capability_manifest",
    "declared_ui_schema",
    "derive_requires",
    "supported_capabilities",
    "wire_schema",
]
