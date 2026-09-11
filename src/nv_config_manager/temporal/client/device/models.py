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
"""Compatibility exports for relocated device models."""

from nv_config_manager_workflows.clients.device.models import DeviceArpTable as DeviceArpTable
from nv_config_manager_workflows.clients.device.models import DeviceMacEntry as DeviceMacEntry
from nv_config_manager_workflows.clients.device.models import DeviceMacTable as DeviceMacTable
from nv_config_manager_workflows.clients.device.models import (
    DeviceNeighborData as DeviceNeighborData,
)
from nv_config_manager_workflows.clients.device.models import (
    InterfaceNeighborData as InterfaceNeighborData,
)
from nv_config_manager_workflows.clients.device.models import _append_unique as _append_unique
from nv_config_manager_workflows.clients.device.models import format_mac as format_mac
from nv_config_manager_workflows.clients.device.models import is_mac_address as is_mac_address
