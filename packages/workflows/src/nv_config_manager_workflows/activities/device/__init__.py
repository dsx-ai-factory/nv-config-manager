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
"""Provider-neutral network device activities."""

from nv_config_manager_workflows.activities.device.activities import (
    get_device_actual_neighbors,
    get_device_arp_table,
    get_device_intended_neighbors,
    get_device_mac_table,
    load_neighbor_data_by_switch_port,
    validate_hostname,
)
from nv_config_manager_workflows.activities.device.models import (
    NetworkDeviceData,
    SwitchPortNeighborActivityInput,
    ValidateHostnameActivityOutput,
)

DEVICE_ACTIVITIES = (
    get_device_intended_neighbors,
    get_device_actual_neighbors,
    get_device_mac_table,
    get_device_arp_table,
    validate_hostname,
    load_neighbor_data_by_switch_port,
)

__all__ = [
    "DEVICE_ACTIVITIES",
    "NetworkDeviceData",
    "SwitchPortNeighborActivityInput",
    "ValidateHostnameActivityOutput",
    "get_device_actual_neighbors",
    "get_device_arp_table",
    "get_device_intended_neighbors",
    "get_device_mac_table",
    "load_neighbor_data_by_switch_port",
    "validate_hostname",
]
