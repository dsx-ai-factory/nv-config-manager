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
"""Provider-neutral DCIM activities."""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

import netaddr
from nv_config_manager_dcim import DeviceInventoryFilter, ZTPDevice
from pydantic import BaseModel, computed_field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMError, DeviceVRF, SpectrumXVRF, create_dcim_client
from nv_config_manager.temporal.common.mixins.device import (
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
    Platform,
)

logger = get_logger(__name__, category=LogCategory.DCIM)
logger.setLevel(logging.INFO)

# Compatibility import for workflow callers while they migrate to DeviceVRF.
DeviceVrfInfo = DeviceVRF


def _as_application_error(error: DCIMError) -> ApplicationError:
    """Translate provider-neutral errors at the Temporal service boundary."""
    return ApplicationError(str(error), non_retryable=bool(getattr(error, "non_retryable", False)))


def _vni_from_rd(route_distinguisher: str) -> int:
    """Derive the VNI from a route distinguisher of the form ``*:<vni>``."""
    parts = route_distinguisher.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(f"Invalid route distinguisher {route_distinguisher!r}, expected '*:<vni>'")
    return int(parts[1])


class GetNetworkDeviceInput(BaseModel):
    """Get network device input."""

    device_id: str


class GetNetworkDeviceOutput(BaseModel):
    """Get network device output."""

    device: NetworkDeviceData


@activity.defn
async def get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    """Get network device data."""
    client = create_dcim_client()
    async with client:
        device = await client.get_network_device(activity_input.device_id)
    return GetNetworkDeviceOutput(device=device)


class GetZTPDeviceInput(BaseModel):
    """Get certificate and ZTP endpoint intent for one device."""

    device_id: str


class GetZTPDeviceOutput(BaseModel):
    """Normalized ZTP intent for one device."""

    device: ZTPDevice


@activity.defn
async def get_ztp_device(activity_input: GetZTPDeviceInput) -> GetZTPDeviceOutput:
    """Get provider-neutral ZTP intent used by certificate rotation."""
    client = create_dcim_client()
    async with client:
        device = await client.get_ztp_device(activity_input.device_id)
    return GetZTPDeviceOutput(device=device)


class GetHostDeviceInput(BaseModel):
    """Get host device input."""

    device_id: str


class GetHostDeviceOutput(BaseModel):
    """Get host device output."""

    device: HostDeviceData


@activity.defn
async def get_host_device(
    activity_input: GetHostDeviceInput,
) -> GetHostDeviceOutput:
    """Get host device data."""
    client = create_dcim_client()
    async with client:
        device = await client.get_host_device(activity_input.device_id)
    return GetHostDeviceOutput(device=device)


class GetNetworkDevicesInput(BaseModel):
    """Get network devices input."""

    site: str | None = None
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


