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
"""Build and validate the v1 ``/form`` envelope of a workflow input model.

An input model declares its form as ``rjsf_ui_schema: ClassVar[Mapping[str,
object]]``, a supported subset of an RJSF ``uiSchema``. :func:`build_form`
checks that declaration against the model and the v1 wire contract
(``workflow-form-v1.schema.json``), fills in server-owned options, derives the
capabilities the form requires, and returns the envelope the endpoint serves.
"""

import copy
import json
import pkgutil
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, cast

from pydantic import BaseModel

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError
from nv_config_manager_workflows.ui.form_schema import (
    json_type,
    omitted_properties,
    project_form_schema,
)
from nv_config_manager_workflows.ui.options import (
    check_endpoint,
    check_params,
    require_text,
)

UI_SCHEMA_VERSION = 1
"""Version of the form contract served as ``ui_schema_version``."""

RJSF_UI_SCHEMA_ATTRIBUTE = "rjsf_ui_schema"
"""Input-model class variable holding the form's RJSF ``uiSchema``."""

IMPLICIT_SCOPE_PREFIX = "implicit:"
"""Prefix of the private filter scope every unscoped device field receives."""

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


def _load_json(resource: str) -> dict[str, Any]:
    """Return a JSON document packaged beside this module."""
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
"""The capability each core field requires."""

ENRICHED_API_OPTIONS_CAPABILITY = "core-field.api-options.enriched.v1"
"""Capability required by the standard enriched option response and presentation."""

HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY = "theme.hide-schema-descriptions.v1"
"""Capability required by ``ui:globalOptions.hideSchemaDescriptions``."""

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
        device_filters=frozenset(defs["deviceOptions"]["properties"]["filters"]["items"]["enum"]),
        author_option_keys={
            "apiOptions": frozenset(defs["apiOptionsOptions"]["properties"]) - {"queryAliases"},
            "location": frozenset(defs["locationOptions"]["properties"]) - {"queryAliases"},
            "device": frozenset(defs["deviceOptions"]["properties"]) - {"queryAliases"},
            "variantRows": frozenset(defs["variantRowsOptions"]["properties"]),
            None: frozenset(defs["standardOptions"]["properties"]),
        },
        property_name=re.compile(defs["propertyName"]["pattern"]),
    )


def declared_ui_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON-only deep copy of the model's ``rjsf_ui_schema``; ``{}`` when absent."""
    declared = getattr(model, RJSF_UI_SCHEMA_ATTRIBUTE, None)
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise WorkflowFormContractError(
            f"{model.__qualname__}.{RJSF_UI_SCHEMA_ATTRIBUTE} must be a mapping; got "
            f"{type(declared).__name__}"
        )
    return cast(dict[str, Any], _plain(declared, f"{model.__qualname__}.rjsf_ui_schema"))


