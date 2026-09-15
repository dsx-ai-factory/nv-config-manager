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
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.worker import Worker

from nv_config_manager.temporal.client.device import (
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    HostData,
    HostInterface,
)
from nv_config_manager.temporal.ngc.workflows.connected_host import (
    ConnectedHostMetadataWorkflow,
    ConnectedHostWorkflowInput,
    interface_sort_key,
)

# Add new mock MAC table for VLAN filtering test
MOCK_MAC_TABLE_VLAN_FILTER = DeviceMacTable(
    by_mac={
        # swp0
        "00-00-00-00-00-01": DeviceMacEntry(
            interface="swp0", mac="00-00-00-00-00-01", age=100, vlan=100
        ),
        "00-00-00-00-00-11": DeviceMacEntry(
            interface="swp0", mac="00-00-00-00-00-11", age=100, vlan=None
        ),
        # swp1
        "00-00-00-00-00-02": DeviceMacEntry(
            interface="swp1", mac="00-00-00-00-00-02", age=100, vlan=200
        ),
        "00-00-00-00-00-12": DeviceMacEntry(
            interface="swp1", mac="00-00-00-00-00-12", age=100, vlan=None
        ),
        # swp2
        "00-00-00-00-00-03": DeviceMacEntry(
            interface="swp2", mac="00-00-00-00-00-03", age=100, vlan=300
        ),
        "00-00-00-00-00-13": DeviceMacEntry(
            interface="swp2", mac="00-00-00-00-00-13", age=100, vlan=None
        ),
    },
    by_interface={
        "swp0": ["00-00-00-00-00-01", "00-00-00-00-00-11"],
        "swp1": ["00-00-00-00-00-02", "00-00-00-00-00-12"],
        "swp2": ["00-00-00-00-00-03", "00-00-00-00-00-13"],
    },
)


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="mock_device",
            role="mock_role",
            platform="cumulus-linux",
            site="SITEA",
            device_type="sn4200",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
        )
    )


@activity.defn(name="get_device_mac_table")
async def mock_get_device_mac_table(_) -> DeviceMacTable:
    return MOCK_MAC_TABLE_VLAN_FILTER


@activity.defn(name="get_device_actual_neighbors")
async def mock_get_device_actual_neighbors(_) -> DeviceNeighborData:
    return DeviceNeighborData(
        neighbors={
            "swp0": InterfaceNeighborData(
                name="swp0",
                macs=[],
                device_name="mock_neighbor1",
                device_serial="MOCKSERIAL1",
                device_role="tenant-a-device",
            ),
            "swp1": InterfaceNeighborData(
                name="Ethernet1/1",
                macs=[],
                device_name="mock_neighbor2",
                device_serial="MOCKSERIAL2",
                device_role="tenant-a-device",
            ),
            "swp10": InterfaceNeighborData(
                name="02:00:00:00:00:10",
                device_name="server6.sitea.example.com",
            ),
            "swp49": InterfaceNeighborData(
                name="swp49",
                macs=[],
                device_name="mock_neighbor3",
                device_serial="MOCKSERIAL3",
                device_role="tenant-a-device",
            ),
            "swp51": InterfaceNeighborData(
                name="Ethernet1/2",
                macs=[],
                device_name="mock_neighbor4",
                device_serial="MOCKSERIAL4",
                device_role="tenant-a-device",
            ),
        },
        link_states={
            "swp0": True,
            "swp1": True,
            "swp2": False,
            "swp10": True,
            "swp49": True,
            "swp51": True,
        },
    )


@activity.defn(name="get_host_data_by_macs")
async def mock_get_host_data_by_macs(mac_addresses: list[str]):
    entries = []
    for i, mac in enumerate(sorted(mac_addresses)):
        entries.append(
            HostData(
                interfaces=[HostInterface(name="eth0", mac=mac)],
                name=f"server{i}",
                tenant="TenantA",
                device_id=f"uuid-{i}",
                url="mock_nb_url",
                alias="server1alias" if i == 1 else None,
            )
        )
    return entries