@activity.defn
async def get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Get network devices for a specific site."""
    client = create_dcim_client()
    async with client:
        devices = await client.get_network_devices(
            DeviceInventoryFilter(
                site=activity_input.site,
                roles=activity_input.roles,
                statuses=activity_input.status,
                tenant=activity_input.tenant,
                device_type_ids=activity_input.device_type_ids,
                mac_addresses=activity_input.mac_addresses,
                device_ids=activity_input.device_ids,
                render_enabled=activity_input.render_enabled,
                deploy_enabled=activity_input.deploy_enabled,
                backup_enabled=activity_input.backup_enabled,
                ztp_enabled=activity_input.ztp_enabled,
                managed_only=activity_input.managed_only,
                platforms=activity_input.platforms,
            )
        )
    return GetNetworkDevicesOutput(devices=devices)


class GetHostDevicesInput(BaseModel):
    """Get host devices input."""

    site: str | None = None
    roles: list[str] | None = None
    status: list[str] | None = None
    tenant: str | None = None
    device_type_ids: list[str] | None = None
    mac_addresses: list[str] | None = None


class GetHostDevicesOutput(BaseModel):
    """Get host devices output."""

    devices: list[HostDeviceData]


@activity.defn
async def get_host_devices(
    activity_input: GetHostDevicesInput,
) -> GetHostDevicesOutput:
    """Get host devices."""
    client = create_dcim_client()
    async with client:
        devices = await client.get_host_devices(
            DeviceInventoryFilter(
                site=activity_input.site,
                roles=activity_input.roles,
                statuses=activity_input.status,
                tenant=activity_input.tenant,
                device_type_ids=activity_input.device_type_ids,
                mac_addresses=activity_input.mac_addresses,
            )
        )
    return GetHostDevicesOutput(devices=devices)


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


@activity.defn
async def get_host_data_by_macs(mac_addresses: list[str]) -> list[HostData]:
    """Load host data from list of mac addresses."""
    client = create_dcim_client()
    async with client:
        hosts = await client.get_host_metadata_by_macs(mac_addresses)
    return [
        HostData(
            interfaces=[
                HostInterface(name=interface.name, mac=str(netaddr.EUI(interface.mac_address)))
                for interface in host.interfaces
            ],
            name=host.name,
            tenant=host.tenant,
            device_id=host.device_id,
            alias=host.alias,
            url=client.get_device_ui_url(host.device_id),
        )
        for host in hosts
    ]


@activity.defn
async def get_host_data_by_names(device_names: list[str]) -> list[HostData]:
    """Load host data from list of device names."""
    client = create_dcim_client()
    async with client:
        hosts = await client.get_host_metadata_by_names(device_names)
    return [
        HostData(
            interfaces=[],
            name=host.name,
            tenant=host.tenant,
            device_id=host.device_id,
            alias=host.alias,
            url=client.get_device_ui_url(host.device_id),
        )
        for host in hosts
    ]


class GetAvailableRouteDistinguishersInput(BaseModel):
    """Get Available Route Distinguishers Activity Input."""

    site: str
    namespace_tag: str
    rd_min: int
    rd_max: int


class GetAvailableRouteDistinguishersOutput(BaseModel):
    """Get Available Route Distinguishers Activity Output."""

    route_distinguisher: str
    namespaces: list[str]


@activity.defn
async def get_available_route_distinguishers(
    activity_input: GetAvailableRouteDistinguishersInput,
) -> GetAvailableRouteDistinguishersOutput:
    """Get Available Route Distinguishers Activity."""
    client = create_dcim_client()
    async with client:
        namespaces = await client.get_namespace_route_distinguishers(
            activity_input.site, activity_input.namespace_tag
        )
    namespace_ids = [namespace.namespace_id for namespace in namespaces]
    if not namespace_ids:
        raise ApplicationError(
            f"No namespaces for site {activity_input.site} and tag {activity_input.namespace_tag}."
        )
    logger.info("Found namespaces: %s", namespace_ids)

    route_distinguishers = {
        route_distinguisher
        for namespace in namespaces
        for route_distinguisher in namespace.route_distinguishers
    }
    logger.info("Found RDs: %s", route_distinguishers)

    assigned_numbers = {
        int(rd.split(":")[1]) for rd in route_distinguishers if re.match(r"\*:\d+", rd)
    }

    available_numbers = (
        set(range(activity_input.rd_min, activity_input.rd_max + 1)) - assigned_numbers
    )
    if not available_numbers:
        raise ApplicationError(f"Namespaces {namespace_ids} out of space for new RDs")
    route_distinguisher = f"*:{min(available_numbers)}"
    return GetAvailableRouteDistinguishersOutput(
        route_distinguisher=route_distinguisher,
        namespaces=namespace_ids,
    )


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


class ProvisionVrfInput(BaseModel):
    """Provision VRF Activity Input."""

    namespaces: list[str]
    route_distinguisher: str
    overlay_id: str
    site: str
    tenant: str


@activity.defn
async def provision_vrf(
    activity_input: ProvisionVrfInput,
) -> None:
    """Provision the SpectrumX overlay, VRFs, and L3 VXLANs for a VPC.

    Finds or creates the (location-scoped) SpectrumX overlay, then creates one VRF
    and one L3 VXLAN per namespace, binding each VRF and VXLAN to the overlay.
    Resources created in this call are rolled back on failure; the overlay is left
    in place since it is found-or-created and may be shared.
    """
    vni = _vni_from_rd(activity_input.route_distinguisher)
    client = create_dcim_client()
    try:
        async with client:
            await client.provision_spectrum_x_vrf(
                activity_input.namespaces,
                activity_input.route_distinguisher,
                vni,
                activity_input.overlay_id,
                activity_input.site,
                activity_input.tenant,
            )
    except DCIMError as error:
        raise _as_application_error(error) from error


class QueryVRFByVPCInput(BaseModel):
    """Query VRF Activity Input."""

    overlay_id: str
    site: str
    namespace_tag: str
    namespace: str | None = None


@activity.defn
async def get_vrfs_by_overlay_id(activity_input: QueryVRFByVPCInput) -> list[Vrf] | None:
    """Get VRFs for an overlay by looking up overlay → vxlans → VRF IDs → GraphQL."""
    client = create_dcim_client()
    async with client:
        spectrum_x_vrfs = await client.get_spectrum_x_vrfs(
            activity_input.overlay_id,
            activity_input.site,
            activity_input.namespace,
        )
    vrfs = [Vrf.from_spectrum_x_vrf(vrf) for vrf in spectrum_x_vrfs]
    return vrfs if vrfs else None


class VrfDeletionActivityInput(BaseModel):
    """VRF Deletion Activity Input."""

    vrf_id: str
    vnid: int


@activity.defn
async def delete_vrf(activity_input: VrfDeletionActivityInput) -> None:
    """Delete a VRF, its overlay assignments, and the L3 VXLAN bound to it.

    VXLANs are fetched by VNI then filtered to those whose vrf.id matches
    vrf_id (the VRF FK is SET_NULL on VRF deletion, so they are removed
    explicitly to keep the overlay clean).
    """
    client = create_dcim_client()
    async with client:
        await client.delete_spectrum_x_vrf(activity_input.vrf_id, activity_input.vnid)


class DeleteOverlayInput(BaseModel):
    """Delete Overlay Activity Input."""

    overlay_id: str
    site: str


class DeleteOverlayOutput(BaseModel):
    """Delete Overlay Activity Output."""

    deleted: bool
    overlay_name: str


@activity.defn
async def delete_overlay(activity_input: DeleteOverlayInput) -> DeleteOverlayOutput:
    """Delete the SpectrumX overlay and its assignments if no VXLANs remain."""
    overlay_name = activity_input.overlay_id
    client = create_dcim_client()
    async with client:
        deleted = await client.delete_spectrum_x_overlay_if_unused(
            overlay_name, activity_input.site
        )
        if not deleted:
            logger.info("Overlay %s still has VXLANs, leaving in place", overlay_name)
    return DeleteOverlayOutput(deleted=deleted, overlay_name=overlay_name)


class SwitchPortByMacActivityInput(BaseModel):
    """Switch Port by MAC Address Input."""

    remote_mac_address: str


class SwitchPortByMacActivityOutput(BaseModel):
    """Switch Port Output."""

    device: NetworkDeviceData
    interface: str


@activity.defn
async def get_switch_port_by_remote_mac_address(
    activity_input: SwitchPortByMacActivityInput,
) -> SwitchPortByMacActivityOutput:
    """Get Switch Port by Remote MAC Address."""
    client = create_dcim_client()
    async with client:
        device, interface = await client.get_connected_switch_port_by_remote_mac(
            activity_input.remote_mac_address
        )
    return SwitchPortByMacActivityOutput(device=device, interface=interface)


class CheckRecordedConfigDriftInput(BaseModel):
    """Check Recorded Config Drift Input."""

    device_id: str


@activity.defn
async def check_recorded_config_drift(
    activity_input: CheckRecordedConfigDriftInput,
) -> bool:
    """Check Recorded Config Drift."""
    client = create_dcim_client()
    async with client:
        return await client.has_recorded_config_drift(activity_input.device_id)


class GetDeviceVrfsInput(BaseModel):
    """Get Device VRFs Input."""

    device_id: str


class GetDeviceVrfsOutput(BaseModel):
    """Get Device VRFs Output."""

    vrfs: list[DeviceVRF]


@activity.defn(name="get_device_vrfs")
async def get_device_vrfs(
    activity_input: GetDeviceVrfsInput,
) -> GetDeviceVrfsOutput:
    """Get VRFs assigned to a device."""
    client = create_dcim_client()
    try:
        async with client:
            vrfs = await client.get_device_vrfs(activity_input.device_id)
    except DCIMError as error:
        raise _as_application_error(error) from error
    return GetDeviceVrfsOutput(vrfs=vrfs)


class AssignVrfToDeviceInput(BaseModel):
    """Assign VRF to Device Input."""

    device_id: str
    vrf_id: str


@activity.defn
async def assign_vrf_to_device(
    activity_input: AssignVrfToDeviceInput,
) -> None:
    """Assign a VRF to a device."""
    client = create_dcim_client()
    async with client:
        await client.assign_vrf_to_device(activity_input.device_id, activity_input.vrf_id)


class GetDeviceInterfacesInput(BaseModel):
    """Get Device Interfaces Input."""

    device_id: str
    interface_names: list[str] | None = None


class GetDeviceInterfacesOutput(BaseModel):
    """Get Device Interfaces Output."""

    interfaces: list[InterfaceData]


@activity.defn
async def get_device_interfaces(
    activity_input: GetDeviceInterfacesInput,
) -> GetDeviceInterfacesOutput:
    """Get interfaces for a device by name."""
    client = create_dcim_client()
    async with client:
        interfaces = await client.get_device_interfaces(device_id=activity_input.device_id)

    if activity_input.interface_names:
        filtered = [intf for intf in interfaces if intf.name in activity_input.interface_names]

        found_names = {intf.name for intf in filtered}
        missing = set[str](activity_input.interface_names) - found_names
        if missing:
            raise ApplicationError(
                f"Interfaces not found on device {activity_input.device_id}: "
                f"{', '.join(sorted(missing))}"
            )

        interfaces = filtered

    return GetDeviceInterfacesOutput(interfaces=interfaces)


class AssignVrfToInterfaceInput(BaseModel):
    """Set the VRF assigned to an interface."""

    interface_id: str
    vrf_id: str | None


@activity.defn
async def assign_vrf_to_interface(
    activity_input: AssignVrfToInterfaceInput,
) -> None:
    """Assign a VRF to an interface."""
    client = create_dcim_client()
    async with client:
        await client.assign_vrf_to_interface(activity_input.interface_id, activity_input.vrf_id)


class ReconcileSpXOverlayAssignmentsInput(BaseModel):
    """Spectrum-X overlay assignments to reconcile in the configured DCIM."""

    overlay_id: str | None
    site: str
    device_id: str
    interface_ids: list[str]
    device_interface_ids: list[str]


class ReconcileSpXOverlayAssignmentsOutput(BaseModel):
    """Result of reconciling Spectrum-X overlay assignments."""

    created: int
    removed: int
    reconciliation_changed: bool = False


def _activity_was_retried() -> bool:
    """Return whether the current activity has already made an attempt."""
    try:
        return activity.info().attempt > 1
    except RuntimeError:
        # Unit tests call activity implementations directly, outside a worker.
        return False


@activity.defn
async def reconcile_spx_overlay_assignments(
    activity_input: ReconcileSpXOverlayAssignmentsInput,
) -> ReconcileSpXOverlayAssignmentsOutput:
    """Make overlay-plugin assignments match Spectrum-X device and port intent.

    An interface can belong to only one Spectrum-X VRF, so stale Spectrum-X
    assignments are removed when a port moves between overlays. Omitting the
    target overlay removes the selected ports' Spectrum-X assignments. Device
    assignments are removed when no interface on the device uses their overlay.
    """
    client = create_dcim_client()
    try:
        async with client:
            created, removed = await client.reconcile_spectrum_x_overlay_assignments(
                activity_input.overlay_id,
                activity_input.site,
                activity_input.device_id,
                activity_input.interface_ids,
                activity_input.device_interface_ids,
            )
    except DCIMError as error:
        raise _as_application_error(error) from error

    # A prior attempt can complete the only mutation and then fail during a
    # later read. The retry sees the desired state and reports zero counts, but
    # downstream render/deploy decisions must still conservatively treat the
    # reconciliation as changed.
    return ReconcileSpXOverlayAssignmentsOutput(
        created=created,
        removed=removed,
        reconciliation_changed=bool(created or removed or _activity_was_retried()),
    )


class RemoveUnmappedDeviceVrfsInput(BaseModel):
    """Device whose VRF associations should be reconciled with its interfaces."""

    device_id: str
    vrf_ids: list[str]


class RemoveUnmappedDeviceVrfsOutput(BaseModel):
    """VRF associations removed because no device interface used them."""

    removed_vrf_ids: list[str]


@activity.defn
async def remove_unmapped_device_vrfs(
    activity_input: RemoveUnmappedDeviceVrfsInput,
) -> RemoveUnmappedDeviceVrfsOutput:
    """Remove affected device/VRF associations that have no interface mappings."""
    if not activity_input.vrf_ids:
        return RemoveUnmappedDeviceVrfsOutput(removed_vrf_ids=[])

    client = create_dcim_client()
    try:
        async with client:
            removed_vrf_ids = await client.remove_unmapped_device_vrfs(
                activity_input.device_id, activity_input.vrf_ids
            )
    except DCIMError as error:
        raise _as_application_error(error) from error

    return RemoveUnmappedDeviceVrfsOutput(removed_vrf_ids=removed_vrf_ids)
