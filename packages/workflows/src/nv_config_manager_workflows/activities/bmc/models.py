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
"""Input and output models for BMC activities."""

from nv_config_manager_dcim.workflow_models import HostDeviceData
from pydantic import BaseModel

from nv_config_manager_workflows.clients.device.models import DeviceArpTable
from nv_config_manager_workflows.clients.redfish.models import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishServer,
    RedfishVendor,
)


class DiscoverHostsInput(BaseModel):
    """Discover hosts input."""

    ip_range_start: str
    ip_range_end: str
    ips_excluded: list[str]
    port: int
    timeout: int = 5


class DiscoverHostsOutput(BaseModel):
    """Discover hosts output."""

    hosts: list[RedfishHost] = []


class PopulateRedfishMacsInput(BaseModel):
    """Populate Redfish MACs input."""

    hosts: list[RedfishHost]
    arp_tables: list[DeviceArpTable]


class PopulateRedfishMacsOutput(BaseModel):
    """Populate Redfish MACs output."""

    hosts: list[RedfishHost]


class RedfishHostInput(BaseModel):
    """Redfish host input."""

    host: RedfishHost


class RedfishHostOutput(BaseModel):
    """Redfish host input."""

    host: RedfishHost | None


class GetServerDetailsActivityInput(BaseModel):
    """Get Server Details activity input."""

    host: RedfishHost
    nic_manufacturers: list[str]


class GetServerDetailsActivityOutput(BaseModel):
    """Get Server Details activity output."""

    server: RedfishServer


class GetDpuDetailsActivityInput(BaseModel):
    """Get DPU Details activity input."""

    host: RedfishHost


class GetDpuDetailsActivityOutput(BaseModel):
    """Get DPU Details activity output."""

    dpu: RedfishDpu


class UpdateDpuDataActivityInput(BaseModel):
    """Update DPU data activity input."""

    server: RedfishServer


class UpdateDpuDataActivityOutput(BaseModel):
    """Update DPU data activity output."""

    device_data: list[HostDeviceData]


__all__ = [
    "DeviceArpTable",
    "DiscoverHostsInput",
    "DiscoverHostsOutput",
    "GetDpuDetailsActivityInput",
    "GetDpuDetailsActivityOutput",
    "GetServerDetailsActivityInput",
    "GetServerDetailsActivityOutput",
    "HostDeviceData",
    "PopulateRedfishMacsInput",
    "PopulateRedfishMacsOutput",
    "RedfishDpu",
    "RedfishDpuPort",
    "RedfishHost",
    "RedfishHostInput",
    "RedfishHostOutput",
    "RedfishServer",
    "RedfishVendor",
    "UpdateDpuDataActivityInput",
    "UpdateDpuDataActivityOutput",
]
