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
"""Compatibility exports for package-owned operating-system activities."""

from nv_config_manager_workflows.activities.os import (
    OS_ACTIVITIES,
    CleanupMlnxOSInput,
    CleanupMlnxOSOutput,
    DownloadMlnxOSInput,
    DownloadMlnxOSOutput,
    ExecuteZTPInput,
    ExecuteZTPOutput,
    GetCurrentOSInput,
    GetCurrentOSOutput,
    GetMlnxOSVersionInput,
    GetMlnxOSVersionOutput,
    GetOSImageVersionsInput,
    GetOSImageVersionsOutput,
    InstallMlnxOSInput,
    InstallMlnxOSOutput,
    PollImageInput,
    PollImageOutput,
    PollZTPStatusInput,
    PollZTPStatusOutput,
    ReloadMlnxOSInput,
    ReloadMlnxOSOutput,
    UpdateIntendedOSImageInput,
    WaitRebootInput,
    WaitRebootOutput,
    cleanup_mlnx_os,
    download_mlnx_os,
    execute_ztp,
    get_current_os,
    get_mlnx_os_version,
    get_os_image_versions,
    install_mlnx_os,
    poll_image,
    poll_ztp_status,
    reload_mlnx_os,
    update_intended_os_image,
    wait_reboot,
)

__all__ = [
    "OS_ACTIVITIES",
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
    "cleanup_mlnx_os",
    "download_mlnx_os",
    "execute_ztp",
    "get_current_os",
    "get_mlnx_os_version",
    "get_os_image_versions",
    "install_mlnx_os",
    "poll_image",
    "poll_ztp_status",
    "reload_mlnx_os",
    "update_intended_os_image",
    "wait_reboot",
]
