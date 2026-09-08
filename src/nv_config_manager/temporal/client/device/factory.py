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
"""Factory for platform-specific network connections."""

from __future__ import annotations

from collections.abc import Callable
from typing import assert_never

from nv_config_manager.common.config import load_config
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform


def from_device_data(device_data: NetworkDeviceData) -> NetworkConnection:
    """Return a NetworkConnection for a given device."""
    # Avoid circular import with device/__init__.py. Resolve classes from the
    # public package so unittest patches of device.CumulusConnection still apply.
    from nv_config_manager.temporal.client import device as device_clients

    config = load_config()
    connection_cls: Callable[..., NetworkConnection]
    if config["device"].getboolean("mock", fallback=False):
        connection_cls = device_clients.MockNetworkConnection
    else:
        match device_data.platform:
            case Platform.ARISTA_EOS:
                connection_cls = device_clients.AristaConnection
            case Platform.CUMULUS_LINUX:
                connection_cls = device_clients.CumulusConnection
            case Platform.NV_OS:
                connection_cls = device_clients.NVOSConnection
            case Platform.MLNX_OS:
                connection_cls = device_clients.MellanoxConnection
            case Platform.JUNIPER_JUNOS:
                connection_cls = device_clients.JuniperConnection
            case Platform.UFM:
                raise NotImplementedError(
                    f"No NetworkConnection for platform {device_data.platform}; use UFMClient"
                )
            case _ as unreachable:
                assert_never(unreachable)
    return connection_cls(device_data.host, site=device_data.site)
