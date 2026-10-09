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
"""Option sources and helpers that build the core-field RJSF declarations.

The helpers return plain RJSF ``uiSchema`` entries for one property. Merge
further standard keys into them, for example
``{**device_field(SOURCE), "ui:title": "Device"}``.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
from typing import Any, Literal

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError

type ScalarValue = str | int | float | bool
"""A JSON scalar other than ``null``: a static query-parameter value."""

type OptionParamValue = ScalarValue | Sequence[ScalarValue]
"""A static query-parameter value; a sequence becomes a repeated parameter."""

type DeviceFilter = Literal["site", "tenant", "status"]
"""A device filter the device core field can render."""

type OptionResponse = Literal["options-v1"]
"""A standard enriched option response envelope."""

type OptionPresentation = Literal["select", "grouped-checkboxes"]
"""A presentation supported by the generic API-options field."""

PLACEHOLDER_SEGMENT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A whole ``{property}`` endpoint path segment."""

_ENDPOINT_FORBIDDEN = frozenset("?#\\")


@dataclass(frozen=True)
class Dependency:
    """A sibling form property whose value supplies one option query parameter.

    A ``required`` dependency holds the option request while the property is
    empty; an optional one is left out of the request instead.
    """

    field: str
    _: KW_ONLY
    required: bool = True

    def to_wire(self) -> dict[str, Any]:
        """Return the v1 wire form, omitting the default ``required``."""
        return {"field": self.field} if self.required else {"field": self.field, "required": False}


@dataclass(frozen=True)
class OptionSource:
    """Where a core field loads its options from.

    ``endpoint`` is a path relative to the workflow API origin; a whole
    ``{property}`` path segment is filled from that sibling property and is a
    required dependency. Each returned row supplies its label under
    ``label_key``, its submitted value under ``value_key``, and, for a location
    field with a ``typeField``, its location type under ``type_key``.
    ``params`` are static query parameters, ``depends_on`` maps a query
    parameter to the :class:`Dependency` supplying its value, and
    ``clear_on_change`` clears the selection when a dependency changes.
    ``response="options-v1"`` selects the enriched option envelope used by
    grouped choices, descriptions, and response metadata.
    """

    endpoint: str
    label_key: str
    value_key: str
    _: KW_ONLY
    type_key: str | None = None
    params: Mapping[str, OptionParamValue] = field(default_factory=dict)
    depends_on: Mapping[str, Dependency] = field(default_factory=dict)
    clear_on_change: bool = False
    response: OptionResponse | None = None

    def to_wire(self) -> dict[str, Any]:
        """Return the v1 wire form, omitting keys that are unset, empty, or false.

        Nothing is validated here, so a malformed source never fails the import
        of the module declaring it; registration checks the wire form instead.
        """
        wire: dict[str, Any] = {
            "endpoint": self.endpoint,
            "label_key": self.label_key,
            "value_key": self.value_key,
        }
        if self.type_key is not None:
            wire["type_key"] = self.type_key
        if self.params:
            wire["params"] = _wire(self.params)
        if self.depends_on:
            wire["depends_on"] = _wire(self.depends_on)
        if self.clear_on_change:
            wire["clear_on_change"] = True
        if self.response is not None:
            wire["response"] = self.response
        return wire


def api_options(
    source: OptionSource,
    *,
    presentation: OptionPresentation | None = None,
    select_all: bool = False,
    show_descriptions: bool = False,
    meta_text: Mapping[str, str] | None = None,
    disable_when_no_matches: bool = False,
) -> dict[str, Any]:
    """Return an ``apiOptions`` field loaded from ``source``.

    The optional display settings apply to the standard ``options-v1``
    response. They deliberately describe presentation only; request execution,
    caching, dependency handling, and rendering remain generic client behavior.
    ``disable_when_no_matches`` disables the picker when response metadata reports
    ``matching_device_count`` as zero.
    """
    options: dict[str, Any] = {"source": _wire(source)}
    if presentation is not None:
        options["presentation"] = presentation
    if select_all:
        options["selectAll"] = True
    if show_descriptions:
        options["showDescriptions"] = True
    if meta_text:
        options["metaText"] = _wire(meta_text)
    if disable_when_no_matches:
        options["disableWhenNoMatches"] = True
    return {"ui:field": "apiOptions", "ui:options": options}


def location_field(source: OptionSource, *, type_field: str | None = None) -> dict[str, Any]:
    """Return a ``location`` field; ``type_field`` names the sibling receiving the type."""
    options: dict[str, Any] = {"source": _wire(source)}
    if type_field is not None:
        options["typeField"] = type_field
    return {"ui:field": "location", "ui:options": options}


