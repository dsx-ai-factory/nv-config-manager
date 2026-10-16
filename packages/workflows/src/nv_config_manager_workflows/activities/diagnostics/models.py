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
"""Input and output models for diagnostics activities."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class RunDiagnosticsInput(BaseModel):
    device_data: NetworkDeviceData
    commands: list[str]  # catalog names e.g. ["show_version", "show_bgp_summary"]


class RunDiagnosticsOutput(BaseModel):
    device_name: str
    outputs: dict[str, str]  # command_name -> raw text output (or "ERROR: ..." on failure)


class TechSupportInput(BaseModel):
    device_data: NetworkDeviceData


class TechSupportOutput(BaseModel):
    device_name: str
    redis_key: str = ""  # Redis key where bundle bytes are stored
    download_url: str = ""  # API URL to download the bundle
    cl_support_log: str = ""  # full text output from the cl-support command


class UploadTechSupportFromRedisInput(BaseModel):
    ticketing_platform: str
    issue_key: str
    device_name: str
    redis_key: str  # key used by collect_tech_support_bundle to store the bundle


__all__ = [
    "RunDiagnosticsInput",
    "RunDiagnosticsOutput",
    "TechSupportInput",
    "TechSupportOutput",
    "UploadTechSupportFromRedisInput",
]
