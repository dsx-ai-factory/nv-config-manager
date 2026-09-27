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
"""Input and output models for device password rotation activities."""

from typing import Any

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pydantic import BaseModel


class ValidatePasswordDiffInput(BaseModel):
    """Input for validating password diff."""

    diff: str
    username: str
    platform: str


class ValidatePasswordDiffOutput(BaseModel):
    """Output for validating password diff."""

    is_valid: bool
    invalid_lines: list[str]
    valid_lines: list[str]
    error_message: str | None = None


class GetPasswordMappingsInput(BaseModel):
    """Input for getting password mappings."""

    device: NetworkDeviceData
    username: str


class GetPasswordMappingsOutput(BaseModel):
    """Output for getting password mappings."""

    username: str


class ValidatePlatformSupportInput(BaseModel):
    """Input for validating platform support."""

    platform: Platform


class ValidatePlatformSupportOutput(BaseModel):
    """Output for validating platform support."""

    normalized_platform: str


class FormatPasswordRotationResultsInput(BaseModel):
    """Format password rotation results activity input."""

    successful_devices: dict[str, Any]
    failed_devices: dict[str, Any]
    total_devices: int
    ui_base_url: str


__all__ = [
    "FormatPasswordRotationResultsInput",
    "GetPasswordMappingsInput",
    "GetPasswordMappingsOutput",
    "ValidatePasswordDiffInput",
    "ValidatePasswordDiffOutput",
    "ValidatePlatformSupportInput",
    "ValidatePlatformSupportOutput",
]
