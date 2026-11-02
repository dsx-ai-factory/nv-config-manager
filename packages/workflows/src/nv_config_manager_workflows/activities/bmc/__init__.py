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
"""Provider-neutral BMC configuration activities."""

from nv_config_manager_workflows.activities.bmc.activities import (
    discover_redfish_hosts,
    factory_reset_bmc,
    get_dpu_details,
    get_server_details,
    populate_redfish_macs,
    power_on_host,
    set_redfish_password,
    update_dpu_data,
)
from nv_config_manager_workflows.activities.bmc.helpers import (
    combine_arp_tables,
    http_get,
)
from nv_config_manager_workflows.activities.bmc.models import (
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
)
from nv_config_manager_workflows.clients.redfish.bluefield import (
    Bluefield3RedfishConnection,
)

BMC_ACTIVITIES = (
    discover_redfish_hosts,
    populate_redfish_macs,
    set_redfish_password,
    power_on_host,
    factory_reset_bmc,
    get_server_details,
    get_dpu_details,
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
