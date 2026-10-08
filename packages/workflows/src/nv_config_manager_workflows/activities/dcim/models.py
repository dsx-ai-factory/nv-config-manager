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
"""Input and output models for provider-neutral DCIM activities."""

from __future__ import annotations

from typing import Any, ClassVar

from nv_config_manager_dcim.models import DCIMLocationIdentifier, DeviceVRF, SpectrumXVRF
from nv_config_manager_dcim.workflow_models import (
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    Platform,
)
from pydantic import BaseModel, computed_field

# Compatibility import for workflow callers while they migrate to DeviceVRF.
DeviceVrfInfo = DeviceVRF


class GetNetworkDeviceInput(BaseModel):
    """Get network device input."""

    device_id: str


class GetNetworkDeviceOutput(BaseModel):
    """Get network device output."""

    device: NetworkDeviceData


class GetHostDeviceInput(BaseModel):
    """Get host device input."""

    device_id: str


class GetHostDeviceOutput(BaseModel):
    """Get host device output."""

    device: HostDeviceData


class GetNetworkDevicesInput(BaseModel):
    """Get network devices input."""

    site: DCIMLocationIdentifier | None = None
    roles: list[str] | None = None
    status: list[str] | None = None
    tenant: str | None = None
    device_type_ids: list[str] | None = None
    mac_addresses: list[str] | None = None
    device_ids: list[str] | None = None
    render_enabled: bool | None = None
    deploy_enabled: bool | None = None
    backup_enabled: bool | None = None
    ztp_enabled: bool | None = None
    managed_only: bool | None = None
    platforms: list[Platform] | None = None


class GetNetworkDevicesOutput(BaseModel):
    """Get network devices output."""

    devices: list[NetworkDeviceData]


class GetHostDevicesInput(BaseModel):
    """Get host devices input."""

    site: DCIMLocationIdentifier | None = None
    roles: list[str] | None = None
    status: list[str] | None = None
    tenant: str | None = None
    device_type_ids: list[str] | None = None
    mac_addresses: list[str] | None = None


class GetHostDevicesOutput(BaseModel):
    """Get host devices output."""

    devices: list[HostDeviceData]


class HostInterface(BaseModel):
    """Host Interface Data."""

    name: str
    mac: str


class HostData(BaseModel):
    """Host Data."""

    interfaces: list[HostInterface]
    name: str
    tenant: str
    device_id: str
    url: str
    alias: str | None = None


class GetAvailableRouteDistinguishersInput(BaseModel):
    """Get Available Route Distinguishers Activity Input."""

    site: DCIMLocationIdentifier
    namespace_tag: str
    rd_min: int
    rd_max: int


class GetAvailableRouteDistinguishersOutput(BaseModel):
    """Get Available Route Distinguishers Activity Output."""

    route_distinguisher: str
    namespaces: list[str]


class ProvisionVrfInput(BaseModel):
    """Provision VRF Activity Input."""

    namespaces: list[str]
    route_distinguisher: str
    overlay_id: str
    site: DCIMLocationIdentifier
    tenant: str


class Vrf(BaseModel):
    """VRF Data."""

    QUERY_BY_IDS: ClassVar[str] = """
query ($ids: [String]!) {
  vrfs(id: $ids) {
    id
    name
    rd
    namespace {
      name
      location {
        name
      }
    }
    interfaces {
      name
      device {
        name
      }
    }
  }
}
"""
    name: str
    namespace: str
    site: str
    id: str
    rd: str
    interfaces: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def interface_count(self) -> int:
        """Count of interfaces tied to this VRF."""
        return len(self.interfaces)

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Vrf:
        """Convert a normalized provider mapping to the activity output."""
        return Vrf(
            name=data["name"],
            namespace=data["namespace"]["name"],
            site=data["namespace"]["location"]["name"],
            id=data["id"],
            rd=data["rd"],
            interfaces=[
                ":".join((intf["device"]["name"], intf["name"])) for intf in data["interfaces"]
            ],
        )

    from_nautobot_graphql = from_mapping

    @staticmethod
    def from_spectrum_x_vrf(vrf: SpectrumXVRF) -> Vrf:
        """Convert a provider-neutral Spectrum-X VRF to the activity output."""
        return Vrf(
            name=vrf.name,
            namespace=vrf.namespace,
            site=vrf.site,
            id=vrf.vrf_id,
            rd=vrf.route_distinguisher,
            interfaces=list(vrf.interfaces),
        )


