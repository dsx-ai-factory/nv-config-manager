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
"""Activities for BMC configuration."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

import aiohttp
import aiohttp.client_exceptions
import netaddr
import requests
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import create_dcim_client
from nv_config_manager.temporal.client.device import DeviceArpTable
from nv_config_manager.temporal.client.redfish import (
    Bluefield3RedfishConnection,
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishServer,
    RedfishVendor,
    get_config_manager_connection,
    get_default_connection,
)
from nv_config_manager.temporal.common.mixins.device import HostDeviceData

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logger.setLevel(logging.INFO)


class DiscoverHostsInput(BaseModel):
    """Discover hosts input."""

    ip_range_start: str
    ip_range_end: str
    ips_excluded: list[str]
    port: int
    timeout: int = 5


class DiscoverHostsOutput(BaseModel):
    """Discover hosts output."""

    hosts: list[RedfishHost] = []


def combine_arp_tables(tables: list[DeviceArpTable]) -> DeviceArpTable:
    """Combine multiple device ARP tables into one."""
    result = DeviceArpTable()
    for table in tables:
        for address, macs in table.ip_to_mac.items():
            if address not in result.ip_to_mac:
                result.ip_to_mac[address] = []
            for mac in macs:
                if mac not in result.ip_to_mac[address]:
                    result.ip_to_mac[address].append(mac)
    return result


async def http_get(session: aiohttp.ClientSession, url: str) -> aiohttp.ClientResponse | None:
    """Async HTTP get."""
    try:
        resp = await session.get(url, ssl=False)
        # populate the response body so it can be accessed later
        await resp.json()
    except TimeoutError as err:
        logger.debug("Timeout to %s: %s", url, str(err))
        return None
    except aiohttp.client_exceptions.ClientConnectionError as err:
        logger.debug("Error connecting to %s: %s", url, str(err))
        return None
    logger.debug("Established connection to %s", url)
    return resp if resp.ok else None


@activity.defn
async def discover_redfish_hosts(
    activity_input: DiscoverHostsInput,
) -> DiscoverHostsOutput:
    """Discover Redfish hosts."""
    start_ip = ipaddress.IPv4Address(activity_input.ip_range_start)
    end_ip = ipaddress.IPv4Address(activity_input.ip_range_end)
    if start_ip >= end_ip:
        raise ApplicationError(
            f"End IP {end_ip} is lower or equal to start IP {start_ip}",
            non_retryable=True,
        )

    addresses = [
        ipaddress.IPv4Address(ip)
        for ip in range(int(start_ip), int(end_ip))
        if str(ipaddress.IPv4Address(ip)) not in activity_input.ips_excluded
    ]
    if not addresses:
        return DiscoverHostsOutput(hosts=[])

    logger.info(
        "Scanning range %s-%s for Redfish hosts",
        addresses[0],
        addresses[-1],
    )
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=activity_input.timeout)
    ) as session:
        responses = await asyncio.gather(
            *[
                http_get(
                    session,
                    f"https://{address}:{activity_input.port}/redfish/v1/",
                )
                for address in addresses
            ]
        )

    output = DiscoverHostsOutput()
    for response in responses:
        if not response or not response.ok:
            continue

        data = await response.json()
        address = response.url.host
        if not address:
            raise ApplicationError(f"Invalid URL: {response.url}")
        if data.get("RedfishVersion") and data.get("Vendor"):
            try:
                output.hosts.append(
                    RedfishHost(
                        address=address,
                        port=activity_input.port,
                        vendor=RedfishVendor(str(data["Vendor"])),
                    )
                )
            except ValueError:
                logger.warning(
                    "Redfish vendor %s not supported for host %s, skipping",
                    data["Vendor"],
                    address,
                )
        else:
            logger.info(
                "Unable to confirm redfish connection to %s: %s",
                address,
                data,
            )
    return output


class PopulateRedfishMacsInput(BaseModel):
    """Populate Redfish MACs input."""

    hosts: list[RedfishHost]
    arp_tables: list[DeviceArpTable]


class PopulateRedfishMacsOutput(BaseModel):
    """Populate Redfish MACs output."""

    hosts: list[RedfishHost]


@activity.defn
async def populate_redfish_macs(
    activity_input: PopulateRedfishMacsInput,
) -> PopulateRedfishMacsOutput:
    """Populate Redfish MACs."""
    arp_tables = combine_arp_tables(activity_input.arp_tables)
    hosts = activity_input.hosts
    for host in hosts:
        if len(arp_tables.ip_to_mac.get(host.address, [])) == 1:
            host.mac = arp_tables.ip_to_mac[host.address][0]
    return PopulateRedfishMacsOutput(hosts=hosts)


class RedfishHostInput(BaseModel):
    """Redfish host input."""

    host: RedfishHost


class RedfishHostOutput(BaseModel):
    """Redfish host input."""

    host: RedfishHost | None


@activity.defn
def set_redfish_password(
    activity_input: RedfishHostInput,
) -> RedfishHostOutput:
    """Activity to provision the Redfish password."""
    conn = get_default_connection(activity_input.host)
    rsp = None
    try:
        rsp = conn.set_config_manager_password()
    except requests.exceptions.HTTPError as error:
        if error.response.status_code != 401:
            raise error

    if rsp:
        result = RedfishHostOutput(host=activity_input.host)
        logger.info("Successfully set NVIDIA Config Manager password for %s", activity_input.host)
    else:
        # Check if NVIDIA Config Manager password already set
        conn = get_config_manager_connection(activity_input.host)
        try:
            rsp = conn.get_redfish_data()
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == 401:
                raise ApplicationError(
                    f"Host {activity_input.host} has a non-default root password, "
                    "please factory reset it and try again"
                ) from error
            raise error
        result = RedfishHostOutput(host=None)
        logger.info("NVIDIA Config Manager password already set for %s", activity_input.host)

    return result


@activity.defn
def power_on_host(
    activity_input: RedfishHostInput,
) -> RedfishHostOutput:
    """Activity to power on a host."""
    conn = get_config_manager_connection(activity_input.host)
    if conn.is_host_powered_on():
        return RedfishHostOutput(host=None)
    conn.power_on_chassis()
    logger.info(
        "Successfully powered on host %s",
        activity_input.host,
    )
    return RedfishHostOutput(host=activity_input.host)


@activity.defn
def factory_reset_bmc(
    activity_input: RedfishHostInput,
) -> RedfishHostOutput:
    """Activity to factory reset a BMC."""
    conn = get_config_manager_connection(activity_input.host)
    conn.factory_reset()
    logger.info(
        "Successfully factory reset BMC %s",
        activity_input.host,
    )
    return RedfishHostOutput(host=activity_input.host)


class GetServerDetailsActivityInput(BaseModel):
    """Get Server Details activity input."""

    host: RedfishHost
    nic_manufacturers: list[str]


class GetServerDetailsActivityOutput(BaseModel):
    """Get Server Details activity output."""

    server: RedfishServer


@activity.defn
def get_server_details(
    activity_input: GetServerDetailsActivityInput,
) -> GetServerDetailsActivityOutput:
    """Get NIC details for a host."""
    client = get_config_manager_connection(activity_input.host)
    return GetServerDetailsActivityOutput(
        server=RedfishServer(
            address=activity_input.host.address,
            port=activity_input.host.port,
            vendor=activity_input.host.vendor,
            mac=activity_input.host.mac,
            serial=client.get_serial(),
            nics=client.get_nic_info(manufacturers=activity_input.nic_manufacturers),
        )
    )


class GetDpuDetailsActivityInput(BaseModel):
    """Get DPU Details activity input."""

    host: RedfishHost


class GetDpuDetailsActivityOutput(BaseModel):
    """Get DPU Details activity output."""

    dpu: RedfishDpu


@activity.defn
def get_dpu_details(
    activity_input: GetDpuDetailsActivityInput,
) -> GetDpuDetailsActivityOutput:
    """Get DPU details."""
    client = get_config_manager_connection(activity_input.host)
    if not isinstance(client, Bluefield3RedfishConnection):
        raise ApplicationError(
            f"Host {activity_input.host} is not a Bluefield DPU", non_retryable=True
        )
    part_number = str(client.get_chassis().json()["PartNumber"]).strip().upper()
    base_mac = netaddr.EUI(client.get_base_mac())
    if part_number == "900-9D3B6-00CV-AA0":
        return GetDpuDetailsActivityOutput(
            dpu=RedfishDpu(
                address=activity_input.host.address,
                port=activity_input.host.port,
                vendor=activity_input.host.vendor,
                mac=activity_input.host.mac,
                base_mac=str(base_mac),
                serial=client.get_serial(),
                ports=[
                    RedfishDpuPort(
                        name="eth0",
                        mac=str(netaddr.EUI(int(base_mac) + 17)),
                    ),
                    RedfishDpuPort(
                        name="eth1",
                        mac=str(netaddr.EUI(int(base_mac) + 18)),
                    ),
                ],
            )
        )
    raise ApplicationError(f"DPU part number {part_number} not supported!", non_retryable=True)


class UpdateDpuDataActivityInput(BaseModel):
    """Update DPU data activity input."""

    server: RedfishServer


class UpdateDpuDataActivityOutput(BaseModel):
    """Update DPU data activity output."""

    device_data: list[HostDeviceData]


@activity.defn
async def update_dpu_data(
    activity_input: UpdateDpuDataActivityInput,
) -> UpdateDpuDataActivityOutput:
    """Update DPU Data."""
    client = create_dcim_client()
    async with client:
        server_mac = activity_input.server.mac
        if server_mac is None:
            raise ApplicationError("Server MAC address is required", non_retryable=True)
        host_devices = await client.find_host_devices_by_mac(server_mac)
        if not host_devices:
            raise ApplicationError(
                f"Server {activity_input.server} not found in the configured DCIM"
            )
        if len(host_devices) > 1:
            raise ApplicationError(
                "Multiple devices in the configured DCIM for "
                f"MAC {activity_input.server.mac}: {host_devices}"
            )
        device_data = host_devices[0]

        # Assume lower bay name corresponds to lower NIC slot
        # i.e. Bay '0' would be slot '4' and bay '1' would be slot '5'
        child_device_data = []
        for bay in sorted(device_data.device_bays, key=lambda bay: bay.name):
            if bay.installed_device_id:
                child_device_data.append(
                    await client.get_host_device(device_id=bay.installed_device_id)
                )

        dpus = [
            nic.dpu
            for nic in sorted(activity_input.server.nics, key=lambda nic: nic.slot)
            if nic.dpu
        ]

        if len(child_device_data) != len(dpus):
            logger.warning(
                "Server %s has %s installed devices but %s DPUs, skipping",
                device_data.name,
                len(child_device_data),
                len(dpus),
            )
            return UpdateDpuDataActivityOutput(device_data=[])

        result = []
        for device, dpu in zip(child_device_data, dpus, strict=False):
            # Assume lower interface name corresponds with lower DPU port name
            # E.g. DPU Port 0 is NIC.Slot.4-1 and DPU Port 1 is NIC.Slot.4-2
            device_interfaces = sorted(
                [
                    interface
                    for interface in device.interfaces
                    if interface.name.lower().startswith("dpu port")
                ],
                key=lambda x: x.name,
            )
            dpu_interfaces = sorted(dpu.ports, key=lambda x: x.name)
            if len(device_interfaces) != len(dpu_interfaces):
                raise ApplicationError("Must have same number of device ports and DPU ports")

            interface_macs: dict[str, str] = {}
            for device_interface, dpu_interface in zip(
                device_interfaces, dpu_interfaces, strict=False
            ):
                if dpu_interface.mac is None:
                    raise ApplicationError(
                        f"MAC address is missing for DPU port {dpu_interface.name}",
                        non_retryable=True,
                    )
                interface_macs[device_interface.id] = dpu_interface.mac

            dpu_data = await client.update_dpu_device_inventory(
                device_id=device.id,
                serial=dpu.serial,
                interface_macs=interface_macs,
            )
            result.append(dpu_data)

    return UpdateDpuDataActivityOutput(device_data=result)
