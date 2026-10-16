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
"""Input and output models for hardware-validation activities."""

from typing import Any

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class HardwareValidationInput(BaseModel):
    """Hardware validation activity input."""

    device_data: NetworkDeviceData


class HardwareValidationOutput(BaseModel):
    """Hardware validation activity output for all API calls."""

    info: dict


class CompleteHardwareValidationOutput(BaseModel):
    """Complete hardware validation output containing all collected data."""

    device: NetworkDeviceData
    platform: dict
    fan: dict
    led: dict
    psu: dict
    voltage: dict
    inventory: dict


class HardwareValidationResult(BaseModel):
    """Simple hardware validation result."""

    success: bool
    devices_validated: int
    total_entries: int
    message: str


class CreateExcelInput(BaseModel):
    """Input for Excel generation activity."""

    command_name: str
    devices_data_and_results: dict[str, dict[str, Any]]


class CreateExcelOutput(BaseModel):
    """Output for Excel generation activity."""

    excel_data: str
    row_count: int


class CreateConsolidatedExcelInput(BaseModel):
    """Input for consolidated Excel generation with multiple worksheets."""

    stage_data: dict[str, dict[str, dict[str, Any]]]


class CreateConsolidatedExcelOutput(BaseModel):
    """Output for consolidated Excel generation."""

    excel_data: str
    total_row_count: int
    worksheet_counts: dict[str, int]


__all__ = [
    "CompleteHardwareValidationOutput",
    "CreateConsolidatedExcelInput",
    "CreateConsolidatedExcelOutput",
    "CreateExcelInput",
    "CreateExcelOutput",
    "HardwareValidationInput",
    "HardwareValidationOutput",
    "HardwareValidationResult",
    "NetworkDeviceData",
]
