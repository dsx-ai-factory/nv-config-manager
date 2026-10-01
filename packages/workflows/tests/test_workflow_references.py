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
"""Workflow input reference contract tests."""

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from nv_config_manager_workflows.workflow_references import (
    DEVICE_REFERENCE,
    DEVICE_REFERENCES,
    LOCATION_REFERENCE,
    MAX_DEVICE_REFERENCES,
    DeviceReference,
    DeviceReferences,
    LocationReference,
    WorkflowReference,
    WorkflowReferenceKind,
    validate_device_value,
    validate_location_reference,
)


class ReferenceInput(BaseModel):
    device: DeviceReference
    devices: DeviceReferences
    location: LocationReference


def test_reference_metadata_remains_attached_to_annotated_types() -> None:
    assert DEVICE_REFERENCE in get_args(DeviceReference)
    assert DEVICE_REFERENCES in get_args(DeviceReferences)
    assert LOCATION_REFERENCE in get_args(LocationReference)
    assert ReferenceInput.model_fields["device"].metadata == [DEVICE_REFERENCE]
    assert ReferenceInput.model_fields["location"].metadata == [LOCATION_REFERENCE]


def test_reference_metadata_is_frozen() -> None:
    reference = WorkflowReference(WorkflowReferenceKind.DEVICE, validate_device_value)

    with pytest.raises(FrozenInstanceError):
        setattr(reference, "many", True)


def test_device_validation_preserves_provider_owned_identifiers() -> None:
    assert validate_device_value("provider-device-17") == "provider-device-17"

    with pytest.raises(ValueError, match="Preloaded device objects"):
        validate_device_value({"id": "provider-device-17"})


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_location_validation_rejects_empty_values(value: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_location_reference(value)


def test_location_validation_preserves_the_original_value() -> None:
    assert validate_location_reference(" rdu ") == " rdu "


def test_device_reference_collection_retains_its_size_limit() -> None:
    with pytest.raises(ValidationError, match=str(MAX_DEVICE_REFERENCES)):
        ReferenceInput(
            device="leaf-1",
            devices=["leaf"] * (MAX_DEVICE_REFERENCES + 1),
            location="rdu",
        )
