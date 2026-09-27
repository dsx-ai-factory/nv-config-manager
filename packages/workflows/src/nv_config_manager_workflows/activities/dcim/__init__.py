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

import logging
import re

import netaddr
from nv_config_manager_dcim.errors import DCIMError
from nv_config_manager_dcim.workflow_models import DeviceInventoryFilter
from nv_config_manager_logging import LogCategory, get_logger
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.dcim.models import (
    AssignVrfToDeviceInput,
    AssignVrfToInterfaceInput,
    CheckRecordedConfigDriftInput,
    DeleteOverlayInput,
    DeleteOverlayOutput,
    DeviceVRF,
    DeviceVrfInfo,
    GetAvailableRouteDistinguishersInput,
    GetAvailableRouteDistinguishersOutput,
    GetDeviceInterfacesInput,
    GetDeviceInterfacesOutput,
    GetDeviceVrfsInput,
    GetDeviceVrfsOutput,
    GetHostDeviceInput,
    GetHostDeviceOutput,
    GetHostDevicesInput,
    GetHostDevicesOutput,
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    GetNetworkDevicesInput,
    GetNetworkDevicesOutput,
    HostData,
    HostInterface,
    ProvisionVrfInput,
    QueryVRFByVPCInput,
    ReconcileSpXOverlayAssignmentsInput,
    ReconcileSpXOverlayAssignmentsOutput,
    RemoveUnmappedDeviceVrfsInput,
    RemoveUnmappedDeviceVrfsOutput,
    SwitchPortByMacActivityInput,
    SwitchPortByMacActivityOutput,
    Vrf,
    VrfDeletionActivityInput,
)
from nv_config_manager_workflows.activities.dcim.session import dcim_client_session
from nv_config_manager_workflows.runtime import get_dcim_client

logger = get_logger(__name__, category=LogCategory.DCIM)
logger.setLevel(logging.INFO)


@activity.defn
async def get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    """Get network device data."""
    client = get_dcim_client()
    async with client:
        device = await client.get_network_device(activity_input.device_id)
    return GetNetworkDeviceOutput(device=device)


@activity.defn
async def get_host_device(
    activity_input: GetHostDeviceInput,
) -> GetHostDeviceOutput:
    """Get host device data."""
    client = get_dcim_client()
    async with client:
        device = await client.get_host_device(activity_input.device_id)
    return GetHostDeviceOutput(device=device)


