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
"""Provider-neutral hardware-validation activities and compatibility exports."""

from temporalio import activity

from nv_config_manager_workflows.activities.hardware_validation.helpers import (
    create_consolidated_excel,
    create_excel,
)
from nv_config_manager_workflows.activities.hardware_validation.models import (
    CompleteHardwareValidationOutput,
    CreateConsolidatedExcelInput,
    CreateConsolidatedExcelOutput,
    CreateExcelInput,
    CreateExcelOutput,
    HardwareValidationInput,
    HardwareValidationOutput,
    HardwareValidationResult,
    NetworkDeviceData,
)
from nv_config_manager_workflows.runtime import get_device_connection


@activity.defn
def get_platform(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform())


@activity.defn
def get_platform_environment_fan(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform fan information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform_environment_fan())


@activity.defn
def get_platform_environment_led(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform LED information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform_environment_led())


@activity.defn
def get_platform_environment_psu(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform PSU information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform_environment_psu())


@activity.defn
def get_platform_environment_voltage(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform voltage information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform_environment_voltage())


@activity.defn
def get_platform_inventory(
    activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Get platform inventory information from the device."""
    connection = get_device_connection(activity_input.device_data)
    return HardwareValidationOutput(info=connection.get_platform_inventory())


@activity.defn
def create_excel_export(activity_input: CreateExcelInput) -> CreateExcelOutput:
    """Create Excel export for stage data."""
    return create_excel(activity_input)


@activity.defn
def create_consolidated_excel_export(
    activity_input: CreateConsolidatedExcelInput,
) -> CreateConsolidatedExcelOutput:
    """Create consolidated Excel export with a worksheet for each stage."""
    return create_consolidated_excel(activity_input)


HARDWARE_VALIDATION_ACTIVITIES = (
    get_platform,
    get_platform_environment_fan,
    get_platform_environment_led,
    get_platform_environment_psu,
    get_platform_environment_voltage,
    get_platform_inventory,
    create_excel_export,
    create_consolidated_excel_export,
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
