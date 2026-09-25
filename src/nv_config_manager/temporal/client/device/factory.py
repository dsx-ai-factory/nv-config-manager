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
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

# isort: off
from nv_config_manager_workflows.clients.device import (
    AristaConnection as _AristaConnection,
    CumulusConnection as _CumulusConnection,
    JuniperConnection as _JuniperConnection,
    MellanoxConnection as _MellanoxConnection,
    MockNetworkConnection as _MockNetworkConnection,
    NVOSConnection as _NVOSConnection,
    connection_class_for_platform,
)
# isort: on


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

    # Import locally to avoid a cycle through device/__init__.py while this
    # factory resolves workflow classes to patch-compatible service adapters.
    from nv_config_manager.temporal.client import device as service_clients

    service_classes: dict[object, type[NetworkConnection]] = {
        _AristaConnection: service_clients.AristaConnection,
        _CumulusConnection: service_clients.CumulusConnection,
        _NVOSConnection: service_clients.NVOSConnection,
        _JuniperConnection: service_clients.JuniperConnection,
        _MellanoxConnection: service_clients.MellanoxConnection,
        _MockNetworkConnection: service_clients.MockNetworkConnection,
    }
    connection_class = service_classes[workflow_class]
    return connection_class(
        device_data.host,
        site=device_data.site,
        config=resolved_config,
    )
