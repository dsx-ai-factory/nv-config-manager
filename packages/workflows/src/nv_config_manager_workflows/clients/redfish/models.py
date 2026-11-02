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
"""Redfish host, NIC, and DPU models."""

from enum import StrEnum

import netaddr
from pydantic import BaseModel, field_validator


class RedfishVendor(StrEnum):
    """Vendors for redfish."""

    LENOVO = "Lenovo"
    BLUEFIELD = "Nvidia"
    DELL = "Dell"


class RedfishHost(BaseModel, validate_assignment=True):
    """Hosts for Redfish."""

    address: str
    port: int = 443
    vendor: RedfishVendor
    mac: str | None = None

    def __str__(self) -> str:
        """String formatting."""
        return f"{self.vendor.value}/{self.mac}/{self.address}:{self.port}"

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpuPort(BaseModel):
    """Redfish DPU Port."""

    name: str
    mac: str | None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpu(RedfishHost, validate_assignment=True):
    """Redfish DPU."""

    ports: list[RedfishDpuPort]
    base_mac: str
    serial: str

    @field_validator("base_mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishNic(BaseModel, validate_assignment=True):
    """Redfish NIC."""

    name: str
    slot: str
    mac: str | None = None
    dpu: RedfishDpu | None = None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address format."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishServer(RedfishHost):
    """Redfish Server."""

    serial: str
    nics: list[RedfishNic]
