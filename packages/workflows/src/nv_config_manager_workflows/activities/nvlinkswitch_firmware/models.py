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
"""Temporal payload models for NVLink switch firmware activities."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class GetRunningFirmwareInput(BaseModel):
    """Input for getting running firmware versions."""

    device_data: NetworkDeviceData


class GetRunningFirmwareOutput(BaseModel):
    """Output for getting running firmware versions."""

    running_firmware: dict[str, str]


class CompareRunningDesiredInput(BaseModel):
    """Input for comparing running vs desired versions."""

    device_data: NetworkDeviceData
    running_os: str
    running_firmware: dict[str, str]
    bundle_version: str


class CompareRunningDesiredOutput(BaseModel):
    """Output for comparing running vs desired versions."""

    upgrade_needed: bool
    desired_os: str
    desired_firmware: dict[str, str]
    differences: dict[str, dict[str, str]]


class UpdateDeviceContextInput(BaseModel):
    """Input for updating device context."""

    device_data: NetworkDeviceData
    bundle_version: str


class ValidateRenderTargetsInput(BaseModel):
    """Input for validating render targets."""

    device_data: NetworkDeviceData
    desired_firmware: dict[str, str]


class ValidateTargetFilesInput(BaseModel):
    """Input for validating target files on ZTP server."""

    device_data: NetworkDeviceData
    desired_firmware: dict[str, str]


class RebootDeviceInput(BaseModel):
    """Input for rebooting device."""

    device_data: NetworkDeviceData


class RebootDeviceOutput(BaseModel):
    """Output for rebooting device."""

    start_time: str


__all__ = [
    "CompareRunningDesiredInput",
    "CompareRunningDesiredOutput",
    "GetRunningFirmwareInput",
    "GetRunningFirmwareOutput",
    "RebootDeviceInput",
    "RebootDeviceOutput",
    "UpdateDeviceContextInput",
    "ValidateRenderTargetsInput",
    "ValidateTargetFilesInput",
]
