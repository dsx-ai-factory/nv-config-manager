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
"""Service factory for platform-specific network connections."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.common.config.loader import resolve_config
from nv_config_manager.temporal.client.device.arista import AristaConnection
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.client.device.cisco_xr import CiscoIosXrConnection
from nv_config_manager.temporal.client.device.cumulus import CumulusConnection, NVOSConnection
from nv_config_manager.temporal.client.device.juniper import JuniperConnection
from nv_config_manager.temporal.client.device.mellanox import MellanoxConnection
from nv_config_manager.temporal.client.device.mock import MockNetworkConnection
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager_workflows.clients.device.arista import (
    AristaConnection as _AristaConnection,
)
from nv_config_manager_workflows.clients.device.cisco_xr.connection import (
    CiscoIosXrConnection as _CiscoIosXrConnection,
)

# isort: off
from nv_config_manager_workflows.clients.device.cumulus import (
    CumulusConnection as _CumulusConnection,
    NVOSConnection as _NVOSConnection,
)
# isort: on

from nv_config_manager_workflows.clients.device.factory import (
    connection_class_for_platform,
)
from nv_config_manager_workflows.clients.device.juniper import (
    JuniperConnection as _JuniperConnection,
)
from nv_config_manager_workflows.clients.device.mellanox import (
    MellanoxConnection as _MellanoxConnection,
)
from nv_config_manager_workflows.clients.device.mock import (
    MockNetworkConnection as _MockNetworkConnection,
)


def from_device_data(
    device_data: NetworkDeviceData,
    *,
    config: ConfigParser | None = None,
) -> NetworkConnection:
    """Return a service adapter for an inventoried device."""
    resolved_config = resolve_config(config)
    workflow_class = connection_class_for_platform(
        device_data.platform,
        mock=resolved_config["device"].getboolean("mock", fallback=False),
    )

    service_classes: dict[object, type[NetworkConnection]] = {
        _AristaConnection: AristaConnection,
        _CumulusConnection: CumulusConnection,
        _NVOSConnection: NVOSConnection,
        _JuniperConnection: JuniperConnection,
        _CiscoIosXrConnection: CiscoIosXrConnection,
        _MellanoxConnection: MellanoxConnection,
        _MockNetworkConnection: MockNetworkConnection,
    }
    connection_class = service_classes[workflow_class]
    return connection_class(
        device_data.host,
        site=device_data.site,
        config=resolved_config,
    )