def build_form(model: type[BaseModel] | None) -> dict[str, Any]:
    """Return the validated v1 ``/form`` envelope for a workflow input model.

    Raises:
        WorkflowFormContractError: The declaration breaks the v1 form contract.
    """
    if model is None:
        schema: dict[str, Any] = {}
        ui_schema: dict[str, Any] = {}
    else:
        try:
            schema = project_form_schema(model)
            ui_schema = declared_ui_schema(model)
            _FormChecker(model, schema, ui_schema).check()
        except WorkflowFormContractError:
            raise
        except (TypeError, KeyError, ValueError) as error:
            # A wrongly typed declaration value (an unhashable widget, say) or a
            # Pydantic JSON Schema error (PydanticUserError is a TypeError) is a
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
        if (
            field == "apiOptions"
            and entry["ui:options"]["source"].get("response") == "options-v1"
        ):
            required.add(ENRICHED_API_OPTIONS_CAPABILITY)
    if ui_schema.get("ui:globalOptions", {}).get("hideSchemaDescriptions") is True:
        required.add(HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY)
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
        """Return an error naming the model."""
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
        """Check a root ``ui:*`` key."""
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
                f"supported: {sorted(supported)} (queryAliases is added by "
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
        allowed = {"owns", "variants", "minimumRows", "clearInactive", "warning"}
        if set(options) - allowed:
            raise self.fail(
                f"{name!r} variantRows options contain unsupported keys "
                f"{sorted(set(options) - allowed)}"
            )
        owns_value = options.get("owns")
        if (
            not isinstance(owns_value, list)
            or len(owns_value) < 2
            or len(set(map(str, owns_value))) != len(owns_value)
        ):
            raise self.fail(f"{name!r} variantRows owns must be at least two unique properties")
        owns = [
            self.require_property(value, f"{name!r} variantRows owns", wire_name=True)
            for value in owns_value
        ]
        if name not in owns:
            raise self.fail(f"{name!r} is the variantRows anchor, so it must own itself")
        minimum_rows = options.get("minimumRows")
        if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
            raise self.fail(f"{name!r} variantRows minimumRows must be a positive integer")
        if options.get("clearInactive") is not True:
            raise self.fail(f"{name!r} variantRows clearInactive must be true")
        if "warning" in options:
            require_text(options["warning"], f"{self.name} {name!r} variantRows warning")

        variants = options.get("variants")
        if not isinstance(variants, list) or len(variants) < 2:
            raise self.fail(f"{name!r} variantRows variants must contain at least two variants")
        variant_ids: set[str] = set()
        used_properties: set[str] = set()
        for index, variant in enumerate(variants):
            variant_properties = self.check_variant_rows_variant(
                name, index, variant, set(owns), variant_ids
            )
            overlap = used_properties & variant_properties
            if overlap:
                raise self.fail(
                    f"{name!r} variantRows properties must belong to only one variant; "
                    f"reused {sorted(overlap)}"
                )
            used_properties.update(variant_properties)
        if used_properties != set(owns):
            raise self.fail(
                f"{name!r} variantRows fields must use every owned property exactly by variant; "
                f"owned {sorted(owns)}, used {sorted(used_properties)}"
            )

        for property_name in owns:
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
                    f"{name!r} variantRows owns sibling {property_name!r}, which must use "
                    "ui:widget 'hidden' so RJSF does not render it twice"
                )
        self.dependencies[name] = []

    def check_variant_rows_variant(
        self,
        anchor: str,
        index: int,
        variant: object,
        owns: set[str],
        variant_ids: set[str],
    ) -> set[str]:
        """Validate one object-array or parallel-array row variant."""
        where = f"{anchor!r} variantRows variants[{index}]"
        if not isinstance(variant, dict) or set(variant) != {"id", "label", "fields"}:
            raise self.fail(f"{where} must contain exactly id, label, and fields")
        variant_id = require_text(variant["id"], f"{self.name} {where}.id")
        if not self.vocabulary.property_name.fullmatch(variant_id):
            raise self.fail(f"{where}.id must be a plain property-style name")
        if variant_id in variant_ids:
            raise self.fail(f"{anchor!r} variantRows variant ids must be unique")
        variant_ids.add(variant_id)
        require_text(variant["label"], f"{self.name} {where}.label")
        fields = variant["fields"]
        if not isinstance(fields, list) or not fields:
            raise self.fail(f"{where}.fields must be a non-empty list")
        keyed = [isinstance(item, dict) and "key" in item for item in fields]
        if any(keyed) and not all(keyed):
            raise self.fail(f"{where}.fields cannot mix keyed and unkeyed fields")

        used: set[str] = set()
        keys: set[str] = set()
        keyed_property: str | None = None
        for field_index, row_field in enumerate(fields):
            property_name, key = self.check_variant_rows_field(
                anchor, index, field_index, row_field, owns
            )
            if property_name in used and key is None:
                raise self.fail(f"{where} uses property {property_name!r} more than once")
            used.add(property_name)
            if key is not None:
                if keyed_property is None:
                    keyed_property = property_name
                elif keyed_property != property_name:
                    raise self.fail(f"{where} keyed fields must share one object-array property")
                if key in keys:
                    raise self.fail(f"{where} uses object key {key!r} more than once")
                keys.add(key)
        return used

    def check_variant_rows_field(
        self,
        anchor: str,
        variant_index: int,
        field_index: int,
        row_field: object,
        owns: set[str],
    ) -> tuple[str, str | None]:
        """Validate one row column and return its top-level property and optional object key."""
        where = f"{anchor!r} variantRows variants[{variant_index}].fields[{field_index}]"
        allowed = {
            "property",
            "key",
            "label",
            "kind",
            "placeholder",
            "required",
            "pattern",
            "options",
        }
        if (
            not isinstance(row_field, dict)
            or set(row_field) - allowed
            or not {"property", "label", "kind"} <= set(row_field)
        ):
            raise self.fail(
                f"{where} must contain property, label, and kind and only supported keys"
            )
        property_name = self.require_property(
            row_field["property"], f"{where}.property", wire_name=True
        )
        if property_name not in owns:
            raise self.fail(f"{where}.property {property_name!r} is not listed in owns")
        require_text(row_field["label"], f"{self.name} {where}.label")
        if "placeholder" in row_field:
            require_text(row_field["placeholder"], f"{self.name} {where}.placeholder")
        if "required" in row_field and not isinstance(row_field["required"], bool):
            raise self.fail(f"{where}.required must be a boolean")

        kind = row_field["kind"]
        if kind not in {"text", "select"}:
            raise self.fail(f"{where}.kind must be 'text' or 'select'")
        choices = row_field.get("options")
        if kind == "select":
            if not isinstance(choices, list) or not choices:
                raise self.fail(f"{where} select field needs a non-empty options list")
            values: set[str] = set()
            for option_index, choice in enumerate(choices):
                if not isinstance(choice, dict) or set(choice) != {"label", "value"}:
                    raise self.fail(
                        f"{where}.options[{option_index}] must contain exactly label and value"
                    )
                require_text(choice["label"], f"{self.name} {where} option label")
                value = require_text(choice["value"], f"{self.name} {where} option value")
                if value in values:
                    raise self.fail(f"{where} option values must be unique")
                values.add(value)
        elif choices is not None:
            raise self.fail(f"{where} text field cannot declare options")
        if "pattern" in row_field:
            if kind != "text":
                raise self.fail(f"{where}.pattern is available only on text fields")
            pattern = row_field["pattern"]
            if not isinstance(pattern, str):
                raise self.fail(f"{where}.pattern must be a string")
            try:
                re.compile(pattern)
            except re.error as error:
                raise self.fail(
                    f"{where}.pattern is not a valid regular expression: {error}"
                ) from error

        prop = self.properties[property_name]
        if json_type(prop, self.definitions) != "array":
            raise self.fail(f"{where}.property {property_name!r} must be an array")
        item = self.resolve_schema(prop.get("items", {}))
        key_value = row_field.get("key")
        if key_value is None:
            if json_type(item, self.definitions) != "string":
                raise self.fail(f"{where} unkeyed property must be an array of strings")
            return property_name, None
        key = require_text(key_value, f"{self.name} {where}.key")
        if not self.vocabulary.property_name.fullmatch(key):
            raise self.fail(f"{where}.key must be a plain property-style name")
        if json_type(item, self.definitions) != "object":
            raise self.fail(f"{where} keyed property must be an array of objects")
        item_properties = item.get("properties", {})
        if (
            key not in item_properties
            or json_type(item_properties[key], self.definitions) != "string"
        ):
            raise self.fail(f"{where}.key {key!r} must name a string property of each item")
        return property_name, key

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
            return []
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
        return [site_field]

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
        """Return whether a property is a string or an array of strings."""
        kind = json_type(prop, self.definitions)
        if kind == "array":
            return json_type(prop.get("items", {}), self.definitions) == "string"
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
                options["siteRequired"],
            )
            scope = options["filterScope"]
            if scope in seen and seen[scope][1] != signature:
                raise self.fail(
                    f"device fields {seen[scope][0]!r} and {name!r} share filterScope {scope!r} "
                    "but differ in siteField, filters, or siteRequired"
                )
            seen.setdefault(scope, (name, signature))

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


def _plain(value: Any, where: str) -> Any:
    """Return a deep JSON copy of a declaration; reject non-JSON values."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise WorkflowFormContractError(f"{where} has a non-string key")
        return {key: _plain(item, f"{where}.{key}") for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item, where) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return copy.copy(value)
    raise WorkflowFormContractError(f"{where} holds a non-JSON value {value!r}")


__all__ = [
    "ENRICHED_API_OPTIONS_CAPABILITY",
    "FIELD_CAPABILITIES",
    "HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY",
    "IMPLICIT_SCOPE_PREFIX",
    "QUERY_ALIASES",
    "RJSF_UI_SCHEMA_ATTRIBUTE",
    "UI_SCHEMA_VERSION",
    "build_form",
    "capability_manifest",
    "declared_ui_schema",
    "derive_requires",
    "supported_capabilities",
    "wire_schema",
]
