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
"""Compatibility exports for package-owned hardware validation activities."""

from nv_config_manager_workflows.activities.hardware_validation import (
    HARDWARE_VALIDATION_ACTIVITIES,
    CompleteHardwareValidationOutput,
    CreateConsolidatedExcelInput,
    CreateConsolidatedExcelOutput,
    CreateExcelInput,
    CreateExcelOutput,
    HardwareValidationInput,
    HardwareValidationOutput,
    HardwareValidationResult,
    NetworkDeviceData,
    create_consolidated_excel_export,
    create_excel_export,
    get_platform,
    get_platform_environment_fan,
    get_platform_environment_led,
    get_platform_environment_psu,
    get_platform_environment_voltage,
    get_platform_inventory,
)

__all__ = [
    "HARDWARE_VALIDATION_ACTIVITIES",
    "CompleteHardwareValidationOutput",
    "CreateConsolidatedExcelInput",
    "CreateConsolidatedExcelOutput",
    "CreateExcelInput",
    "CreateExcelOutput",
    "HardwareValidationInput",
    "HardwareValidationOutput",
    "HardwareValidationResult",
    "NetworkDeviceData",
    "create_consolidated_excel_export",
    "create_excel_export",
    "get_platform",
    "get_platform_environment_fan",
    "get_platform_environment_led",
    "get_platform_environment_psu",
    "get_platform_environment_voltage",
    "get_platform_inventory",
]