class QueryVRFByVPCInput(BaseModel):
    """Query VRF Activity Input."""

    overlay_id: str
    site: DCIMLocationIdentifier
    namespace_tag: str
    namespace: str | None = None


class VrfDeletionActivityInput(BaseModel):
    """VRF Deletion Activity Input."""

    vrf_id: str
    vnid: int


class DeleteOverlayInput(BaseModel):
    """Delete Overlay Activity Input."""

    overlay_id: str
    site: DCIMLocationIdentifier


class DeleteOverlayOutput(BaseModel):
    """Delete Overlay Activity Output."""

    deleted: bool
    overlay_name: str


class SwitchPortByMacActivityInput(BaseModel):
    """Switch Port by MAC Address Input."""

    remote_mac_address: str


class SwitchPortByMacActivityOutput(BaseModel):
    """Switch Port Output."""

    device: NetworkDeviceData
    interface: str


class CheckRecordedConfigDriftInput(BaseModel):
    """Check Recorded Config Drift Input."""

    device_id: str


class GetDeviceVrfsInput(BaseModel):
    """Get Device VRFs Input."""

    device_id: str


class GetDeviceVrfsOutput(BaseModel):
    """Get Device VRFs Output."""

    vrfs: list[DeviceVRF]


class AssignVrfToDeviceInput(BaseModel):
    """Assign VRF to Device Input."""

    device_id: str
    vrf_id: str


class GetDeviceInterfacesInput(BaseModel):
    """Get Device Interfaces Input."""

    device_id: str
    interface_names: list[str] | None = None


class GetDeviceInterfacesOutput(BaseModel):
    """Get Device Interfaces Output."""

    interfaces: list[InterfaceData]


class AssignVrfToInterfaceInput(BaseModel):
    """Set the VRF assigned to an interface."""

    interface_id: str
    vrf_id: str | None


class ReconcileSpXOverlayAssignmentsInput(BaseModel):
    """Spectrum-X overlay assignments to reconcile in the configured DCIM."""

    overlay_id: str | None
    site: DCIMLocationIdentifier
    device_id: str
    interface_ids: list[str]
    device_interface_ids: list[str]


class ReconcileSpXOverlayAssignmentsOutput(BaseModel):
    """Result of reconciling Spectrum-X overlay assignments."""

    created: int
    removed: int
    reconciliation_changed: bool = False


class RemoveUnmappedDeviceVrfsInput(BaseModel):
    """Device whose VRF associations should be reconciled with its interfaces."""

    device_id: str
    vrf_ids: list[str]


class RemoveUnmappedDeviceVrfsOutput(BaseModel):
    """VRF associations removed because no device interface used them."""

    removed_vrf_ids: list[str]


__all__ = [
    "AssignVrfToDeviceInput",
    "AssignVrfToInterfaceInput",
    "CheckRecordedConfigDriftInput",
    "DeleteOverlayInput",
    "DeleteOverlayOutput",
    "DeviceVRF",
    "DeviceVrfInfo",
    "GetAvailableRouteDistinguishersInput",
    "GetAvailableRouteDistinguishersOutput",
    "GetDeviceInterfacesInput",
    "GetDeviceInterfacesOutput",
    "GetDeviceVrfsInput",
    "GetDeviceVrfsOutput",
    "GetHostDeviceInput",
    "GetHostDeviceOutput",
    "GetHostDevicesInput",
    "GetHostDevicesOutput",
    "GetNetworkDeviceInput",
    "GetNetworkDeviceOutput",
    "GetNetworkDevicesInput",
    "GetNetworkDevicesOutput",
    "HostData",
    "HostInterface",
    "ProvisionVrfInput",
    "QueryVRFByVPCInput",
    "ReconcileSpXOverlayAssignmentsInput",
    "ReconcileSpXOverlayAssignmentsOutput",
    "RemoveUnmappedDeviceVrfsInput",
    "RemoveUnmappedDeviceVrfsOutput",
    "SwitchPortByMacActivityInput",
    "SwitchPortByMacActivityOutput",
    "Vrf",
    "VrfDeletionActivityInput",
]
