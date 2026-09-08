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
"""Connected Host Workflow."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from py_markdown_table.markdown_table import markdown_table
from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.mixins.stage import (
    StageInput,
    StageMixin,
    StageOutput,
    StateEnum,
    stage_executor,
)
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.device import (
        DeviceMacEntry,
        DeviceMacTable,
        DeviceNeighborData,
        format_mac,
        is_mac_address,
    )
    from nv_config_manager.temporal.common.mixins.archive import ArchiveMixin
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        HostData,
        get_host_data_by_macs,
        get_host_data_by_names,
        get_network_device,
    )
    from nv_config_manager.temporal.ngc.activities.device import (
        get_device_actual_neighbors,
        get_device_mac_table,
    )


ACTIVITY_NO_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


def interface_sort_key(interface_name: str) -> tuple[str, list[int]]:
    """
    Create a sort key for interface names that handles various vendor formats.

    Supports formats like:
    - swp1, swp2, swp3
    - swp1s1, swp1s2, swp2s1, swp2s2
    - Ethernet1, Ethernet1/1, Ethernet1/1/1
    - ge-1/1/1, xe-1/1/1

    Returns a tuple of (prefix, [numeric_parts]) for proper sorting.
    """
    # Pattern to match interface names with numeric components
    # Matches: prefix + numbers separated by / or s
    pattern = r"^([\w\-]+?)(\d+(?:[\/s]\d+)*)?$"
    match = re.match(pattern, interface_name)

    if not match:
        # If no match, return original string for fallback sorting
        return (interface_name, [])

    prefix, numeric_part = match.groups()

    # Extract all numeric components
    # Split by / or s and convert to integers
    numeric_parts = []
    if numeric_part:
        # Split by / or s, filter out empty strings, convert to int
        parts = re.split(r"[\/s]", numeric_part)
        numeric_parts = [int(part) for part in parts if part.isdigit()]

    return (prefix, numeric_parts)


class ConnectedHostWorkflowInput(BaseModel):
    """Connected Host Workflow Input."""

    device_id: DeviceReference = Field(description="Identifier of the network device to analyze.")


@workflow.defn
class ConnectedHostMetadataWorkflow(WorkflowMetadataMixin, StageMixin, DeviceMixin, ArchiveMixin):
    """Connected host metadata discovery workflow."""

    # Workflow metadata
    workflow_name = "Connected Host Metadata"
    workflow_description = (
        "Discover and analyze connected hosts via MAC table and LLDP neighbor data"
    )
    workflow_input_class = ConnectedHostWorkflowInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/ngc/connected_host_metadata"
    workflow_namespace = "ngc"
    workflow_mcp_enabled = True

    def __init__(self) -> None:
        """Initialize workflow."""
        StageMixin.__init__(self)
        self.define_stage(
            name="get_device_mac_table",
            description="Get the MAC addresses from the device FDB",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_device_neighbors",
            description="Get the LLDP neighbor data from the device",
            requires_approval=False,
            depends_on=[],
        )
        self.define_stage(
            name="get_connected_host_data",
            description="Get the host data from the DCIM",
            requires_approval=False,
            depends_on=["get_device_mac_table", "get_device_neighbors"],
        )

    class DeviceMacTableStageInput(StageInput):
        """MAC Table Stage Input."""

        device_id: str

    class DeviceMacTableStageOuput(StageOutput):
        """MAC Table Stage Output."""

        mac_table: DeviceMacTable

    @stage_executor("get_device_mac_table")
    async def get_device_mac_table(
        self, stage_input: DeviceMacTableStageInput
    ) -> DeviceMacTableStageOuput:
        """Get connected mac addresses from device."""
        device_data = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        self.attach_device_search_attributes(device_data.device)
        mac_table = await workflow.execute_activity(
            get_device_mac_table,
            device_data.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        table_data = [
            {
                "Interface": entry.interface,
                "MAC Address": format_mac(entry.mac),
                "VLAN": entry.vlan or "--",
            }
            for entry in sorted(
                mac_table.by_mac.values(),
                key=lambda x: interface_sort_key(x.interface),
            )
            if entry.vlan
        ]

        markdown = (
            markdown_table(table_data).set_params(quote=False, row_sep="markdown").get_markdown()
            if table_data
            else "No MAC Addresses found on device."
        )

        return ConnectedHostMetadataWorkflow.DeviceMacTableStageOuput(
            display=markdown,
            mac_table=mac_table,
        )

    class DeviceNeighborsStageInput(StageInput):
        """Device Neighbors Stage Input."""

        device_id: str

    class DeviceNeighborsStageOutput(StageOutput):
        """Device Neighbors Stage Output."""

        neighbor_data: DeviceNeighborData

    @stage_executor("get_device_neighbors")
    async def get_device_neighbors(
        self, stage_input: DeviceNeighborsStageInput
    ) -> DeviceNeighborsStageOutput:
        """Get LLDP neighbor data from device."""
        device_data = await workflow.execute_activity(
            get_network_device,
            GetNetworkDeviceInput(device_id=stage_input.device_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        neighbor_data = await workflow.execute_activity(
            get_device_actual_neighbors,
            device_data.device,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        table_data = [
            {
                "Interface": interface,
                "Neighbor Device": neighbor.device_name or "--",
                "Neighbor Interface": neighbor.name or "--",
                "Link Status": "Up" if neighbor_data.link_states.get(interface, False) else "Down",
            }
            for interface, neighbor in sorted(
                neighbor_data.neighbors.items(), key=lambda x: interface_sort_key(x[0])
            )
        ]

        markdown = (
            markdown_table(table_data).set_params(quote=False, row_sep="markdown").get_markdown()
            if table_data
            else "No LLDP neighbors found on device."
        )

        return ConnectedHostMetadataWorkflow.DeviceNeighborsStageOutput(
            display=markdown,
            neighbor_data=neighbor_data,
        )

    class ConnectedHostDataStageInput(StageInput):
        """Connected Host Stage Input."""

        mac_table: DeviceMacTable
        neighbor_data: DeviceNeighborData

    class HostEntry(BaseModel):
        """Connected Host Entry."""

        mac: str | None
        vlan: int | None
        connected_interface: str | None
        device_interface: str
        name: str | None
        lldp_name: str | None
        tenant: str | None
        url: str | None
        alias: str | None = None

    class ConnectedHostDataStageOutput(StageOutput):
        """Markdown output for connected host data."""

        host_table: list[ConnectedHostMetadataWorkflow.HostEntry]

    @stage_executor("get_connected_host_data")
    async def get_connected_host_data(
        self, stage_input: ConnectedHostDataStageInput
    ) -> ConnectedHostDataStageOutput:
        """Get metadata for connected hosts."""
        mac_addresses = set(stage_input.mac_table.by_mac.keys())

        # Add neighbor MACs in case LLDP hostnames don't match the DCIM
        for neighbor in stage_input.neighbor_data.neighbors.values():
            if neighbor.name and is_mac_address(neighbor.name):
                mac_addresses.add(neighbor.name)

        neighbor_device_names = {
            neighbor.device_name
            for neighbor in stage_input.neighbor_data.neighbors.values()
            if neighbor.device_name
        }

        host_data_by_mac = []
        # Get host data by MAC addresses (for end hosts)
        if mac_addresses:
            host_data_by_mac = await workflow.execute_activity(
                get_host_data_by_macs,
                list(mac_addresses),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )

        # Get host data by device names (for LLDP neighbors - network devices)
        host_data_by_name = []
        if neighbor_device_names:
            host_data_by_name = await workflow.execute_activity(
                get_host_data_by_names,
                list(neighbor_device_names),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )

        # Create lookup dictionaries
        nb_data_by_mac: dict[str, HostData] = {}
        for entry in host_data_by_mac:
            for host_iface in entry.interfaces:
                nb_data_by_mac[host_iface.mac] = entry

        nb_data_by_name = {entry.name: entry for entry in host_data_by_name}

        mac_entry_by_interface: dict[str, DeviceMacEntry] = {}
        for interface_name, macs in stage_input.mac_table.by_interface.items():
            for mac in macs:
                mac_entry = stage_input.mac_table.by_mac[mac]
                # Only include the remote MAC, not the local MAC
                if mac_entry.vlan:
                    if interface_name in mac_entry_by_interface:
                        # If the previous entry is whats stored in NB, keep that
                        if mac_entry_by_interface[interface_name].mac not in nb_data_by_mac:
                            # If our new entry is in NB or is younger than the previous entry, override
                            if (
                                mac_entry.mac in nb_data_by_mac
                                or mac_entry.age < mac_entry_by_interface[interface_name].age
                            ):
                                mac_entry_by_interface[interface_name] = mac_entry
                    else:
                        mac_entry_by_interface[interface_name] = mac_entry

        all_interfaces = set(mac_entry_by_interface.keys()) | set(
            stage_input.neighbor_data.neighbors.keys()
        )

        # Build entries by mapping MAC and LLDP data by interface name
        host_entries: list[ConnectedHostMetadataWorkflow.HostEntry] = []

        for interface_name in all_interfaces:
            host_entry: dict[str, Any] = {
                "device_interface": interface_name,
                "mac": None,
                "vlan": None,
                "connected_interface": None,
                "name": None,
                "tenant": None,
                "url": None,
                "alias": None,
                "lldp_name": None,
            }
            host_data = None
            if interface_name in mac_entry_by_interface:
                mac_entry = mac_entry_by_interface[interface_name]
                host_data = nb_data_by_mac.get(mac_entry.mac)
                host_interface = None
                if host_data:
                    host_interface = next(
                        (i for i in host_data.interfaces if i.mac == mac_entry.mac),
                        None,
                    )
                host_entry["mac"] = mac_entry.mac
                host_entry["vlan"] = mac_entry.vlan
                host_entry["connected_interface"] = host_interface.name if host_interface else None
                host_entry["name"] = host_data.name if host_data else None
                host_entry["tenant"] = host_data.tenant if host_data else None
                host_entry["url"] = host_data.url if host_data else None
                host_entry["alias"] = host_data.alias if host_data else None

            if interface_name in stage_input.neighbor_data.neighbors:
                neighbor_data = stage_input.neighbor_data.neighbors[interface_name]
                if not host_data and neighbor_data.device_name:
                    host_data = nb_data_by_name.get(neighbor_data.device_name)
                    if not host_data and neighbor_data.name and is_mac_address(neighbor_data.name):
                        host_data = nb_data_by_mac.get(neighbor_data.name)
                    host_entry["name"] = host_data.name if host_data else None
                    host_entry["tenant"] = host_data.tenant if host_data else None
                    host_entry["url"] = host_data.url if host_data else None
                    host_entry["alias"] = host_data.alias if host_data else None
                    host_entry["connected_interface"] = (
                        neighbor_data.name if neighbor_data else None
                    )
                host_entry["lldp_name"] = neighbor_data.device_name

            host_entries.append(ConnectedHostMetadataWorkflow.HostEntry(**host_entry))

        table_data = [
            {
                "Network Interface": row.device_interface,
                "Host": f"[{row.name}]({row.url})" if row.name else "--",
                "LLDP Name": row.lldp_name or "--",
                "Alias": row.alias or "--",
                "Connected Interface": row.connected_interface or "--",
                "MAC Address": format_mac(row.mac) if row.mac else "--",
                "Tenant": row.tenant or "--",
                "VLAN": row.vlan or "--",
            }
            for row in sorted(host_entries, key=lambda x: interface_sort_key(x.device_interface))
        ]

        return ConnectedHostMetadataWorkflow.ConnectedHostDataStageOutput(
            display=markdown_table(table_data)
            .set_params(quote=False, row_sep="markdown")
            .get_markdown(),
            host_table=host_entries,
        )

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: ConnectedHostWorkflowInput) -> str:  # type: ignore[override, ty:invalid-method-override]
        """Execute connected host workflow."""
        self.set_input(workflow_input)
        mac_table_output = await self.get_device_mac_table(
            ConnectedHostMetadataWorkflow.DeviceMacTableStageInput(
                device_id=workflow_input.device_id
            )
        )

        neighbor_output = await self.get_device_neighbors(
            ConnectedHostMetadataWorkflow.DeviceNeighborsStageInput(
                device_id=workflow_input.device_id
            )
        )

        if not mac_table_output.mac_table.by_mac and not neighbor_output.neighbor_data.neighbors:
            self.set_stage_state("get_connected_host_data", StateEnum.UNREACHABLE)
            await self.archive_results()
            return "No MAC addresses or LLDP neighbors found on device."

        host_data_output = await self.get_connected_host_data(
            ConnectedHostMetadataWorkflow.ConnectedHostDataStageInput(
                mac_table=mac_table_output.mac_table,
                neighbor_data=neighbor_output.neighbor_data,
            )
        )
        await self.archive_results()
        return host_data_output.display
