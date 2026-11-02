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
"""Canonical resources, vocabulary, and capabilities for workflow-form v1."""

import json
import pkgutil
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, cast

from nv_config_manager_workflows.ui.form.errors import WorkflowFormContractError

UI_SCHEMA_VERSION = 1

RJSF_UI_SCHEMA_ATTRIBUTE = "rjsf_ui_schema"

FIELD_CAPABILITIES = {
    "apiOptions": "core-field.api-options.v1",
    "device": "core-field.device.v1",
    "location": "core-field.location.v1",
    "variantRows": "core-field.variant-rows.v1",
}

ENRICHED_API_OPTIONS_CAPABILITY = "core-field.api-options.enriched.v1"

API_OPTIONS_WAITING_TEXT_CAPABILITY = "core-field.api-options.waiting-text.v1"

HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY = "theme.hide-schema-descriptions.v1"

HELP_VARIANT_CAPABILITY = "theme.help-variant.v1"

EXCLUSIVE_GROUPS_CAPABILITY = "interaction.exclusive-groups.v1"

QUERY_SEPARATOR_CAPABILITY = "prefill.query-separator.v1"

QUERY_COERCION_CAPABILITY = "prefill.query-coercion.v1"

SELECTED_OPTION_DEPENDENCY_CAPABILITY = "option-source.selected-option.v1"

FIELD_COMPARISON_CAPABILITY = "validation.field-comparison.v1"


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
    return _load_json("resources/workflow-form-v1.schema.json")


@cache
def capability_manifest() -> dict[str, Any]:
    """Return the canonical v1 capability manifest the UI supports; treat it as read-only."""
    return _load_json("resources/workflow-form-v1.capabilities.json")


def supported_capabilities() -> frozenset[str]:
    """Return the capabilities a v1 UI of the same release supports."""
    return frozenset(capability_manifest()["capabilities"])


@dataclass(frozen=True)
class Vocabulary:
    """The keys and values the wire schema allows, read from it once."""

    defs: dict[str, Any]
    root_keys: frozenset[str]
    field_keys: frozenset[str]
    help_variants: frozenset[str]
    widgets: frozenset[str]
    core_fields: frozenset[str]
    source_keys: frozenset[str]
    device_filters: frozenset[str]
    author_option_keys: dict[str | None, frozenset[str]]
    property_name: re.Pattern[str]


@cache
def vocabulary() -> Vocabulary:
    defs = wire_schema()["$defs"]
    field_properties = defs["fieldUiSchema"]["properties"]
    return Vocabulary(
        defs=defs,
        root_keys=frozenset(defs["uiSchema"]["properties"]),
        field_keys=frozenset(field_properties),
        help_variants=frozenset(field_properties["ui:helpVariant"]["enum"]),
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
            None: frozenset(defs["standardFieldOptions"]["properties"]) - {"queryCoercion"},
        },
        property_name=re.compile(defs["propertyName"]["pattern"]),
    )


def derive_requires(ui_schema: Mapping[str, Any]) -> list[str]:
    """Return the sorted capabilities a validated ``ui_schema`` uses.

    Raises:
        WorkflowFormContractError: A derived capability is missing from the
            capability manifest, so the UI of this release could not render it.
    """
    required: set[str] = set()
    for key, entry in ui_schema.items():
        if key.startswith("ui:"):
            continue
        if entry.get("ui:helpVariant") is not None:
            required.add(HELP_VARIANT_CAPABILITY)
        if entry.get("ui:options", {}).get("queryCoercion"):
            required.add(QUERY_COERCION_CAPABILITY)
        if "ui:field" not in entry:
            continue
        field = entry["ui:field"]
        required.add(FIELD_CAPABILITIES[field])
        if field == "apiOptions" and entry["ui:options"]["source"].get("response") == "options-v1":
            required.add(ENRICHED_API_OPTIONS_CAPABILITY)
        if field == "apiOptions" and entry["ui:options"].get("waitingText") is not None:
            required.add(API_OPTIONS_WAITING_TEXT_CAPABILITY)
        if any(
            dependency.get("option_key") is not None
            for dependency in entry["ui:options"].get("source", {}).get("depends_on", {}).values()
        ):
            required.add(SELECTED_OPTION_DEPENDENCY_CAPABILITY)
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


__all__ = [
    "API_OPTIONS_WAITING_TEXT_CAPABILITY",
    "ENRICHED_API_OPTIONS_CAPABILITY",
    "EXCLUSIVE_GROUPS_CAPABILITY",
    "FIELD_COMPARISON_CAPABILITY",
    "FIELD_CAPABILITIES",
    "HELP_VARIANT_CAPABILITY",
    "HIDE_SCHEMA_DESCRIPTIONS_CAPABILITY",
    "QUERY_COERCION_CAPABILITY",
    "QUERY_SEPARATOR_CAPABILITY",
    "SELECTED_OPTION_DEPENDENCY_CAPABILITY",
    "RJSF_UI_SCHEMA_ATTRIBUTE",
    "UI_SCHEMA_VERSION",
    "Vocabulary",
    "capability_manifest",
    "derive_requires",
    "supported_capabilities",
    "vocabulary",
    "wire_schema",
]
