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
"""Service adapters for legacy juniper connections."""

from __future__ import annotations

from typing import Any

from jnpr.junos import Device as Device
from jnpr.junos.utils.config import Config as Config

from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.juniper import (
    JuniperConnection as WorkflowJuniperConnection,
)
from nv_config_manager_workflows.clients.device.juniper import (
    _arp_table_from_junos as _arp_table_from_junos,
)
from nv_config_manager_workflows.clients.device.juniper import _junos_list as _junos_list
from nv_config_manager_workflows.clients.device.juniper import _junos_string as _junos_string
from nv_config_manager_workflows.clients.device.juniper import (
    _mac_entry_from_junos as _mac_entry_from_junos,
)
from nv_config_manager_workflows.clients.device.juniper import (
    _neighbor_from_junos as _neighbor_from_junos,
)


class JuniperConnection(NetworkConnection, WorkflowJuniperConnection):
    """Retain the service constructor while inheriting workflow behavior."""

    DEFAULT_PORT = 830

    @property
    def _device_factory(self) -> Any:
        return Device

    @property
    def _config_factory(self) -> Any:
        return Config
