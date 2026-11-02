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
"""Compatibility exports for package-owned NVLink firmware activities."""

from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    NVLINKSWITCH_FIRMWARE_ACTIVITIES,
    CompareRunningDesiredInput,
    CompareRunningDesiredOutput,
    GetRunningFirmwareInput,
    GetRunningFirmwareOutput,
    RebootDeviceInput,
    RebootDeviceOutput,
    UpdateDeviceContextInput,
    ValidateRenderTargetsInput,
    ValidateTargetFilesInput,
    compare_running_desired,
    get_running_firmware,
    reboot_device,
    update_device_context,
    validate_render_targets,
    validate_target_files,
)

__all__ = [
    "NVLINKSWITCH_FIRMWARE_ACTIVITIES",
    "CompareRunningDesiredInput",
    "CompareRunningDesiredOutput",
    "GetRunningFirmwareInput",
    "GetRunningFirmwareOutput",
    "RebootDeviceInput",
    "RebootDeviceOutput",
    "UpdateDeviceContextInput",
    "ValidateRenderTargetsInput",
    "ValidateTargetFilesInput",
    "compare_running_desired",
    "get_running_firmware",
    "reboot_device",
    "update_device_context",
    "validate_render_targets",
    "validate_target_files",
]
