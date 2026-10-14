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
"""Cross-field semantic validation for workflow-form UI schemas."""

import json
from typing import Any, cast

from nv_config_manager_workflows.ui.form.option_sources import require_text
from nv_config_manager_workflows.ui.form.projection import json_type
from nv_config_manager_workflows.ui.form.validation.context import FormValidationContext


def check_hidden(context: FormValidationContext) -> None:
    """Allow a hidden widget only on a location typeField or a property with a default."""
    type_fields = {
        entry["ui:options"]["typeField"]
        for entry in context.fields.values()
        if entry.get("ui:field") == "location" and "typeField" in entry["ui:options"]
    }
    for name, entry in context.fields.items():
        if (
            entry.get("ui:widget") == "hidden"
            and name not in type_fields
            and name not in context.composite_owners
            and "default" not in context.properties[name]
        ):
            raise context.fail(
                f"{name!r} is hidden but is neither a location typeField nor a property "
                "with a projected default; omit it with FormExcluded instead"
            )


def check_scopes(context: FormValidationContext) -> None:
    """Require the device fields sharing a scope to agree on their filters."""
    seen: dict[str, tuple[str, tuple[Any, ...]]] = {}
    for name, entry in context.fields.items():
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
            raise context.fail(
                f"device fields {seen[scope][0]!r} and {name!r} share filterScope {scope!r} "
                "but differ in siteField, filters, filterSources, or siteRequired"
            )
        seen.setdefault(scope, (name, signature))


def check_exclusive_groups(context: FormValidationContext) -> None:
    """Validate mutually exclusive fields and device-filter owners."""
    groups = context.ui.get("ui:globalOptions", {}).get("exclusiveGroups")
    if groups is None:
        return
    if not isinstance(groups, list) or len(groups) < 2:
        raise context.fail("ui:globalOptions.exclusiveGroups must contain at least two groups")
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
            raise context.fail(f"{where} must contain fields and/or deviceFilters")
        group_mapping = cast(dict[str, object], group)
        if group_mapping.get("requireComplete") is True:
            complete_groups += 1
        elif "requireComplete" in group_mapping:
            raise context.fail(f"{where}.requireComplete must be true")
        fields = group_mapping.get("fields", [])
        device_filters = group_mapping.get("deviceFilters", [])
        if (
            not isinstance(fields, list)
            or not isinstance(device_filters, list)
            or not fields
            and not device_filters
        ):
            raise context.fail(f"{where} must contain a non-empty fields or deviceFilters list")
        if len(set(map(str, fields))) != len(fields):
            raise context.fail(f"{where}.fields must be unique")
        if len(set(map(str, device_filters))) != len(device_filters):
            raise context.fail(f"{where}.deviceFilters must be unique")
        for field in fields:
            name = context.require_property(field, f"{where}.fields", wire_name=True)
            if name in used_fields:
                raise context.fail(f"exclusive group field {name!r} belongs to more than one group")
            used_fields.add(name)
        for device_filter in device_filters:
            name = context.require_property(device_filter, f"{where}.deviceFilters", wire_name=True)
            if name not in fields:
                raise context.fail(
                    f"{where}.deviceFilters entry {name!r} must also appear in fields"
                )
            if context.fields.get(name, {}).get("ui:field") != "device":
                raise context.fail(f"{where}.deviceFilters entry {name!r} must be a device field")
            if name in used_device_filters:
                raise context.fail(
                    f"exclusive group device filter {name!r} belongs to more than one group"
                )
            used_device_filters.add(name)
    if complete_groups not in {0, len(groups)}:
        raise context.fail("exclusiveGroups requireComplete must be enabled on every group or none")


def check_field_comparisons(context: FormValidationContext) -> None:
    """Validate declarative comparisons between projected numeric fields."""
    comparisons = context.ui.get("ui:globalOptions", {}).get("fieldComparisons")
    if comparisons is None:
        return
    if not isinstance(comparisons, list) or not comparisons:
        raise context.fail("ui:globalOptions.fieldComparisons must be a non-empty list")
    for index, comparison in enumerate(comparisons):
        where = f"ui:globalOptions.fieldComparisons[{index}]"
        if not isinstance(comparison, dict) or set(comparison) != {
            "left",
            "operator",
            "right",
            "message",
        }:
            raise context.fail(f"{where} must contain left, operator, right, and message")
        comparison_mapping = cast(dict[str, object], comparison)
        left = context.require_property(comparison_mapping["left"], f"{where}.left", wire_name=True)
        right = context.require_property(
            comparison_mapping["right"], f"{where}.right", wire_name=True
        )
        if left == right:
            raise context.fail(f"{where} must compare two different fields")
        for side, name in (("left", left), ("right", right)):
            if json_type(context.properties[name], context.definitions) not in {
                "integer",
                "number",
            }:
                raise context.fail(f"{where}.{side} {name!r} must be numeric")
        if comparison_mapping["operator"] != "lessThan":
            raise context.fail(f"{where}.operator must be 'lessThan'")
        require_text(comparison_mapping["message"], f"{context.name} {where}.message")


def check_cycles(context: FormValidationContext) -> None:
    """Reject an option dependency on the field itself, directly or transitively."""
    finished: set[str] = set()

    def visit(name: str, path: list[str]) -> None:
        if name in path:
            cycle = " -> ".join([*path[path.index(name) :], name])
            raise context.fail(f"option dependencies form a cycle: {cycle}")
        if name in finished:
            return
        for target in context.dependencies.get(name, ()):
            visit(target, [*path, name])
        finished.add(name)

    for name in context.dependencies:
        visit(name, [])


def check_url_owners(context: FormValidationContext) -> None:
    """Allow at most one prefill owner per URL query parameter."""
    owners: dict[str, set[str]] = {}

    def own(parameter: str, owner: str) -> None:
        owners.setdefault(parameter, set()).add(owner)

    for name in context.properties:
        entry = context.fields.get(name, {})
        if entry.get("ui:widget") == "hidden" or entry.get("ui:readonly") is True:
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
            raise context.fail(
                f"URL parameter {parameter!r} has more than one prefill owner: "
                f"{', '.join(sorted(claimants))}"
            )


__all__ = [
    "check_cycles",
    "check_exclusive_groups",
    "check_field_comparisons",
    "check_hidden",
    "check_scopes",
    "check_url_owners",
]