def device_field(
    source: OptionSource,
    *,
    filters: Sequence[DeviceFilter] = (),
    site_required: bool = True,
    site_field: str | None = None,
    filter_scope: str | None = None,
    query_param: str | None = "device-id",
) -> dict[str, Any]:
    """Return a ``device`` field.

    ``filters`` selects the Site, Tenant, and Status filters. ``site_field``
    takes Site from that location property instead of a Site filter control.
    Device fields with the same ``filter_scope`` share one filter set; without
    one, registration gives the field the private scope ``implicit:<property>``.
    ``query_param`` pre-fills the selection from the URL; ``None`` disables it.
    """
    options: dict[str, Any] = {
        "source": _wire(source),
        "filters": _wire(filters),
        "siteRequired": site_required,
    }
    if site_field is not None:
        options["siteField"] = site_field
    if filter_scope is not None:
        options["filterScope"] = filter_scope
    if query_param is not None:
        options["queryParam"] = query_param
    return {"ui:field": "device", "ui:options": options}


def variant_rows(
    *,
    owned_properties: Sequence[str],
    modes: Sequence[Mapping[str, Any]],
    minimum_rows: int = 1,
    clear_inactive: bool = True,
    warning: str | None = None,
) -> dict[str, Any]:
    """Return a generic mutually-exclusive repeatable-row declaration.

    A mode's columns with an item property edit one object-array property;
    columns without one edit parallel scalar-array properties. Registration validates the full shape and
    its bindings against the projected form schema.
    """
    options: dict[str, Any] = {
        "ownedProperties": _wire(owned_properties),
        "modes": _wire(modes),
        "minimumRows": minimum_rows,
        "clearInactive": clear_inactive,
    }
    if warning is not None:
        options["warning"] = warning
    return {"ui:field": "variantRows", "ui:options": options}


def _wire(value: Any) -> Any:
    """Return ``value`` with option sources, dependencies, mappings, and sequences in wire form.

    Any other value passes through unchanged for registration to report.
    """
    if isinstance(value, OptionSource | Dependency):
        return value.to_wire()
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_wire(item) for item in value]
    return value


def require_text(value: object, where: str) -> str:
    """Return a string with visible content; reject anything else."""
    if isinstance(value, str) and value.strip():
        return value
    raise WorkflowFormContractError(f"{where} must be a non-empty string; got {value!r}")


def is_scalar(value: object) -> bool:
    """Return whether a value is a string, a boolean, or a finite number."""
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, str | int)


def check_endpoint(endpoint: object, where: str) -> tuple[str, ...]:
    """Reject a malformed endpoint and return the properties its placeholders name."""
    path = require_text(endpoint, where)
    if not path.startswith("/") or path.startswith("//"):
        raise WorkflowFormContractError(f"{where} must start with a single '/'; got {path!r}")
    if any(c in _ENDPOINT_FORBIDDEN or c.isspace() or not c.isprintable() for c in path):
        raise WorkflowFormContractError(
            f"{where} must not contain '?', '#', '\\', whitespace, or control characters; "
            f"got {path!r}; put query parameters in params"
        )
    for segment in path.split("/"):
        if ("{" in segment or "}" in segment) and not PLACEHOLDER_SEGMENT.fullmatch(segment):
            raise WorkflowFormContractError(
                f"{where} placeholders must be whole path segments naming a property, such "
                f"as '/{{device_id}}'; got segment {segment!r} in {path!r}"
            )
    return tuple(dict.fromkeys(PLACEHOLDER_SEGMENT.findall(path)))


def check_params(params: object, where: str) -> None:
    """Reject static query parameters that are not scalars or lists of scalars."""
    if not isinstance(params, Mapping):
        raise WorkflowFormContractError(f"{where} must be a mapping; got {params!r}")
    for name, value in params.items():
        require_text(name, f"{where} key")
        is_list = isinstance(value, Sequence) and not isinstance(value, str | bytes)
        if not (is_scalar(value) or (is_list and all(is_scalar(item) for item in value))):
            raise WorkflowFormContractError(
                f"{where}[{name!r}] must be a string, finite number, boolean, or a list of "
                f"them; got {value!r}"
            )


__all__ = [
    "PLACEHOLDER_SEGMENT",
    "Dependency",
    "DeviceFilter",
    "OptionPresentation",
    "OptionParamValue",
    "OptionResponse",
    "OptionSource",
    "ScalarValue",
    "api_options",
    "check_endpoint",
    "check_params",
    "device_field",
    "is_scalar",
    "location_field",
    "require_text",
    "variant_rows",
]
