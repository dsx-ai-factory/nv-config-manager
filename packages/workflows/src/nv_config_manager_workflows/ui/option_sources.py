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
"""Option-source declarations, canonical device-filter sources, and validation."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
from typing import Any, Literal

from nv_config_manager_workflows.ui.errors import WorkflowFormContractError

type ScalarValue = str | int | float | bool

type OptionParamValue = ScalarValue | Sequence[ScalarValue]

type DeviceFilter = Literal["site", "tenant", "status"]

type OptionResponse = Literal["options-v1"]

PLACEHOLDER_SEGMENT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

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
    ``label_key``, its submitted value under ``value_key``, and its optional
    location type under ``type_key``. The latter is used by a location field
    with a ``typeField`` and by the device field's Site filter.
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
            wire["params"] = wire_value(self.params)
        if self.depends_on:
            wire["depends_on"] = wire_value(self.depends_on)
        if self.clear_on_change:
            wire["clear_on_change"] = True
        if self.response is not None:
            wire["response"] = self.response
        return wire


SITE_FILTER_SOURCE = OptionSource(
    "/v1/parameter/location",
    "name",
    "id",
    type_key="location_type",
    params={"location_type": ["Site", "Module"]},
)

TENANT_FILTER_SOURCE = OptionSource(
    "/v1/parameter/tenant", "name", "name", params={"managed_only": True}
)

STATUS_FILTER_SOURCE = OptionSource(
    "/v1/parameter/status", "name", "name", params={"content_type": "dcim.device"}
)

DEVICE_FILTER_SOURCES: Mapping[DeviceFilter, OptionSource] = {
    "site": SITE_FILTER_SOURCE,
    "tenant": TENANT_FILTER_SOURCE,
    "status": STATUS_FILTER_SOURCE,
}


def wire_value(value: Any) -> Any:
    """Return option-source values as JSON-compatible wire values."""
    if isinstance(value, OptionSource | Dependency):
        return value.to_wire()
    if isinstance(value, Mapping):
        return {key: wire_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [wire_value(item) for item in value]
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
        if is_scalar(value):
            continue
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            if all(is_scalar(item) for item in value):
                continue
        raise WorkflowFormContractError(
            f"{where}[{name!r}] must be a string, finite number, boolean, or a list of "
            f"them; got {value!r}"
        )


__all__ = [
    "DEVICE_FILTER_SOURCES",
    "PLACEHOLDER_SEGMENT",
    "Dependency",
    "DeviceFilter",
    "OptionParamValue",
    "OptionResponse",
    "OptionSource",
    "SITE_FILTER_SOURCE",
    "STATUS_FILTER_SOURCE",
    "ScalarValue",
    "TENANT_FILTER_SOURCE",
    "check_endpoint",
    "check_params",
    "is_scalar",
    "require_text",
    "wire_value",
]
