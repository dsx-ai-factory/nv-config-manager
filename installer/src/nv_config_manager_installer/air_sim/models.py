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
"""Data classes for nvcm-air-simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nv_config_manager_installer.air_sim.constants import (
    DEFAULT_NVCM_SERVER_NAME,
    NVCM_SERVER_CPU,
    NVCM_SERVER_MEMORY,
    NVCM_SERVER_OS,
    NVCM_SERVER_STORAGE,
)


@dataclass
class DeviceInfo:
    """Information about a device from the site export."""

    name: str
    platform: str
    role: str
    model: str
    firmware_version: str
    interfaces: list[str] = field(default_factory=list)
    interface_macs: dict[str, str] = field(default_factory=dict)
    serial: str = ""
    nvcm_enabled: bool = False
    air_config: dict[str, Any] = field(default_factory=dict)
    air_enabled: bool = True


@dataclass
class CableConnection:
    """A cable connection between two interfaces."""

    source_device: str
    source_interface: str
    dest_device: str
    dest_interface: str


@dataclass
class NVCMServerConfig:
    """Configuration for the NVCM server in the simulation.

    Can either:
    1. Use an existing server from the simulation (use_existing_server=name)
    2. Create a new node and attach it to a switch
    """

    # Option 1: Use existing server from simulation (e.g., "oob-mgmt-server")
    use_existing_server: str | None = None

    # Option 2: Create new node attached to a switch
    attach_switch: str | None = None  # Name of the switch to attach to
    attach_interface: str | None = None  # Interface on the switch to connect to
    server_interface: str = "eth0"  # Interface on the nvcm server for the connection
    cpu: int = NVCM_SERVER_CPU
    memory: int = NVCM_SERVER_MEMORY
    storage: int = NVCM_SERVER_STORAGE
    os: str = NVCM_SERVER_OS

    # Common settings
    metallb_ip_range: str = "192.168.200.100-192.168.200.110"  # MetalLB IP pool
    nvcm_size: str = "small"  # T-shirt size for NVCM deployment

    @property
    def server_name(self) -> str:
        """Get the server name (existing or new)."""
        if self.use_existing_server:
            return self.use_existing_server
        return DEFAULT_NVCM_SERVER_NAME

    @property
    def creates_new_node(self) -> bool:
        """Whether this config creates a new node vs using existing."""
        return self.use_existing_server is None
