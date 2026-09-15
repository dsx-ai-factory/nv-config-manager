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
"""Activities for devices."""

from __future__ import annotations

import netaddr
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMError, dcim_client_session
from nv_config_manager.temporal.client.device import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
    NetworkConnection,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


@activity.defn
async def get_device_intended_neighbors(
    activity_input: NetworkDeviceData,
) -> DeviceNeighborData:
    """Get intended interface connections through the selected DCIM provider."""
    try:
        async with dcim_client_session() as client:
            interfaces = await client.get_intended_interface_neighbors(activity_input.id)
    except DCIMError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc

    return DeviceNeighborData(
        neighbors={
            interface.name: InterfaceNeighborData(
                name=interface.connected_interface_name,
                macs=(
                    [str(netaddr.EUI(interface.connected_interface_mac))]
                    if interface.connected_interface_mac
                    else []
                ),
                device_name=device.name,
                device_serial=device.serial,
                device_role=(device.role.lower().replace(" ", "-") if device.role else None),
                device_rack=device.rack,
                device_position=device.position,
            )
            for interface in interfaces
            if (device := interface.connected_device)
        },
        ignore=[
            interface.name
            for interface in interfaces
            if "cable-validation-ignore" in interface.tags
        ],
        link_state_only=[
            interface.name
            for interface in interfaces
            if "cable-validation-link-state-only" in interface.tags
        ],
    )


def _has_neighbor_data(neighbor: InterfaceNeighborData) -> bool:
    """True if the neighbor entry has any actual LLDP/connection data."""
    return neighbor.name is not None or bool(neighbor.device_name) or bool(neighbor.macs)


@activity.defn
def get_device_actual_neighbors(
    device_data: NetworkDeviceData,
) -> DeviceNeighborData:
    """Get the current connections from a device. Empty neighbor entries are excluded."""
    result = NetworkConnection.from_device_data(device_data).get_interface_connections()
    neighbors = {k: v for k, v in result.neighbors.items() if _has_neighbor_data(v)}
    return DeviceNeighborData(
        neighbors=neighbors,
        link_states=result.link_states,
        ts_info=result.ts_info,
        ignore=result.ignore,
        link_state_only=result.link_state_only,
    )


@activity.defn
def get_device_mac_table(device_data: NetworkDeviceData) -> DeviceMacTable:
    """Get the MAC entries from a device FDB."""
    return NetworkConnection.from_device_data(device_data).get_mac_table()


@activity.defn
def get_device_arp_table(device_data: NetworkDeviceData) -> DeviceArpTable:
    """Get the ARP entries from a device."""
    return NetworkConnection.from_device_data(device_data).get_arp_table()


class ValidateHostnameActivityOutput(BaseModel):
    """Validate hostname activity input."""

    hostname: str


@activity.defn
def validate_hostname(device_data: NetworkDeviceData) -> ValidateHostnameActivityOutput:
    """Get the hostname from a device."""
    hostname = NetworkConnection.from_device_data(device_data).get_hostname()
    if hostname.lower() != device_data.name.lower():
        raise ApplicationError(
            f"Hostname on {device_data.primary_ip4 or device_data.primary_ip6} "
            f"({hostname}) does not match the DCIM record ({device_data.name}).",
            non_retryable=True,
        )
    return ValidateHostnameActivityOutput(hostname=hostname)


class SwitchPortNeighborActivityInput(BaseModel):
    """Switch Port Neighbor Input."""

    device_data: NetworkDeviceData
    interface: str


@activity.defn
def load_neighbor_data_by_switch_port(
    activity_input: SwitchPortNeighborActivityInput,
) -> InterfaceNeighborData | None:
    """Load neighbor data by switch port."""
    return NetworkConnection.from_device_data(activity_input.device_data).get_lldp_data(
        activity_input.interface
    )
