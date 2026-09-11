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
"""Service adapters for legacy mellanox connections."""

from __future__ import annotations

from typing import Any

from netmiko import ConnectHandler as ConnectHandler  # type: ignore[import-untyped]
from netmiko.base_connection import BaseConnection  # type: ignore[import-untyped]

from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.mellanox import (
    MellanoxConnection as WorkflowMellanoxConnection,
)


class MellanoxConnection(NetworkConnection, WorkflowMellanoxConnection):
    """Retain the service constructor while inheriting workflow behavior."""

    DEFAULT_PORT = 22

    def _ssh_connect(self, **kwargs: Any) -> BaseConnection:
        return ConnectHandler(**kwargs)
