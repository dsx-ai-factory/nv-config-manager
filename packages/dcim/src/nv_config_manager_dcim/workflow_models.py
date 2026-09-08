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
"""Provider-neutral inventory models required by workflow services."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, NamedTuple, assert_never

from pydantic import BaseModel, computed_field

from nv_config_manager_dcim.errors import DCIMInvalidDataError


class Platform(StrEnum):
    """Network device platform types understood by the workflow services."""

    ARISTA_EOS = "arista-eos"
    CUMULUS_LINUX = "cumulus-linux"
    NV_OS = "nv-os"
    MLNX_OS = "mlnx-os"
    JUNIPER_JUNOS = "juniper-junos"
    UFM = "ufm"


class OSImageVersions(NamedTuple):
    """Firmware intent and the address used to perform ZTP."""

    intended_firmware: str
    desired_firmware: str
    ztp_address: str


class DeviceBayData(BaseModel):
    """A host device bay and its installed child device, if present."""

    name: str
    id: str
    installed_device_id: str | None


class InterfaceData(BaseModel):
    """Normalized device interface data."""

    name: str
    id: str
    host: str
    mac_address: str | None
    vrf_id: str | None


class DeviceData(BaseModel):
    """Normalized common device inventory used by operational workflows."""

    id: str
    name: str
    rack: str | None = None
    position: int | None = None
    role: str
    site: str
    device_type: str

    markdown_fields: ClassVar[list[str]] = ["name", "role", "site", "rack", "position"]


class NetworkDeviceData(DeviceData):
    """Normalized network-device inventory and configuration intent."""

    platform: Platform
    primary_ip4: str | None
    primary_ip6: str | None
    intent: dict[str, Any] | None = None
    render_enabled: bool = False
    deploy_enabled: bool = False
    backup_enabled: bool = False
    ztp_enabled: bool = False

    markdown_fields: ClassVar[list[str]] = ["name", "platform", "role", "site", "rack", "position"]

    @computed_field  # type: ignore[misc]
    @property
    def intended_config_file(self) -> str:
        match self.platform:
            case Platform.ARISTA_EOS | Platform.MLNX_OS | Platform.JUNIPER_JUNOS:
                return "full-config"
            case Platform.CUMULUS_LINUX | Platform.NV_OS:
                return "startup.yaml"
            case Platform.UFM:
                return ""
            case _ as unreachable:
                assert_never(unreachable)

    @computed_field  # type: ignore[misc]
    @property
    def intended_config_path(self) -> str:
        return f"{self.id}/{self.intended_config_file}"

    @computed_field  # type: ignore[misc]
    @property
    def backup_path(self) -> str:
        return self.intended_config_path

    @computed_field  # type: ignore[misc]
    @property
    def backup_file(self) -> str:
        return self.intended_config_file

    @computed_field  # type: ignore[misc]
    @property
    def tenant_config_file(self) -> str:
        return "tenant.yaml" if self.platform == Platform.CUMULUS_LINUX else ""

    @computed_field  # type: ignore[misc]
    @property
    def tenant_config_path(self) -> str:
        return f"{self.id}/{self.tenant_config_file}" if self.tenant_config_file else ""

    @computed_field  # type: ignore[misc]
    @property
    def host(self) -> str:
        host = self.primary_ip4 or self.primary_ip6
        if host is None:
            raise DCIMInvalidDataError(f"No primary IP set for {self.name}")
        return host


class HostDeviceData(DeviceData):
    """Normalized host-device inventory."""

    serial: str | None
    device_bays: list[DeviceBayData]
    interfaces: list[InterfaceData]


class DeviceInventoryFilter(BaseModel):
    """Provider-neutral criteria for selecting device inventory records."""

    site: str | None = None
    roles: list[str] | None = None
    statuses: list[str] | None = None
    tenant: str | None = None
    device_type_ids: list[str] | None = None
    mac_addresses: list[str] | None = None
    device_ids: list[str] | None = None
    platforms: list[Platform] | None = None
    managed_only: bool | None = None
    render_enabled: bool | None = None
    deploy_enabled: bool | None = None
    backup_enabled: bool | None = None
    ztp_enabled: bool | None = None
