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
"""Models shared by Redfish client implementations."""

from __future__ import annotations

from enum import StrEnum

import netaddr
from pydantic import BaseModel, field_validator


class RedfishVendor(StrEnum):
    """Vendors supported by the Redfish connection layer."""

    LENOVO = "Lenovo"
    BLUEFIELD = "Nvidia"
    DELL = "Dell"


class RedfishHost(BaseModel, validate_assignment=True):
    """A host exposing a Redfish endpoint."""

    address: str
    port: int = 443
    vendor: RedfishVendor
    mac: str | None = None

    def __str__(self) -> str:
        """Return the existing vendor/MAC/address representation."""
        return f"{self.vendor.value}/{self.mac}/{self.address}:{self.port}"

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address formatting."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpuPort(BaseModel):
    """A Redfish DPU port."""

    name: str
    mac: str | None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address formatting."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishDpu(RedfishHost, validate_assignment=True):
    """A DPU discovered through Redfish."""

    ports: list[RedfishDpuPort]
    base_mac: str
    serial: str

    @field_validator("base_mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent base-MAC formatting."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishNic(BaseModel, validate_assignment=True):
    """A network interface discovered through Redfish."""

    name: str
    slot: str
    mac: str | None = None
    dpu: RedfishDpu | None = None

    @field_validator("mac", mode="before")
    @classmethod
    def format_mac(cls, mac: str | None) -> str | None:
        """Ensure consistent MAC address formatting."""
        return str(netaddr.EUI(mac)) if mac else None


class RedfishServer(RedfishHost):
    """A server discovered through Redfish."""

    serial: str
    nics: list[RedfishNic]
