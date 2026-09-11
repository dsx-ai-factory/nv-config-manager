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
"""Platform dispatch and construction from explicit device settings."""

from __future__ import annotations

from typing import Protocol, assert_never

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager_workflows.clients.device.arista import AristaConnection
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.cumulus import CumulusConnection, NVOSConnection
from nv_config_manager_workflows.clients.device.juniper import JuniperConnection
from nv_config_manager_workflows.clients.device.mellanox import MellanoxConnection
from nv_config_manager_workflows.clients.device.mock import MockNetworkConnection
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings


class DeviceConnectionClass(Protocol):
    """Platform constructors provide their own default port."""

    __name__: str

    def __call__(self, host: str, *, settings: DeviceConnectionSettings) -> NetworkConnection: ...


def connection_class_for_platform(platform: Platform, *, mock: bool) -> DeviceConnectionClass:
    """Select the implementation, including legacy mock and unsupported behavior."""
    if mock:
        return MockNetworkConnection
    match platform:
        case Platform.ARISTA_EOS:
            return AristaConnection
        case Platform.CUMULUS_LINUX:
            return CumulusConnection
        case Platform.NV_OS:
            return NVOSConnection
        case Platform.MLNX_OS:
            return MellanoxConnection
        case Platform.JUNIPER_JUNOS:
            return JuniperConnection
        case Platform.UFM:
            raise NotImplementedError(
                f"No NetworkConnection for platform {platform}; use UFMClient"
            )
        case _ as unreachable:
            assert_never(unreachable)


def from_device_data(
    device_data: NetworkDeviceData, settings: DeviceConnectionSettings
) -> NetworkConnection:
    """Construct a platform connection with already-resolved credentials."""
    connection_cls = connection_class_for_platform(device_data.platform, mock=settings["mock"])
    return connection_cls(device_data.host, settings=settings)
