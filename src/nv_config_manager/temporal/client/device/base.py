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
"""Service adapter for legacy network-connection construction."""

from __future__ import annotations

from collections.abc import Callable
from configparser import ConfigParser
from typing import ClassVar, cast

from nv_config_manager_dcim.workflow_models import NetworkDeviceData

from nv_config_manager.common.config_loader import load_config
from nv_config_manager.temporal.client import device as device_clients
from nv_config_manager.temporal.factories.device import device_connection_settings
from nv_config_manager_workflows.clients.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS as COMMIT_CONFIRM_ROLLBACK_SECONDS,
)
from nv_config_manager_workflows.clients.device.base import (
    NetworkConnection as WorkflowNetworkConnection,
)
from nv_config_manager_workflows.clients.device.factory import connection_class_for_platform
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings


def legacy_settings(
    username: str | None,
    password: str | None,
    site: str | None,
) -> DeviceConnectionSettings:
    """Resolve the legacy constructor's credentials from service configuration."""
    return device_connection_settings(
        load_config(), username=username, password=password, site=site, mock=False
    )


class NetworkConnection(WorkflowNetworkConnection):
    """Resolve service settings before cooperative vendor initialization.

    Vendor adapters put this class before their workflow implementation in
    the MRO. Their DEFAULT_PORT applies when port is omitted or None.
    """

    DEFAULT_PORT: ClassVar[int | None] = None

    def __init__(
        self,
        host: str,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        *,
        site: str | None = None,
    ) -> None:
        resolved_port = self.DEFAULT_PORT if port is None else port
        if resolved_port is None:
            raise TypeError("NetworkConnection requires a port")
        settings = legacy_settings(username, password, site)
        super().__init__(host, resolved_port, settings=settings)

    @staticmethod
    def from_device_data(
        device_data: NetworkDeviceData,
        *,
        config: ConfigParser | None = None,
    ) -> NetworkConnection:
        """Construct a service adapter using service-owned configuration."""
        resolved = config if config is not None else load_config()
        implementation = connection_class_for_platform(
            device_data.platform, mock=resolved["device"].getboolean("mock", fallback=False)
        )

        connection_cls = cast(
            Callable[..., NetworkConnection], getattr(device_clients, implementation.__name__)
        )
        return connection_cls(device_data.host, site=device_data.site)
