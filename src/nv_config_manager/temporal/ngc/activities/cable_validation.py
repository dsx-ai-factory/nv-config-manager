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
"""Compatibility exports for package-owned cable validation activities."""

from nv_config_manager_workflows.activities.cable_validation import (
    CABLE_STATUS_UPDATE_CONCURRENCY,
    CABLE_VALIDATION_ACTIVITIES,
    CSV_EXCLUDE_COLUMNS,
    DETAIL_SHEET_NAME,
    EXCEL_MIME_TYPE,
    HOST_SUMMARY_COLUMNS,
    HOST_SUMMARY_SHEET_NAME,
    INCORRECT_CABLING_PREFIX,
    LINK_DOWN_MSG,
    LINK_UP_NO_NEIGHBOR_MSG,
    MARKDOWN_EXCLUDE_COLUMNS,
    UNEXPECTED_CONNECTION_MSG,
    CableStatus,
    CableStatusUpdate,
    CableValidationResultData,
    CableValidationRow,
    DecorateResultActivityInput,
    DecorateResultActivityOutput,
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    FormatDeviceValidationResultInput,
    FormatResultsActivityInput,
    InterfaceNeighborData,
    InvalidCable,
    NetworkDeviceData,
    UpdateCableStatusesInput,
    ValidateDeviceNeighborsInput,
    ValidateDeviceNeighborsResult,
    decorate_result,
    format_device_validation_result,
    format_mac,
    format_results,
    is_mac_address,
    update_cable_statuses,
    validate_device_neighbors,
)

__all__ = [
    "CABLE_STATUS_UPDATE_CONCURRENCY",
    "CABLE_VALIDATION_ACTIVITIES",
    "CSV_EXCLUDE_COLUMNS",
    "DETAIL_SHEET_NAME",
    "EXCEL_MIME_TYPE",
    "HOST_SUMMARY_COLUMNS",
    "HOST_SUMMARY_SHEET_NAME",
    "INCORRECT_CABLING_PREFIX",
    "LINK_DOWN_MSG",
    "LINK_UP_NO_NEIGHBOR_MSG",
    "MARKDOWN_EXCLUDE_COLUMNS",
    "UNEXPECTED_CONNECTION_MSG",
    "CableStatus",
    "CableStatusUpdate",
    "CableValidationResultData",
    "CableValidationRow",
    "DecorateResultActivityInput",
    "DecorateResultActivityOutput",
    "DeviceArpTable",
    "DeviceMacTable",
    "DeviceNeighborData",
    "FormatDeviceValidationResultInput",
    "FormatResultsActivityInput",
    "InterfaceNeighborData",
    "InvalidCable",
    "NetworkDeviceData",
    "UpdateCableStatusesInput",
    "ValidateDeviceNeighborsInput",
    "ValidateDeviceNeighborsResult",
    "decorate_result",
    "format_device_validation_result",
    "format_mac",
    "format_results",
    "is_mac_address",
    "update_cable_statuses",
    "validate_device_neighbors",
]
