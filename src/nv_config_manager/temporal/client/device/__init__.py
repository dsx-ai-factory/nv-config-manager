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
"""Network Device clients."""

from __future__ import annotations

import logging

import urllib3

from nv_config_manager.temporal.client.device.arista import AristaConnection
from nv_config_manager.temporal.client.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager.temporal.client.device.cumulus import CumulusConnection, NVOSConnection
from nv_config_manager.temporal.client.device.exceptions import (
    ConfigApplyFailureException,
    ConfigSyntaxException,
    DiffChangedException,
    DiffValidationError,
    InvalidConfigException,
    NetworkDeviceException,
)
from nv_config_manager.temporal.client.device.juniper import JuniperConnection
from nv_config_manager.temporal.client.device.mellanox import MellanoxConnection
from nv_config_manager.temporal.client.device.mock import MockNetworkConnection
from nv_config_manager.temporal.client.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
    format_mac,
    is_mac_address,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

logging.getLogger("paramiko").setLevel(logging.WARNING)

# Suppress SSL warnings for network devices which typically use self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

__all__ = [
    "COMMIT_CONFIRM_ROLLBACK_SECONDS",
    "AristaConnection",
    "ConfigApplyFailureException",
    "ConfigSyntaxException",
    "CumulusConnection",
    "DeviceArpTable",
    "DeviceMacEntry",
    "DeviceMacTable",
    "DeviceNeighborData",
    "DiffChangedException",
    "DiffValidationError",
    "InterfaceNeighborData",
    "InvalidConfigException",
    "JuniperConnection",
    "MellanoxConnection",
    "MockNetworkConnection",
    "NVOSConnection",
    "NetworkConnection",
    "NetworkDeviceData",
    "NetworkDeviceException",
    "format_mac",
    "is_mac_address",
]
