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
"""Helpers that build the core-field RJSF declarations.

The helpers return plain RJSF ``uiSchema`` entries for one property. Merge
further standard keys into them, for example
``{**device_field(SOURCE), "ui:title": "Device"}``.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from nv_config_manager_workflows.ui.option_sources import (
    DEVICE_FILTER_SOURCES,
    DeviceFilter,
    OptionSource,
    wire_value,
)

type OptionPresentation = Literal["select", "grouped-checkboxes"]
"""A presentation supported by the generic API-options field."""


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
    options: dict[str, Any] = {"source": wire_value(source)}
    if presentation is not None:
        options["presentation"] = presentation
    if select_all:
        options["selectAll"] = True
    if show_descriptions:
        options["showDescriptions"] = True
    if meta_text:
        options["metaText"] = wire_value(meta_text)
    if disable_when_no_matches:
        options["disableWhenNoMatches"] = True
    return {"ui:field": "apiOptions", "ui:options": options}


def location_field(source: OptionSource, *, type_field: str | None = None) -> dict[str, Any]:
    """Return a ``location`` field; ``type_field`` names the sibling receiving the type."""
    options: dict[str, Any] = {"source": wire_value(source)}
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

    ``filters`` selects the Site, Tenant, and Status filters and adds their
    canonical backend-owned option sources. ``site_field`` takes Site from that
    location property instead of a Site filter control. Device fields with the
    same ``filter_scope`` share one filter set; without one, registration gives
    the field the private scope ``implicit:<property>``. ``query_param``
    pre-fills the selection from the URL; ``None`` disables it.
    """
    options: dict[str, Any] = {
        "source": wire_value(source),
        "filters": wire_value(filters),
        "filterSources": {
            name: wire_value(filter_source)
            for name, filter_source in DEVICE_FILTER_SOURCES.items()
            if name in filters
        },
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
        "ownedProperties": wire_value(owned_properties),
        "modes": wire_value(modes),
        "minimumRows": minimum_rows,
        "clearInactive": clear_inactive,
    }
    if warning is not None:
        options["warning"] = warning
    return {"ui:field": "variantRows", "ui:options": options}


__all__ = [
    "DeviceFilter",
    "OptionPresentation",
    "api_options",
    "device_field",
    "location_field",
    "variant_rows",
]