@activity.defn(name="get_host_data_by_names")
async def mock_get_host_data_by_names(device_names: list[str]):
    entries = []
    for i, name in enumerate(sorted(device_names)):
        if name == "server6.sitea.example.com":
            # Device name that isn't in nautobot
            continue
        entries.append(
            HostData(
                interfaces=[],
                name=name,
                tenant="TenantA",
                device_id=f"uuid-neighbor-{i}",
                url="mock_nb_url",
                alias=f"{name}_alias" if i == 0 else None,
            )
        )
    return entries


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow(
    mock_time,
    mock_nats_client,
    env,
):
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[ConnectedHostMetadataWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_device_mac_table,
            mock_get_device_actual_neighbors,
            mock_get_host_data_by_macs,
            mock_get_host_data_by_names,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = ConnectedHostWorkflowInput(device_id="mock_device_uuid")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            ConnectedHostMetadataWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert result
        stages = await handle.query("stages")
        assert len(stages) == 3

        # Check get_device_mac_table stage
        mac_table_stage = stages[0]
        assert mac_table_stage["name"] == "get_device_mac_table"
        assert mac_table_stage["state"] == "COMPLETE"
        assert mac_table_stage["depends_on"] == []

        # Check get_device_neighbors stage
        neighbors_stage = stages[1]
        assert neighbors_stage["name"] == "get_device_neighbors"
        assert neighbors_stage["state"] == "COMPLETE"
        assert neighbors_stage["depends_on"] == []
        neighbors_display_stripped = neighbors_stage["output"]["display"].replace(" ", "")
        assert (
            "|Interface|NeighborDevice|NeighborInterface|LinkStatus|" in neighbors_display_stripped
        )

        # Check get_connected_host_data stage
        host_data_stage = stages[2]
        assert host_data_stage["name"] == "get_connected_host_data"
        assert host_data_stage["state"] == "COMPLETE"
        assert host_data_stage["depends_on"] == [
            "get_device_mac_table",
            "get_device_neighbors",
        ]

        # Check that the final output includes the new table structure with LLDP Name
        result_stripped = result.replace(" ", "")
        assert (
            "|NetworkInterface|Host|LLDPName|Alias|ConnectedInterface|MACAddress|Tenant|VLAN|"
            in result_stripped
        )
        # Check MAC-based entries
        assert (
            "|swp0|[server0](mock_nb_url)|mock_neighbor1|--|eth0|00:00:00:00:00:01|TenantA|100|"
            in result_stripped
        )
        assert (
            "|swp1|[server1](mock_nb_url)|mock_neighbor2|server1alias|eth0|00:00:00:00:00:02|TenantA|200|"
            in result_stripped
        )
        assert (
            "|swp2|[server2](mock_nb_url)|--|--|eth0|00:00:00:00:00:03|TenantA|300|"
            in result_stripped
        )
        # Check entries with MAC addresses reported over LLDP
        assert (
            "|swp10|[server6](mock_nb_url)|server6.sitea.example.com|--|02:00:00:00:00:10|--|TenantA|--|"
            in result_stripped
        )
        # Check LLDP-only entries (network devices without MAC addresses)
        assert (
            "|swp49|[mock_neighbor3](mock_nb_url)|mock_neighbor3|--|swp49|--|TenantA|--|"
            in result_stripped
        )
        assert (
            "|swp51|[mock_neighbor4](mock_nb_url)|mock_neighbor4|--|Ethernet1/2|--|TenantA|--|"
            in result_stripped
        )

        expected_search_attributes = {
            "DeviceID": ["mock_device_uuid"],
            "DeviceRole": ["mock_role"],
            "Site": ["SITEA"],
            "DeviceName": ["mock_device"],
            "DevicePlatform": ["cumulus-linux"],
        }
        desc = await handle.describe()
        search_attrs = desc.search_attributes
        for attr, val in expected_search_attributes.items():
            assert search_attrs[attr] == val

        assert mock_nats_client.return_value.publish.called == 1


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow_no_data(
    mock_time,
    mock_nats_client,
    env,
):
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    @activity.defn(name="get_device_mac_table")
    async def mock_get_device_mac_table_empty(_) -> DeviceMacTable:
        return DeviceMacTable(by_mac={}, by_interface={})

    @activity.defn(name="get_device_actual_neighbors")
    async def mock_get_device_actual_neighbors_empty(_) -> DeviceNeighborData:
        return DeviceNeighborData(neighbors={}, link_states={})

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[ConnectedHostMetadataWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_device_mac_table_empty,
            mock_get_device_actual_neighbors_empty,
            mock_get_host_data_by_macs,
            mock_get_host_data_by_names,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = ConnectedHostWorkflowInput(device_id="mock_device_uuid")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            ConnectedHostMetadataWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert result == "No MAC addresses or LLDP neighbors found on device."

        stages = await handle.query("stages")
        assert len(stages) == 3

        # Check that get_connected_host_data stage is marked as UNREACHABLE
        host_data_stage = stages[2]
        assert host_data_stage["name"] == "get_connected_host_data"
        assert host_data_stage["state"] == "UNREACHABLE"

        assert mock_nats_client.return_value.publish.called == 1


def test_interface_sort_key():
    """Test the interface_sort_key function with various interface name formats."""

    # Test basic numeric sorting
    assert interface_sort_key("swp1") == ("swp", [1])
    assert interface_sort_key("swp2") == ("swp", [2])
    assert interface_sort_key("swp10") == ("swp", [10])

    # Test sorting with sub-interfaces (s separator)
    assert interface_sort_key("swp1s1") == ("swp", [1, 1])
    assert interface_sort_key("swp1s2") == ("swp", [1, 2])
    assert interface_sort_key("swp2s1") == ("swp", [2, 1])
    assert interface_sort_key("swp2s2") == ("swp", [2, 2])

    # Test sorting with slash separators
    assert interface_sort_key("Ethernet1") == ("Ethernet", [1])
    assert interface_sort_key("Ethernet1/1") == ("Ethernet", [1, 1])
    assert interface_sort_key("Ethernet1/1/1") == ("Ethernet", [1, 1, 1])
    assert interface_sort_key("Ethernet1/2") == ("Ethernet", [1, 2])
    assert interface_sort_key("Ethernet2/1") == ("Ethernet", [2, 1])

    # Test vendor-specific formats
    assert interface_sort_key("ge-1/1/1") == ("ge-", [1, 1, 1])
    assert interface_sort_key("xe-1/1/1") == ("xe-", [1, 1, 1])
    assert interface_sort_key("ge-1/1/2") == ("ge-", [1, 1, 2])
    assert interface_sort_key("ge-2/1/1") == ("ge-", [2, 1, 1])

    # Test edge cases
    assert interface_sort_key("swp0") == ("swp", [0])
    assert interface_sort_key("swp49") == ("swp", [49])
    assert interface_sort_key("swp51") == ("swp", [51])

    # Test non-matching patterns (should return original string)
    assert interface_sort_key("invalid-interface") == ("invalid-interface", [])
    assert interface_sort_key("") == ("", [])
    assert interface_sort_key("swp") == ("swp", [])

    # Test that sorting works correctly
    interfaces = [
        "swp10",
        "swp2",
        "swp1",
        "swp1s2",
        "swp1s1",
        "swp2s1",
        "Ethernet10/1",
        "Ethernet2/1",
        "Ethernet1/1",
        "Ethernet1/1/1",
        "ge-2/1/1",
        "ge-1/1/2",
        "ge-1/1/1",
    ]

    sorted_interfaces = sorted(interfaces, key=interface_sort_key)

    expected_order = [
        "Ethernet1/1",
        "Ethernet1/1/1",
        "Ethernet2/1",
        "Ethernet10/1",
        "ge-1/1/1",
        "ge-1/1/2",
        "ge-2/1/1",
        "swp1",
        "swp1s1",
        "swp1s2",
        "swp2",
        "swp2s1",
        "swp10",
    ]

    assert sorted_interfaces == expected_order
