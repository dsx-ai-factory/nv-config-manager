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
"""Compatibility exports for package-owned BMC activities."""

from nv_config_manager_workflows.activities.bmc import (
    BMC_ACTIVITIES,
    Bluefield3RedfishConnection,
    DeviceArpTable,
    DiscoverHostsInput,
    DiscoverHostsOutput,
    GetDpuDetailsActivityInput,
    GetDpuDetailsActivityOutput,
    GetServerDetailsActivityInput,
    GetServerDetailsActivityOutput,
    HostDeviceData,
    PopulateRedfishMacsInput,
    PopulateRedfishMacsOutput,
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishHostInput,
    RedfishHostOutput,
    RedfishServer,
    RedfishVendor,
    UpdateDpuDataActivityInput,
    UpdateDpuDataActivityOutput,
    combine_arp_tables,
    discover_redfish_hosts,
    factory_reset_bmc,
    get_dpu_details,
    get_server_details,
    http_get,
    populate_redfish_macs,
    power_on_host,
    set_redfish_password,
    update_dpu_data,
)

__all__ = [
    "BMC_ACTIVITIES",
    "Bluefield3RedfishConnection",
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
    "combine_arp_tables",
    "discover_redfish_hosts",
    "factory_reset_bmc",
    "get_dpu_details",
    "get_server_details",
    "http_get",
    "populate_redfish_macs",
    "power_on_host",
    "set_redfish_password",
    "update_dpu_data",
]
