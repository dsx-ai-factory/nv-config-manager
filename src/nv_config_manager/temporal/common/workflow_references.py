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
"""Deprecated import path for workflow reference declarations.

Import from :mod:`nv_config_manager_workflows.workflow_references` in new code.
"""

from nv_config_manager_workflows.workflow_references import (
    DEVICE_REFERENCE,
    DEVICE_REFERENCES,
    LOCATION_REFERENCE,
    MAX_DEVICE_REFERENCES,
    DeviceReference,
    DeviceReferences,
    LocationReference,
    OptionalDeviceReference,
    OptionalLocationReference,
    WorkflowReference,
    WorkflowReferenceKind,
    validate_device_value,
    validate_location_reference,
)

__all__ = [
    "DEVICE_REFERENCE",
    "DEVICE_REFERENCES",
    "LOCATION_REFERENCE",
    "MAX_DEVICE_REFERENCES",
    "DeviceReference",
    "DeviceReferences",
    "LocationReference",
    "OptionalDeviceReference",
    "OptionalLocationReference",
    "WorkflowReference",
    "WorkflowReferenceKind",
    "validate_device_value",
    "validate_location_reference",
]
