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
"""Construct service device adapters from inventory data."""

from configparser import ConfigParser

from nv_config_manager_dcim.workflow_models import NetworkDeviceData

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.temporal.client.device.arista import AristaConnection
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.client.device.cumulus import CumulusConnection, NVOSConnection
from nv_config_manager.temporal.client.device.juniper import JuniperConnection
from nv_config_manager.temporal.client.device.mellanox import MellanoxConnection
from nv_config_manager.temporal.client.device.mock import MockNetworkConnection
from nv_config_manager_workflows.clients import device as workflow_device
from nv_config_manager_workflows.clients.device.factory import (
    DeviceConnectionClass,
    connection_class_for_platform,
)


def from_device_data(
    device_data: NetworkDeviceData,
    *,
    config: ConfigParser | None = None,
) -> NetworkConnection:
    """Select and construct a vendor adapter using service configuration."""
    resolved = config if config is not None else load_config()
    implementation = connection_class_for_platform(
        device_data.platform, mock=resolved["device"].getboolean("mock", fallback=False)
    )
    adapters: dict[DeviceConnectionClass, type[NetworkConnection]] = {
        workflow_device.AristaConnection: AristaConnection,
        workflow_device.CumulusConnection: CumulusConnection,
        workflow_device.NVOSConnection: NVOSConnection,
        workflow_device.MellanoxConnection: MellanoxConnection,
        workflow_device.JuniperConnection: JuniperConnection,
        workflow_device.MockNetworkConnection: MockNetworkConnection,
    }
    connection_cls = adapters[implementation]
    return connection_cls(device_data.host, site=device_data.site, config=resolved)