@activity.defn
async def get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Get network devices for a specific site."""
    client = get_dcim_client()
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


@activity.defn
async def get_host_devices(
    activity_input: GetHostDevicesInput,
) -> GetHostDevicesOutput:
    """Get host devices."""
    client = get_dcim_client()
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


@activity.defn
async def get_host_data_by_macs(mac_addresses: list[str]) -> list[HostData]:
    """Load host data from list of mac addresses."""
    client = get_dcim_client()
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
    client = get_dcim_client()
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


@activity.defn
async def get_available_route_distinguishers(
    activity_input: GetAvailableRouteDistinguishersInput,
) -> GetAvailableRouteDistinguishersOutput:
    """Get Available Route Distinguishers Activity."""
    client = get_dcim_client()
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


def _vni_from_rd(route_distinguisher: str) -> int:
    """Derive the VNI from a route distinguisher of the form ``*:<vni>``."""
    parts = route_distinguisher.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(f"Invalid route distinguisher {route_distinguisher!r}, expected '*:<vni>'")
    return int(parts[1])


@activity.defn
async def provision_vrf(
    activity_input: ProvisionVrfInput,
) -> None:
    """Provision the Spectrum-X overlay, VRFs, and L3 VXLANs for a VPC.

    Finds or creates the (location-scoped) Spectrum-X overlay, then creates one VRF
    and one L3 VXLAN per namespace, binding each VRF and VXLAN to the overlay.
    Resources created in this call are rolled back on failure; the overlay is left
    in place since it is found-or-created and may be shared.
    """
    vni = _vni_from_rd(activity_input.route_distinguisher)
    client = get_dcim_client()
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
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error


@activity.defn
async def get_vrfs_by_overlay_id(activity_input: QueryVRFByVPCInput) -> list[Vrf] | None:
    """Get VRFs for an overlay by looking up overlay → vxlans → VRF IDs → GraphQL."""
    client = get_dcim_client()
    async with client:
        spectrum_x_vrfs = await client.get_spectrum_x_vrfs(
            activity_input.overlay_id,
            activity_input.site,
            activity_input.namespace,
        )
    vrfs = [Vrf.from_spectrum_x_vrf(vrf) for vrf in spectrum_x_vrfs]
    return vrfs if vrfs else None


@activity.defn
async def delete_vrf(activity_input: VrfDeletionActivityInput) -> None:
    """Delete a VRF, its overlay assignments, and the L3 VXLAN bound to it.

    VXLANs are fetched by VNI then filtered to those whose vrf.id matches
    vrf_id (the VRF FK is SET_NULL on VRF deletion, so they are removed
    explicitly to keep the overlay clean).
    """
    client = get_dcim_client()
    async with client:
        await client.delete_spectrum_x_vrf(activity_input.vrf_id, activity_input.vnid)


@activity.defn
async def delete_overlay(activity_input: DeleteOverlayInput) -> DeleteOverlayOutput:
    """Delete the SpectrumX overlay and its assignments if no VXLANs remain."""
    overlay_name = activity_input.overlay_id
    client = get_dcim_client()
    async with client:
        deleted = await client.delete_spectrum_x_overlay_if_unused(
            overlay_name, activity_input.site
        )
        if not deleted:
            logger.info("Overlay %s still has VXLANs, leaving in place", overlay_name)
    return DeleteOverlayOutput(deleted=deleted, overlay_name=overlay_name)


@activity.defn
async def get_switch_port_by_remote_mac_address(
    activity_input: SwitchPortByMacActivityInput,
) -> SwitchPortByMacActivityOutput:
    """Get Switch Port by Remote MAC Address."""
    client = get_dcim_client()
    async with client:
        device, interface = await client.get_connected_switch_port_by_remote_mac(
            activity_input.remote_mac_address
        )
    return SwitchPortByMacActivityOutput(device=device, interface=interface)


@activity.defn
async def check_recorded_config_drift(
    activity_input: CheckRecordedConfigDriftInput,
) -> bool:
    """Check Recorded Config Drift."""
    client = get_dcim_client()
    async with client:
        return await client.has_recorded_config_drift(activity_input.device_id)


@activity.defn(name="get_device_vrfs")
async def get_device_vrfs(
    activity_input: GetDeviceVrfsInput,
) -> GetDeviceVrfsOutput:
    """Get VRFs assigned to a device."""
    client = get_dcim_client()
    try:
        async with client:
            vrfs = await client.get_device_vrfs(activity_input.device_id)
    except DCIMError as error:
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error
    return GetDeviceVrfsOutput(vrfs=vrfs)


@activity.defn
async def assign_vrf_to_device(
    activity_input: AssignVrfToDeviceInput,
) -> None:
    """Assign a VRF to a device."""
    client = get_dcim_client()
    async with client:
        await client.assign_vrf_to_device(activity_input.device_id, activity_input.vrf_id)


@activity.defn
async def get_device_interfaces(
    activity_input: GetDeviceInterfacesInput,
) -> GetDeviceInterfacesOutput:
    """Get interfaces for a device by name."""
    client = get_dcim_client()
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


@activity.defn
async def assign_vrf_to_interface(
    activity_input: AssignVrfToInterfaceInput,
) -> None:
    """Assign a VRF to an interface."""
    client = get_dcim_client()
    async with client:
        await client.assign_vrf_to_interface(activity_input.interface_id, activity_input.vrf_id)


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
    client = get_dcim_client()
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
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error

    # A prior attempt can complete the only mutation and then fail during a
    # later read. The retry sees the desired state and reports zero counts, but
    # downstream render/deploy decisions must still conservatively treat the
    # reconciliation as changed.
    return ReconcileSpXOverlayAssignmentsOutput(
        created=created,
        removed=removed,
        reconciliation_changed=bool(created or removed or _activity_was_retried()),
    )


@activity.defn
async def remove_unmapped_device_vrfs(
    activity_input: RemoveUnmappedDeviceVrfsInput,
) -> RemoveUnmappedDeviceVrfsOutput:
    """Remove affected device/VRF associations that have no interface mappings."""
    if not activity_input.vrf_ids:
        return RemoveUnmappedDeviceVrfsOutput(removed_vrf_ids=[])

    client = get_dcim_client()
    try:
        async with client:
            removed_vrf_ids = await client.remove_unmapped_device_vrfs(
                activity_input.device_id, activity_input.vrf_ids
            )
    except DCIMError as error:
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error

    return RemoveUnmappedDeviceVrfsOutput(removed_vrf_ids=removed_vrf_ids)


# Preserve the DCIM activities' relative positions in the service worker catalog.
DCIM_ACTIVITIES = (
    get_host_data_by_macs,
    get_host_data_by_names,
    get_host_devices,
    get_network_devices,
    get_host_device,
    get_network_device,
    get_available_route_distinguishers,
    provision_vrf,
    get_switch_port_by_remote_mac_address,
    get_vrfs_by_overlay_id,
    delete_vrf,
    delete_overlay,
    get_device_vrfs,
    assign_vrf_to_device,
    get_device_interfaces,
    assign_vrf_to_interface,
    reconcile_spx_overlay_assignments,
    remove_unmapped_device_vrfs,
    check_recorded_config_drift,
)

__all__ = [
    "DCIM_ACTIVITIES",
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
    "assign_vrf_to_device",
    "assign_vrf_to_interface",
    "check_recorded_config_drift",
    "dcim_client_session",
    "delete_overlay",
    "delete_vrf",
    "get_available_route_distinguishers",
    "get_device_interfaces",
    "get_device_vrfs",
    "get_host_data_by_macs",
    "get_host_data_by_names",
    "get_host_device",
    "get_host_devices",
    "get_network_device",
    "get_network_devices",
    "get_switch_port_by_remote_mac_address",
    "get_vrfs_by_overlay_id",
    "provision_vrf",
    "reconcile_spx_overlay_assignments",
    "remove_unmapped_device_vrfs",
]
