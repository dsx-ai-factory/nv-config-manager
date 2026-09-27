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
"""Temporal payload models for operating-system image activities."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class GetCurrentOSInput(BaseModel):
    """Input for getting current OS version."""

    device_data: NetworkDeviceData


class GetCurrentOSOutput(BaseModel):
    """Output for getting current OS version."""

    running_os: str


class GetOSImageVersionsInput(BaseModel):
    """Input for getting firmware versions."""

    device_id: str


class GetOSImageVersionsOutput(BaseModel):
    """Output for getting firmware versions."""

    intended_firmware: str
    desired_firmware: str
    ztp_ipv4_address: str


class UpdateIntendedOSImageInput(BaseModel):
    """Input for updating intended firmware."""

    device_id: str
    desired_firmware: str


class ExecuteZTPInput(BaseModel):
    """Input for executing ZTP."""

    device_data: NetworkDeviceData


class ExecuteZTPOutput(BaseModel):
    """Output for executing ZTP."""

    start_time: str


class PollImageInput(BaseModel):
    """Input for polling device image."""

    device_data: NetworkDeviceData
    expected_image: str


class PollImageOutput(BaseModel):
    """Output for image polling."""

    running_image: str | None = None


class PollZTPStatusInput(BaseModel):
    """Input for polling ZTP status."""

    device_data: NetworkDeviceData
    timeout_minutes: int = 30
    ztp_execution_timestamp: str | None = None


class PollZTPStatusOutput(BaseModel):
    """Output for ZTP status polling."""

    success: bool


class WaitRebootInput(BaseModel):
    """Input for waiting for reboot."""

    device_data: NetworkDeviceData
    ztp_execution_timestamp: str
    timeout: int = 10


class WaitRebootOutput(BaseModel):
    """Output for waiting for reboot."""

    success: bool


class GetMlnxOSVersionInput(BaseModel):
    """Input for getting Mellanox OS version."""

    device_data: NetworkDeviceData


class GetMlnxOSVersionOutput(BaseModel):
    """Output for getting Mellanox OS version."""

    current_os_versions: list[str]


class DownloadMlnxOSInput(BaseModel):
    """Input for downloading Mellanox OS."""

    device_data: NetworkDeviceData
    ztp_ipv4_address: str
    intended_version: str


class DownloadMlnxOSOutput(BaseModel):
    """Output for downloading Mellanox OS."""

    download_status: str
    image_name: str


class InstallMlnxOSInput(BaseModel):
    """Input for installing MLNX OS."""

    device_data: NetworkDeviceData
    image_name: str


class InstallMlnxOSOutput(BaseModel):
    """Install MLNX OS Output."""

    install_status: str


class ReloadMlnxOSInput(BaseModel):
    """Input for reloading MLNX OS."""

    device_data: NetworkDeviceData


class ReloadMlnxOSOutput(BaseModel):
    """Output for reloading MLNX OS."""

    save_config_status: str
    reload_status: str
    is_online: bool


class CleanupMlnxOSInput(BaseModel):
    """Input for cleaning up MLNX OS images."""

    device_data: NetworkDeviceData
    image_name: str


class CleanupMlnxOSOutput(BaseModel):
    """Output for cleaning up MLNX OS images."""

    cleanup_status: str


__all__ = [
    "CleanupMlnxOSInput",
    "CleanupMlnxOSOutput",
    "DownloadMlnxOSInput",
    "DownloadMlnxOSOutput",
    "ExecuteZTPInput",
    "ExecuteZTPOutput",
    "GetCurrentOSInput",
    "GetCurrentOSOutput",
    "GetMlnxOSVersionInput",
    "GetMlnxOSVersionOutput",
    "GetOSImageVersionsInput",
    "GetOSImageVersionsOutput",
    "InstallMlnxOSInput",
    "InstallMlnxOSOutput",
    "PollImageInput",
    "PollImageOutput",
    "PollZTPStatusInput",
    "PollZTPStatusOutput",
    "ReloadMlnxOSInput",
    "ReloadMlnxOSOutput",
    "UpdateIntendedOSImageInput",
    "WaitRebootInput",
    "WaitRebootOutput",
]
