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
"""Explicit external references accepted by workflow input models."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field

MAX_DEVICE_REFERENCES = 1000
"""Maximum number of device IDs accepted in one workflow submission."""


class WorkflowReferenceKind(StrEnum):
    """External resource kinds resolved before workflow submission."""

    DEVICE = "device"
    LOCATION = "location"


@dataclass(frozen=True)
class WorkflowReference:
    """Metadata describing an explicitly annotated workflow input reference."""

    kind: WorkflowReferenceKind
    validator: Callable[[Any], Any]
    many: bool = False
    enrich_search_attributes: bool = True


def validate_device_value(value: Any) -> Any:
    """Reject preloaded API objects while preserving provider-owned identifiers."""
    if not isinstance(value, str):
        raise ValueError("Preloaded device objects are not accepted by the Workflow API")
    return value


def validate_location_reference(value: str) -> str:
    """Reject empty location references while preserving their original value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Location reference must not be empty")
    return value


DEVICE_REFERENCE = WorkflowReference(
    WorkflowReferenceKind.DEVICE,
    validator=validate_device_value,
)
DEVICE_REFERENCES = WorkflowReference(
    WorkflowReferenceKind.DEVICE,
    validator=validate_device_value,
    many=True,
    enrich_search_attributes=False,
)
LOCATION_REFERENCE = WorkflowReference(
    WorkflowReferenceKind.LOCATION,
    validator=validate_location_reference,
)


DeviceReference = Annotated[
    str,
    DEVICE_REFERENCE,
]
OptionalDeviceReference = Annotated[
    str | None,
    DEVICE_REFERENCE,
]
DeviceReferences = Annotated[
    list[str],
    Field(max_length=MAX_DEVICE_REFERENCES),
    DEVICE_REFERENCES,
]
LocationReference = Annotated[
    str,
    LOCATION_REFERENCE,
]
OptionalLocationReference = Annotated[
    str | None,
    LOCATION_REFERENCE,
]
