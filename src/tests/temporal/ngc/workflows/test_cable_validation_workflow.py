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
# pylint: disable=B101,C0115,C0116
"""Test Suite for Cable Validation Workflow"""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from tests.temporal.ngc.workflows.test_cable_validation_data import (
    DEVICE_CONNECTION_DATA_INVALID,
    DEVICE_CONNECTION_DATA_MAC_TABLE,
    DEVICE_CONNECTION_DATA_MAC_TABLE_INVALID,
    DEVICE_CONNECTION_DATA_MAC_VALIDATION,
    DEVICE_CONNECTION_DATA_VALID,
)

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_dcim_nautobot_2x.workflow_models import (
        network_device_from_nautobot_graphql,
    )

    from nv_config_manager.temporal.client.device import DeviceArpTable, DeviceMacTable
    from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
    from nv_config_manager.temporal.common.mixins.device import InterfaceData, NetworkDeviceData
    from nv_config_manager.temporal.common.mixins.stage import StageRuntimeFailure
    from nv_config_manager.temporal.ngc.activities.cable_validation import (
        DecorateResultActivityInput,
        DeviceNeighborData,
        InterfaceNeighborData,
        decorate_result,
        format_device_validation_result,
        format_results,
        validate_device_neighbors,
    )
    from nv_config_manager.temporal.ngc.activities.device import ValidateHostnameActivityOutput
    from nv_config_manager.temporal.ngc.activities.nautobot import (
        GetNetworkDevicesInput,
        GetNetworkDevicesOutput,
    )
    from nv_config_manager.temporal.ngc.workflows.cable_validation import (
        DeviceCableValidationInput,
        DeviceCableValidationResult,
        DeviceCableValidationWorkflow,
        InvalidCable,
        SiteCableValidationInput,
        SiteCableValidationWorkflow,
    )
    from tests.temporal.conftest import mock_publish_nats

ROLES_FOR_CABLE_VALIDATION = [
    "wan",
    "cin-core",
    "cin-spine",
    "cin-leaf",
    "tan-core",
    "tan-spine",
    "tan-leaf",
    "smn-core",
    "smn-spine",
    "smn-leaf",
]


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(
        devices=[
            network_device_from_nautobot_graphql(device)
            for device in DEVICE_CONNECTION_DATA_VALID.values()
            if device["location"]["name"] == activity_input.site
        ]
    )


@activity.defn(name="get_network_devices")
async def mock_get_network_devices_hostname_mismatch(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(
        devices=[
            network_device_from_nautobot_graphql(device)
            for device in DEVICE_CONNECTION_DATA_VALID.values()
            if device["location"]["name"] == activity_input.site
        ]
        + [
            NetworkDeviceData(
                id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
                name="mock_device_mismatch",
                rack="a01",
                position=1,
                role="cin-core",
                site=activity_input.site,
                device_type="mock_device_type",
                platform="cumulus-linux",
                primary_ip4="172.0.0.200",
                primary_ip6=None,
                device_bays=[],
                interfaces=[],
                render_enabled=True,
                deploy_enabled=True,
                backup_enabled=True,
                ztp_enabled=True,
                intent=None,
            )
        ]
    )


@activity.defn(name="get_network_devices")
async def mock_get_network_devices_empty(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(devices=[])


@activity.defn(name="get_device_intended_neighbors")
async def mock_get_device_intended_neighbors_valid(
    activity_input: NetworkDeviceData,
) -> DeviceNeighborData:
    return DeviceNeighborData(
        neighbors={
            interface["name"]: InterfaceNeighborData.from_graphql(interface)
            for interface in DEVICE_CONNECTION_DATA_VALID[activity_input.name][
                "intended_connections"
            ]
        },
        link_state_only=["swp0"],
        ignore=[],
    )


@activity.defn(name="get_device_actual_neighbors")
async def mock_get_device_actual_neighbors_valid(
    activity_input: NetworkDeviceData,
) -> DeviceNeighborData:
    raw = DeviceNeighborData(
        neighbors={
            interface["name"]: InterfaceNeighborData.from_graphql(interface)
            for interface in DEVICE_CONNECTION_DATA_VALID[activity_input.name]["actual_connections"]
        },
        link_states={
            interface["name"]: interface["link_up"]
            for interface in DEVICE_CONNECTION_DATA_VALID[activity_input.name]["actual_connections"]
        },
    )
    neighbors = {
        k: v for k, v in raw.neighbors.items() if v.name is not None or v.device_name or v.macs
    }
    return DeviceNeighborData(
        neighbors=neighbors,
        link_states=raw.link_states,
    )


@activity.defn(name="get_device_intended_neighbors")
async def mock_get_device_intended_neighbors_for_mac_validation(
    activity_input: NetworkDeviceData,
) -> DeviceNeighborData:
    return DeviceNeighborData(
        neighbors={
            interface["name"]: InterfaceNeighborData.from_graphql(interface)
            for interface in DEVICE_CONNECTION_DATA_MAC_VALIDATION[activity_input.name][
                "intended_connections"
            ]
        },
    )


@activity.defn(name="get_device_actual_neighbors")
async def mock_get_device_actual_neighbors_for_mac_validation(
    activity_input: NetworkDeviceData,
) -> DeviceNeighborData:
    return DeviceNeighborData(
        link_states={
            interface["name"]: interface["link_up"]
            for interface in DEVICE_CONNECTION_DATA_MAC_VALIDATION[activity_input.name][
                "actual_connections"
            ]
        },
    )


@activity.defn(name="get_device_mac_table")
async def mock_get_device_mac_table(_) -> DeviceMacTable:
    return DEVICE_CONNECTION_DATA_MAC_TABLE


@activity.defn(name="get_device_mac_table")
async def mock_get_device_mac_table_invalid(_) -> DeviceMacTable:
    return DEVICE_CONNECTION_DATA_MAC_TABLE_INVALID


@activity.defn(name="get_device_arp_table")
async def mock_get_device_arp_table(_):
    """Mock ARP table for testing."""
    return DeviceArpTable(
        ip_to_mac={"10.0.0.1": ["00-00-00-00-00-01"]},
        mac_to_ip={"00-00-00-00-00-01": ["10.0.0.1"]},
        interface_to_mac={"swp0": ["00-00-00-00-00-01"]},
    )


@activity.defn(name="get_device_arp_table")
async def mock_get_device_arp_table_invalid(_):
    """Mock invalid ARP table for testing."""
    return DeviceArpTable(
        ip_to_mac={"10.0.0.2": ["00-00-00-00-00-02"]},
        mac_to_ip={"00-00-00-00-00-02": ["10.0.0.2"]},
        interface_to_mac={"swp0": ["00-00-00-00-00-02"]},
    )


@activity.defn(name="get_ui_base_url")
async def mock_get_ui_base_url() -> str:
    """Mock UI base URL for testing."""
    return "test-ui.example.com"


@workflow.defn(name="DeviceCableValidationWorkflow")
class MockedDeviceCableValidationAllValid:
    @run_nv_config_manager_workflow
    async def run(self, _) -> DeviceCableValidationResult:
        return DeviceCableValidationResult()


@workflow.defn(name="DeviceCableValidationWorkflow")
class MockedDeviceCableValidationSomeInvalid:
    @run_nv_config_manager_workflow
    async def run(self, workflow_input: DeviceCableValidationInput) -> DeviceCableValidationResult:
        if workflow_input.device.name == "mock_device1":
            return DeviceCableValidationResult(
                interfaces=(
                    {
                        "swp0": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="swp0",
                                macs=["00-00-00-00-00-02"],
                                device_name="MOCK_DEVICE2",
                                device_serial="MOCKSERIAL2",
                                device_role="tenant-a-device",
                                device_rack="a01",
                                device_position=2,
                            ),
                            actual=InterfaceNeighborData(
                                name="Server BMC",
                                device_name="mock_server2",
                                macs=["00-00-00-00-00-03"],
                                link_up=True,
                            ),
                        ),
                        "swp1": InvalidCable(
                            intended=InterfaceNeighborData(
                                device_name="mock_server1",
                                device_role="tenant-a-device",
                                device_serial="J105K3WV",
                                macs=["08-8F-C3-A6-8A-9D"],
                                name="Server BMC",
                                device_rack="b01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(
                                device_name=None,
                                device_role=None,
                                device_serial=None,
                                macs=["08-8F-C3-A6-8A-9D"],
                                name="swp2",
                                link_up=False,
                                ts_info="Cable is unplugged.",
                            ),
                        ),
                        "swp23": InvalidCable(
                            intended=InterfaceNeighborData(
                                device_name="mock_server2",
                                device_role="tenant-a-device",
                                device_serial="J105K3WZ",
                                device_rack="b01",
                                device_position=2,
                                macs=["08-8F-C3-A6-8A-9F"],
                                name="MOCKSERVER2",
                            ),
                            actual=InterfaceNeighborData(
                                device_name=None,
                                device_role=None,
                                device_serial=None,
                                macs=[],
                                name=None,
                                link_up=False,
                                ts_info="Cable is unplugged.",
                            ),
                        ),
                    }
                )
            )
        elif workflow_input.device.name == "mock_device2":
            return DeviceCableValidationResult(
                interfaces=(
                    {
                        "swp9": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="DPU BMC",
                                macs=["00-00-00-00-00-09"],
                                device_name="mock_dpu1",
                                device_role="tenant-a-device",
                                device_rack="a01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(
                                name="DPU BMC",
                                device_name="mock_server1_dpu1",
                                macs=["00-00-00-00-00-1D", "00-00-00-00-00-FE"],
                                link_up=True,
                            ),
                        ),
                        # Duplicate neighor to test deduplication
                        "swp10": InvalidCable(
                            intended=InterfaceNeighborData(
                                device_name="mock_server1",
                                device_role="tenant-a-device",
                                device_serial="J105K3WV",
                                macs=["08-8F-C3-A6-8A-9D"],
                                name="Server BMC",
                                device_rack="b01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(
                                device_name=None,
                                device_serial=None,
                                macs=[],
                                name=None,
                                link_up=False,
                                ts_info="Cable is unplugged.",
                            ),
                        ),
                    }
                )
            )
        elif workflow_input.device.name == "mock_device3":
            return DeviceCableValidationResult(
                interfaces=(
                    {
                        "swp0": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="swp6",
                                macs=["00-00-00-00-00-02"],
                                device_name="mock_device2",
                                device_serial="MOCKSERIAL2",
                                device_role="tenant-a-device",
                                device_rack="a01",
                                device_position=2,
                            ),
                            actual=InterfaceNeighborData(
                                link_up=True,
                            ),
                        ),
                        "swp1": InvalidCable(
                            intended=None,
                            actual=InterfaceNeighborData(
                                name="swp7",
                                macs=["00-00-00-00-00-04"],
                                device_name="MOCK_DEVICE4",
                                device_serial="MOCKSERIAL4",
                                device_role="tenant-a-device",
                                link_up=True,
                            ),
                        ),
                        "swp5": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="swp8",
                                device_name="mock_device1",
                                device_role="tenant-a-device",
                                device_serial="MOCKSERIAL1",
                                macs=["00-00-00-00-00-DE"],
                                device_rack="a01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(
                                name="swp9",
                                device_name="mock_device1",
                                device_role="tenant-a-device",
                                device_serial="MOCKSERIAL1",
                                link_up=True,
                            ),
                        ),
                        "swp6": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="swp8",
                                device_name="mock_device1",
                                device_role="tenant-a-device",
                                device_serial="MOCKSERIAL1",
                                macs=["00-00-00-00-00-DE"],
                                device_rack="a01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(
                                name="00:00:00:00:a3:42",
                                device_name=None,
                                device_role="tenant-a-device",
                                device_serial="MOCKSERIAL1",
                                link_up=True,
                            ),
                        ),
                    }
                )
            )
        elif workflow_input.device.name == "MOCK-LEAF-04":
            return DeviceCableValidationResult(
                interfaces=(
                    {
                        "swp1": InvalidCable(
                            intended=InterfaceNeighborData(
                                name="eth1",
                                device_name="MOCK-Server-04",
                                device_role="tenant-a-device",
                                device_serial="MOCKSERIAL4",
                                device_rack="b01",
                                device_position=1,
                            ),
                            actual=InterfaceNeighborData(link_up=False),
                        )
                    }
                )
            )
        elif workflow_input.device.name == "mock_device_mismatch":
            raise StageRuntimeFailure(
                (
                    "Activity validate_hostname:0 in validate_device_hostname has failed "
                    "and cannot be retried: Hostname on 172.0.0.200 (mock_device_match) "
                    "does not match nautobot (mock_device_mismatch)."
                ),
                non_retryable=True,
            )
        else:
            return DeviceCableValidationResult()


@activity.defn(name="validate_hostname")
def mock_validate_hostname_match(
    device_data: NetworkDeviceData,
) -> ValidateHostnameActivityOutput:
    return ValidateHostnameActivityOutput(hostname=device_data.name)


@activity.defn(name="validate_hostname")
def mock_validate_hostname_mismatch(
    device_data: NetworkDeviceData,
) -> ValidateHostnameActivityOutput:
    raise ApplicationError(
        f"Hostname on {device_data.primary_ip4 or device_data.primary_ip6} "
        f"({device_data.name + 'mismatch'}) does not match nautobot "
        f"({device_data.name}).",
        non_retryable=True,
    )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_device_cable_validation_workflow_dpu_mac_offset(_, env):
    """Test DPU MAC offset validation where actual MAC is expected MAC + 0x10."""

    @activity.defn(name="get_device_actual_neighbors")
    async def mock_get_device_actual_neighbors_dpu_offset(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # Test DPU MAC offset scenario via LLDP interface name
                "swp1": InterfaceNeighborData(
                    name="00-00-00-00-00-19",  # MAC is intended MAC + 0x10 (0x09 + 0x10 = 0x19)
                    device_name="mock_dpu1",
                    device_serial="MOCKSERIAL_DPU1",
                    device_role="tenant-a-device",
                    device_rack="a01",
                    device_position=1,
                ),
                # Test DPU MAC offset scenario via LLDP device name
                "swp2": InterfaceNeighborData(
                    name="eth0",
                    device_name="00-00-00-00-00-29",  # MAC is intended MAC + 0x10 (0x19 + 0x10 = 0x29)
                    device_serial="MOCKSERIAL_DPU2",
                    device_role="tenant-a-device",
                    device_rack="a01",
                    device_position=2,
                ),
            },
            link_states={
                "swp1": True,
                "swp2": True,
            },
        )

    @activity.defn(name="get_device_intended_neighbors")
    async def mock_get_device_intended_neighbors_dpu_offset(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # DPU that advertises its MAC + 0x10 over LLDP as interface name
                "swp1": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_dpu1",
                    device_serial="MOCKSERIAL_DPU1",
                    device_role="tenant-a-device",
                    device_rack="a01",
                    device_position=1,
                    macs=["00-00-00-00-00-09"],  # Expected MAC
                ),
                # DPU that advertises its MAC + 0x10 over LLDP as device name
                "swp2": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_dpu2",
                    device_serial="MOCKSERIAL_DPU2",
                    device_role="tenant-a-device",
                    device_rack="a01",
                    device_position=2,
                    macs=["00-00-00-00-00-19"],  # Expected MAC
                ),
            },
        )

    @activity.defn(name="get_device_mac_table")
    async def mock_get_device_mac_table_dpu_offset(
        activity_input: NetworkDeviceData,
    ) -> DeviceMacTable:
        from nv_config_manager.temporal.client.device import DeviceMacTable

        # Empty MAC table - validation should pass purely on LLDP with MAC offset
        return DeviceMacTable(by_mac={}, by_interface={})

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[DeviceCableValidationWorkflow],
        activities=[
            mock_validate_hostname_match,
            mock_get_device_actual_neighbors_dpu_offset,
            mock_get_device_intended_neighbors_dpu_offset,
            decorate_result,
            format_device_validation_result,
            validate_device_neighbors,
            mock_get_device_mac_table_dpu_offset,
            mock_get_device_arp_table,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_INVALID["mock_device1"]
            ),
            device_id=DEVICE_CONNECTION_DATA_INVALID["mock_device1"]["id"],
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        # Should pass validation because MAC offset logic recognizes DPU MAC + 0x10
        assert result == DeviceCableValidationResult()

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_device_data"]["output"]["display"] == (
            "|    name    |rack|position|      role     | site|   platform  |\n"
            "|------------|----|--------|---------------|-----|-------------|\n"
            "|mock_device1| a01|    1   |tenant-a-device|SITEA|cumulus-linux|"
        )
        assert (
            stages_dict["validate_device_hostname"]["output"]["display"]
            == f"```{workflow_input.device.name}```"
        )
        assert stages_dict["get_device_intended_neighbors"]["output"]["display"] == (
            "|name|       macs      |device_name| device_serial |  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|----|-----------------|-----------|---------------|---------------|-----------|---------------|-------|-------|\n"
            "|eth0|00-00-00-00-00-09| mock_dpu1 |MOCKSERIAL_DPU1|tenant-a-device|    a01    |       1       |  None |  None |\n"
            "|eth0|00-00-00-00-00-19| mock_dpu2 |MOCKSERIAL_DPU2|tenant-a-device|    a01    |       2       |  None |  None |"
        )
        assert stages_dict["get_device_actual_neighbors"]["output"]["display"] == (
            "|       name      |macs|   device_name   | device_serial |  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|-----------------|----|-----------------|---------------|---------------|-----------|---------------|-------|-------|\n"
            "|00-00-00-00-00-19|    |    mock_dpu1    |MOCKSERIAL_DPU1|tenant-a-device|    a01    |       1       |  None |  None |\n"
            "|       eth0      |    |00-00-00-00-00-29|MOCKSERIAL_DPU2|tenant-a-device|    a01    |       2       |  None |  None |"
        )
        assert (
            stages_dict["get_device_mac_table"]["output"]["display"]
            == "No MAC table entries found."
        )
        assert (
            stages_dict["validate_connections"]["output"]["display"]
            == "All cable connections are valid."
        )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.cable_validation.create_dcim_client")
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_cable_validation_workflow_all_valid(_, mock_nb_client, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteCableValidationWorkflow, MockedDeviceCableValidationAllValid],
        activities=[
            mock_get_network_devices,
            mock_get_ui_base_url,
            mock_publish_nats,
            format_results,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = SiteCableValidationInput(
            site="SITEA",
            roles=ROLES_FOR_CABLE_VALIDATION,
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SiteCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
            search_attributes={"User": ["test"], "ReadRoles": ["test"], "ExecuteRoles": ["test"]},
        )

        result = await handle.result()

        assert result.markdown == "No invalid cabling found."

        stages = await handle.query("stages")
        assert stages[2]["output"]["display"] == "No invalid cabling found."

        # Validate search attributes inherited by child workflows
        for stage in stages:
            for child_workflow in stage["child_workflows"]:
                child_handle: WorkflowHandle = env.client.get_workflow_handle(child_workflow)
                description = await child_handle.describe()
                assert description.search_attributes["User"] == ["test"]
                assert description.search_attributes["ReadRoles"] == ["test"]
                assert description.search_attributes["ExecuteRoles"] == ["test"]
                assert "DeviceID" in description.search_attributes


# The site report links a two-tab Excel workbook. The .xlsx bytes are not
# byte-stable across runs (the zip embeds timestamps and document properties),
# so tests assert the link prefix plus the deterministic table body instead of
# pinning the exact base64.
EXCEL_LINK_PREFIX = (
    "[Download Excel](data:application/vnd.openxmlformats-officedocument."
    "spreadsheetml.sheet;base64,"
)
EXPECTED_CABLE_TABLE = (
    "|Start Device|Start Port|Intended End Device|    Intended End Port   |Actual End Device| Actual End Port |                                          Issue                                          |\n"
    "|------------|----------|-------------------|------------------------|-----------------|-----------------|-----------------------------------------------------------------------------------------|\n"
    "|mock_device1|   swp0   |    mock_device2   |          swp0          |   mock_server2  |    Server BMC   |Incorrect cabling, actual should match intended. Based on expected MAC 00-00-00-00-00-02*|\n"
    "|mock_device1|   swp1   |    mock_server1   |       Server BMC       |       None      |       None      |                                      Link is down.                                      |\n"
    "|mock_device1|   swp23  |    mock_server2   |       MOCKSERVER2      |       None      |       None      |                                      Link is down.                                      |\n"
    "|mock_device2|   swp9   |     mock_dpu1     |         DPU BMC        |mock_server1_dpu1|     DPU BMC     |Incorrect cabling, actual should match intended. Based on expected MAC 00-00-00-00-00-09*|\n"
    "|mock_device3|   swp0   |    mock_device2   |          swp6          |       None      |       None      |                             Link is up but no neighbor found                            |\n"
    "|mock_device3|   swp1   |        None       |          None          |   mock_device4  |       swp7      |                               Unexpected connection found                               |\n"
    "|mock_device3|   swp5   |    mock_device1   |          swp8          |   mock_device1  |       swp9      |           Incorrect cabling, actual should match intended. Based on LLDP data           |\n"
    "|mock_device3|   swp6   |    mock_device1   |swp8 (00:00:00:00:00:de)|       None      |00:00:00:00:a3:42|           Incorrect cabling, actual should match intended. Based on LLDP data           |\n\n"
    "*Either the expected MAC in our database is wrong, or this link is not cabled correctly."
)


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_cable_validation_workflow_some_invalid(_, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteCableValidationWorkflow, MockedDeviceCableValidationSomeInvalid],
        activities=[
            mock_get_network_devices,
            mock_get_ui_base_url,
            mock_publish_nats,
            format_results,
            mock_validate_hostname_match,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = SiteCableValidationInput(
            site="SITEA",
            roles=ROLES_FOR_CABLE_VALIDATION,
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SiteCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=10),
        )

        result = await handle.result()
        stages = await handle.query("stages")
        display = stages[2]["output"]["display"]

        assert display.startswith(EXCEL_LINK_PREFIX)
        assert display.endswith(EXPECTED_CABLE_TABLE)
        assert result.markdown == display


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_cable_validation_workflow_hostname_mismatch(_, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteCableValidationWorkflow, MockedDeviceCableValidationSomeInvalid],
        activities=[
            mock_get_network_devices_hostname_mismatch,
            mock_get_ui_base_url,
            mock_publish_nats,
            format_results,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = SiteCableValidationInput(
            site="SITEA",
            roles=ROLES_FOR_CABLE_VALIDATION,
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SiteCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=10),
            search_attributes={"User": ["test"]},
        )

        await handle.result()

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Get the list of devices to validate for this site",
                "execution_time": 0.0,
                "input": {
                    "device_type_ids": [],
                    "roles": [
                        "wan",
                        "cin-core",
                        "cin-spine",
                        "cin-leaf",
                        "tan-core",
                        "tan-spine",
                        "tan-leaf",
                        "smn-core",
                        "smn-spine",
                        "smn-leaf",
                    ],
                    "site": "SITEA",
                    "status": ["Active", "Provisioned"],
                    "tenant": None,
                },
                "name": "get_devices_to_validate",
                "output": {
                    "devices": [
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "msn4600-cs2fc",
                            "host": "10.0.0.1",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "name": "mock_device1",
                            "platform": "cumulus-linux",
                            "position": 1,
                            "primary_ip4": "10.0.0.1",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "msn4600-cs2fc",
                            "host": "10.0.0.2",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                            "name": "mock_device2",
                            "platform": "cumulus-linux",
                            "position": 2,
                            "primary_ip4": "10.0.0.2",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                            "backup_file": "full-config",
                            "intended_config_file": "full-config",
                            "deploy_enabled": True,
                            "device_type": "dcs-7368x-128-bnd-r",
                            "host": "10.0.0.3",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                            "name": "mock_device3",
                            "platform": "arista-eos",
                            "position": 3,
                            "primary_ip4": "10.0.0.3",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "",
                            "tenant_config_path": "",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "mock_device_type",
                            "host": "172.0.0.200",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "name": "mock_device_mismatch",
                            "platform": "cumulus-linux",
                            "position": 1,
                            "primary_ip4": "172.0.0.200",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "cin-core",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                    ],
                    "display": ANY,  # Until we add a cleaner to_markdown method to device data
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [
                    ANY,
                    ANY,
                    ANY,
                    ANY,
                ],
                "depends_on": ["get_devices_to_validate"],
                "description": "Validate all devices",
                "execution_time": 0.0,
                "input": {
                    "devices": [
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "msn4600-cs2fc",
                            "host": "10.0.0.1",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "name": "mock_device1",
                            "platform": "cumulus-linux",
                            "position": 1,
                            "primary_ip4": "10.0.0.1",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "msn4600-cs2fc",
                            "host": "10.0.0.2",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                            "name": "mock_device2",
                            "platform": "cumulus-linux",
                            "position": 2,
                            "primary_ip4": "10.0.0.2",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                            "backup_file": "full-config",
                            "intended_config_file": "full-config",
                            "deploy_enabled": True,
                            "device_type": "dcs-7368x-128-bnd-r",
                            "host": "10.0.0.3",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                            "name": "mock_device3",
                            "platform": "arista-eos",
                            "position": 3,
                            "primary_ip4": "10.0.0.3",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "tenant-a-device",
                            "site": "SITEA",
                            "tenant_config_file": "",
                            "tenant_config_path": "",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                        {
                            "backup_enabled": True,
                            "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "backup_file": "startup.yaml",
                            "intended_config_file": "startup.yaml",
                            "deploy_enabled": True,
                            "device_type": "mock_device_type",
                            "host": "172.0.0.200",
                            "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                            "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                            "name": "mock_device_mismatch",
                            "platform": "cumulus-linux",
                            "position": 1,
                            "primary_ip4": "172.0.0.200",
                            "primary_ip6": None,
                            "rack": "a01",
                            "render_enabled": True,
                            "role": "cin-core",
                            "site": "SITEA",
                            "tenant_config_file": "tenant.yaml",
                            "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                            "ztp_enabled": True,
                            "intent": None,
                        },
                    ],
                    "legacy_site": False,
                },
                "name": "validate_devices",
                "output": {
                    "devices": {
                        "mock_device1": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                                "backup_file": "startup.yaml",
                                "intended_config_file": "startup.yaml",
                                "deploy_enabled": True,
                                "device_type": "msn4600-cs2fc",
                                "host": "10.0.0.1",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                                "name": "mock_device1",
                                "platform": "cumulus-linux",
                                "position": 1,
                                "primary_ip4": "10.0.0.1",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "tenant.yaml",
                                "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp0": {
                                    "actual": {
                                        "device_name": "mock_server2",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": ["00-00-00-00-00-03"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "MOCK_DEVICE2",
                                        "device_position": 2,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL2",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-02"],
                                        "name": "swp0",
                                        "ts_info": None,
                                    },
                                },
                                "swp1": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "swp2",
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server1",
                                        "device_position": 1,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WV",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                },
                                "swp23": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server2",
                                        "device_position": 2,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WZ",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9F"],
                                        "name": "MOCKSERVER2",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                        "mock_device2": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                                "backup_file": "startup.yaml",
                                "intended_config_file": "startup.yaml",
                                "deploy_enabled": True,
                                "device_type": "msn4600-cs2fc",
                                "host": "10.0.0.2",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                                "name": "mock_device2",
                                "platform": "cumulus-linux",
                                "position": 2,
                                "primary_ip4": "10.0.0.2",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "tenant.yaml",
                                "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/tenant.yaml",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp9": {
                                    "actual": {
                                        "device_name": "mock_server1_dpu1",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": [
                                            "00-00-00-00-00-1D",
                                            "00-00-00-00-00-FE",
                                        ],
                                        "name": "DPU BMC",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_dpu1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": None,
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-09"],
                                        "name": "DPU BMC",
                                        "ts_info": None,
                                    },
                                },
                                "swp10": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server1",
                                        "device_position": 1,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WV",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                        "mock_device3": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                                "backup_file": "full-config",
                                "intended_config_file": "full-config",
                                "deploy_enabled": True,
                                "device_type": "dcs-7368x-128-bnd-r",
                                "host": "10.0.0.3",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                                "name": "mock_device3",
                                "platform": "arista-eos",
                                "position": 3,
                                "primary_ip4": "10.0.0.3",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "",
                                "tenant_config_path": "",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp0": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device2",
                                        "device_position": 2,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL2",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-02"],
                                        "name": "swp6",
                                        "ts_info": None,
                                    },
                                },
                                "swp1": {
                                    "actual": {
                                        "device_name": "MOCK_DEVICE4",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL4",
                                        "link_up": True,
                                        "macs": ["00-00-00-00-00-04"],
                                        "name": "swp7",
                                        "ts_info": None,
                                    },
                                    "intended": None,
                                },
                                "swp5": {
                                    "actual": {
                                        "device_name": "mock_device1",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": True,
                                        "macs": [],
                                        "name": "swp9",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-DE"],
                                        "name": "swp8",
                                        "ts_info": None,
                                    },
                                },
                                "swp6": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": True,
                                        "macs": [],
                                        "name": "00:00:00:00:a3:42",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-DE"],
                                        "name": "swp8",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                    },
                    "display": (
                        "**Device validation completed:**\n"
                        "- 3 devices validated successfully\n"
                        "- 1 devices failed"
                    ),
                    "failed_devices": {
                        "mock_device_mismatch": "Workflow failed: Activity validate_hostname:0 in validate_device_hostname has failed and cannot be retried: Hostname on 172.0.0.200 (mock_device_match) does not match nautobot (mock_device_mismatch)."
                    },
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["validate_devices"],
                "description": "Generate cable validation report",
                "execution_time": 0.0,
                "input": {
                    "devices": {
                        "mock_device1": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                                "backup_file": "startup.yaml",
                                "intended_config_file": "startup.yaml",
                                "deploy_enabled": True,
                                "device_type": "msn4600-cs2fc",
                                "host": "10.0.0.1",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/startup.yaml",
                                "name": "mock_device1",
                                "platform": "cumulus-linux",
                                "position": 1,
                                "primary_ip4": "10.0.0.1",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "tenant.yaml",
                                "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050273/tenant.yaml",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp0": {
                                    "actual": {
                                        "device_name": "mock_server2",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": ["00-00-00-00-00-03"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "MOCK_DEVICE2",
                                        "device_position": 2,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL2",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-02"],
                                        "name": "swp0",
                                        "ts_info": None,
                                    },
                                },
                                "swp1": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "swp2",
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server1",
                                        "device_position": 1,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WV",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                },
                                "swp23": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server2",
                                        "device_position": 2,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WZ",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9F"],
                                        "name": "MOCKSERVER2",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                        "mock_device2": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                                "backup_file": "startup.yaml",
                                "intended_config_file": "startup.yaml",
                                "deploy_enabled": True,
                                "device_type": "msn4600-cs2fc",
                                "host": "10.0.0.2",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/startup.yaml",
                                "name": "mock_device2",
                                "platform": "cumulus-linux",
                                "position": 2,
                                "primary_ip4": "10.0.0.2",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "tenant.yaml",
                                "tenant_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050232/tenant.yaml",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp9": {
                                    "actual": {
                                        "device_name": "mock_server1_dpu1",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": [
                                            "00-00-00-00-00-1D",
                                            "00-00-00-00-00-FE",
                                        ],
                                        "name": "DPU BMC",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_dpu1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": None,
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-09"],
                                        "name": "DPU BMC",
                                        "ts_info": None,
                                    },
                                },
                                "swp10": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": False,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": "Cable is unplugged.",
                                    },
                                    "intended": {
                                        "device_name": "mock_server1",
                                        "device_position": 1,
                                        "device_rack": "b01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "J105K3WV",
                                        "link_up": None,
                                        "macs": ["08-8F-C3-A6-8A-9D"],
                                        "name": "Server BMC",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                        "mock_device3": {
                            "device": {
                                "backup_enabled": True,
                                "backup_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                                "backup_file": "full-config",
                                "intended_config_file": "full-config",
                                "deploy_enabled": True,
                                "device_type": "dcs-7368x-128-bnd-r",
                                "host": "10.0.0.3",
                                "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
                                "intended_config_path": "c2c2b006-d4f6-4645-8ac8-a4a968050214/full-config",
                                "name": "mock_device3",
                                "platform": "arista-eos",
                                "position": 3,
                                "primary_ip4": "10.0.0.3",
                                "primary_ip6": None,
                                "rack": "a01",
                                "render_enabled": True,
                                "role": "tenant-a-device",
                                "site": "SITEA",
                                "tenant_config_file": "",
                                "tenant_config_path": "",
                                "ztp_enabled": True,
                                "intent": None,
                            },
                            "interfaces": {
                                "swp0": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": None,
                                        "device_serial": None,
                                        "link_up": True,
                                        "macs": [],
                                        "name": None,
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device2",
                                        "device_position": 2,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL2",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-02"],
                                        "name": "swp6",
                                        "ts_info": None,
                                    },
                                },
                                "swp1": {
                                    "actual": {
                                        "device_name": "MOCK_DEVICE4",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL4",
                                        "link_up": True,
                                        "macs": ["00-00-00-00-00-04"],
                                        "name": "swp7",
                                        "ts_info": None,
                                    },
                                    "intended": None,
                                },
                                "swp5": {
                                    "actual": {
                                        "device_name": "mock_device1",
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": True,
                                        "macs": [],
                                        "name": "swp9",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-DE"],
                                        "name": "swp8",
                                        "ts_info": None,
                                    },
                                },
                                "swp6": {
                                    "actual": {
                                        "device_name": None,
                                        "device_position": None,
                                        "device_rack": None,
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": True,
                                        "macs": [],
                                        "name": "00:00:00:00:a3:42",
                                        "ts_info": None,
                                    },
                                    "intended": {
                                        "device_name": "mock_device1",
                                        "device_position": 1,
                                        "device_rack": "a01",
                                        "device_role": "tenant-a-device",
                                        "device_serial": "MOCKSERIAL1",
                                        "link_up": None,
                                        "macs": ["00-00-00-00-00-DE"],
                                        "name": "swp8",
                                        "ts_info": None,
                                    },
                                },
                            },
                        },
                    },
                    "failed_devices": {
                        "mock_device_mismatch": "Workflow failed: Activity validate_hostname:0 in validate_device_hostname has failed and cannot be retried: Hostname on 172.0.0.200 (mock_device_match) does not match nautobot (mock_device_mismatch)."
                    },
                    "legacy_site": False,
                },
                "name": "format_result",
                "output": {
                    "display": ANY,
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
        ]

        format_display = stages[2]["output"]["display"]
        assert format_display.startswith(
            "### Failed Devices\n"
            "Address the listed issues and re-run the workflow for complete results.\n\n"
        )
        assert EXCEL_LINK_PREFIX in format_display
        assert format_display.endswith(EXPECTED_CABLE_TABLE)


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_device_cable_validation_workflow_valid(_, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[DeviceCableValidationWorkflow],
        activities=[
            mock_get_device_actual_neighbors_valid,
            mock_get_device_intended_neighbors_valid,
            validate_device_neighbors,
            decorate_result,
            mock_get_device_mac_table,
            mock_get_device_arp_table,
            mock_publish_nats,
            mock_validate_hostname_match,
            format_device_validation_result,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_INVALID["mock_device1"]
            ),
            device_id=DEVICE_CONNECTION_DATA_INVALID["mock_device1"]["id"],
        )

        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result == DeviceCableValidationResult()

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_device_data"]["output"]["display"] == (
            "|    name    |rack|position|      role     | site|   platform  |\n"
            "|------------|----|--------|---------------|-----|-------------|\n"
            "|mock_device1| a01|    1   |tenant-a-device|SITEA|cumulus-linux|"
        )
        assert (
            stages_dict["validate_device_hostname"]["output"]["display"]
            == f"```{workflow_input.device.name}```"
        )
        assert stages_dict["get_device_intended_neighbors"]["output"]["display"] == (
            "|    name   |       macs      | device_name|device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|-----------|-----------------|------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|    swp0   |                 |mock_device2| MOCKSERIAL2 |tenant-a-device|    a01    |       2       |  None |  None |\n"
            "|    swp1   |00-00-00-00-00-02|mock_device2| MOCKSERIAL2 |tenant-a-device|    a01    |       2       |  None |  None |\n"
            "|Ethernet1/1|00-00-00-00-00-03|mock_device3| MOCKSERIAL3 |tenant-a-device|    a01    |       3       |  None |  None |\n"
            "|Ethernet1/2|00-00-00-00-00-03|mock_device3| MOCKSERIAL3 |tenant-a-device|    a01    |       3       |  None |  None |"
        )
        assert stages_dict["get_device_actual_neighbors"]["output"]["display"] == (
            "|    name   |       macs      | device_name|device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|-----------|-----------------|------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|    swp0   |                 |mock_device2|     None    |tenant-a-device|    None   |      None     |  None |  None |\n"
            "|    swp1   |00-00-00-00-00-02|mock_device2|     None    |tenant-a-device|    None   |      None     |  None |  None |\n"
            "|Ethernet1/1|                 |mock_device3|     None    |tenant-a-device|    None   |      None     |  None |  None |\n"
            "|Ethernet1/2|00-00-00-00-00-03|mock_device3|     None    |tenant-a-device|    None   |      None     |  None |  None |"
        )
        assert stages_dict["get_device_mac_table"]["output"]["display"] == (
            "|       mac       |interface|  age  |vlan|\n"
            "|-----------------|---------|-------|----|\n"
            "|00-00-00-00-00-01|   swp0  |2798641|None|\n"
            "|00-00-00-00-00-02|   swp1  |2798641|None|\n"
            "|00-00-00-00-00-03|   swp2  |2798641|None|"
        )
        assert (
            stages_dict["validate_connections"]["output"]["display"]
            == "All cable connections are valid."
        )

        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_VALID["MOCK-LEAF-04"]
            ),
            device_id=DEVICE_CONNECTION_DATA_VALID["MOCK-LEAF-04"]["id"],
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result == DeviceCableValidationResult()

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_device_data"]["output"]["display"] == (
            "|    name    |rack|position|      role     |  site  |   platform  |\n"
            "|------------|----|--------|---------------|--------|-------------|\n"
            "|MOCK-LEAF-04| a01|    4   |tenant-a-device|LEGACY01|cumulus-linux|"
        )
        assert (
            stages_dict["validate_device_hostname"]["output"]["display"]
            == f"```{workflow_input.device.name}```"
        )
        assert stages_dict["get_device_intended_neighbors"]["output"]["display"] == (
            "|name|       macs      |  device_name |device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|----|-----------------|--------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|eth1|00-00-00-00-00-44|MOCK-Server-04| MOCKSERIAL4 |tenant-a-device|    b01    |       4       |  None |  None |"
        )
        assert stages_dict["get_device_actual_neighbors"]["output"]["display"] == (
            "No actual neighbors found."
        )
        assert stages_dict["get_device_mac_table"]["output"]["display"] == (
            "|       mac       |interface|  age  |vlan|\n"
            "|-----------------|---------|-------|----|\n"
            "|00-00-00-00-00-01|   swp0  |2798641|None|\n"
            "|00-00-00-00-00-02|   swp1  |2798641|None|\n"
            "|00-00-00-00-00-03|   swp2  |2798641|None|"
        )
        assert (
            stages_dict["validate_connections"]["output"]["display"]
            == "All cable connections are valid."
        )


@pytest.mark.asyncio
async def test_execute_device_cable_validation_workflow_invalid(env):
    # Test scenarios for cable validation:
    # swp1: intended present, actual link down, no lldp or mac data
    # swp2: mac table matches intended mac, no lldp
    # swp3: lldp matches intended, no mac
    # swp4: lldp mismatch, no mac
    # swp5: mac mismatch, no lldp data
    # swp6: mac table doesn't match intended mac but arp table does
    # swp7: mac table and ARP table don't match intended mac

    @activity.defn(name="get_device_actual_neighbors")
    async def mock_get_device_actual_neighbors(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # swp2: No LLDP data (MAC table will match instead)
                # swp3: LLDP matches intended, no mac
                "swp3": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server2",
                    device_serial="MOCKSERIAL2",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=2,
                    macs=["00-00-00-00-00-33"],
                ),
                # swp4: LLDP mismatch, no mac
                "swp4": InterfaceNeighborData(
                    name="eth0",  # Different from intended
                    device_name="mock_server3",  # Different from intended
                    device_serial="MOCKSERIAL3",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=3,
                    macs=["00-00-00-00-00-44"],
                ),
            },
            link_states={
                "swp1": False,  # Link down
                "swp2": True,  # Link up
                "swp3": True,  # Link up
                "swp4": True,  # Link up
                "swp5": True,  # Link up but no LLDP
                "swp6": True,  # Link up but no LLDP
                "swp7": True,  # Link up but no LLDP
            },
        )

    @activity.defn(name="get_device_intended_neighbors")
    async def mock_get_device_intended_neighbors(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # swp1: intended present, actual link down
                "swp1": InterfaceNeighborData(
                    name="Server BMC",
                    device_name="mock_server0",
                    device_serial="MOCKSERIAL0",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=0,
                    macs=["00-00-00-00-00-11"],
                ),
                # swp2: mac table matches intended mac, no lldp
                "swp2": InterfaceNeighborData(
                    name="Server BMC",
                    device_name="mock_server1",
                    device_serial="MOCKSERIAL1",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=1,
                    macs=["00-00-00-00-00-22"],
                ),
                # swp3: lldp matches intended, no mac
                "swp3": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server2",
                    device_serial="MOCKSERIAL2",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=2,
                    macs=["00-00-00-00-00-33"],
                ),
                # swp4: lldp mismatch, no mac
                "swp4": InterfaceNeighborData(
                    name="Server BMC",  # Different from actual
                    device_name="mock_server4",  # Different from actual
                    device_serial="MOCKSERIAL4",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=4,
                    macs=["00-00-00-00-00-44"],
                ),
                # swp5: mac mismatch, no lldp data
                "swp5": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server5",
                    device_serial="MOCKSERIAL5",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=5,
                    macs=["00-00-00-00-00-55"],
                ),
                # swp6: mac table doesn't match intended mac but arp table does
                "swp6": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server6",
                    device_serial="MOCKSERIAL6",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=6,
                    macs=["00-00-00-00-00-66"],
                ),
                # swp7: mac table and ARP table don't match intended mac
                "swp7": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server7",
                    device_serial="MOCKSERIAL7",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=7,
                    macs=["00-00-00-00-00-77"],
                ),
            },
        )

    @activity.defn(name="get_device_mac_table")
    async def mock_get_device_mac_table(
        activity_input: NetworkDeviceData,
    ) -> DeviceMacTable:
        from nv_config_manager.temporal.client.device import DeviceMacEntry, DeviceMacTable

        return DeviceMacTable(
            by_mac={
                # swp2: MAC matches intended (no LLDP)
                "00-00-00-00-00-22": DeviceMacEntry(
                    interface="swp2", mac="00-00-00-00-00-22", age=100
                ),
                # swp5: MAC mismatch - intended was 00-00-00-00-00-55 (no LLDP)
                "00-00-00-00-00-99": DeviceMacEntry(
                    interface="swp5", mac="00-00-00-00-00-99", age=200
                ),
                # Note: swp3 intentionally excluded - should only match via LLDP
                # Note: swp1, swp4 have no MAC data
            },
            by_interface={
                "swp2": ["00-00-00-00-00-22"],
                "swp5": ["00-00-00-00-00-99"],
                # swp3 intentionally excluded from MAC table
                # swp6 and swp7 intentionally excluded from MAC table
            },
        )

    @activity.defn(name="get_device_arp_table")
    async def mock_get_device_arp_table(
        activity_input: NetworkDeviceData,
    ) -> DeviceArpTable:
        return DeviceArpTable(
            ip_to_mac={
                "10.0.0.6": ["00-00-00-00-00-66"],
                "10.0.0.7": ["00-00-00-00-00-88"],
            },
            mac_to_ip={"00-00-00-00-00-66": ["10.0.0.6"], "00-00-00-00-00-88": ["10.0.0.7"]},
            interface_to_mac={"swp6": ["00-00-00-00-00-66"], "swp7": ["00-00-00-00-00-88"]},
        )

    # Set Nautobot mock before Worker so activity thread sees it
    mock_interfaces = [
        InterfaceData(
            name="swp5-remote",
            id="if-99",
            host="mock_server5_peer",
            mac_address="00-00-00-00-00-99",
            vrf_id=None,
        ),
        InterfaceData(
            name="eth7-remote",
            id="if-88",
            host="mock_server7_peer",
            mac_address="00-00-00-00-00-88",
            vrf_id=None,
        ),
    ]

    async def _mock_get_interface_hosts_by_mac(*args, **kwargs):
        return mock_interfaces

    mock_nb_instance = AsyncMock()
    mock_nb_instance.get_interface_hosts_by_mac = _mock_get_interface_hosts_by_mac
    mock_nb_instance.__aenter__ = AsyncMock(return_value=mock_nb_instance)
    mock_nb_instance.__aexit__ = AsyncMock(return_value=None)
    mock_nb_class = MagicMock(return_value=mock_nb_instance)

    @activity.defn(name="decorate_result")
    async def _decorate_result_with_mock(activity_input: DecorateResultActivityInput):
        with patch(
            "nv_config_manager.temporal.ngc.activities.cable_validation.create_dcim_client",
            mock_nb_class,
        ):
            return await decorate_result(activity_input)

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[DeviceCableValidationWorkflow],
        activities=[
            mock_validate_hostname_match,
            mock_get_device_actual_neighbors,
            mock_get_device_intended_neighbors,
            _decorate_result_with_mock,
            format_device_validation_result,
            validate_device_neighbors,
            mock_get_device_mac_table,
            mock_get_device_arp_table,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_INVALID["mock_device1"]
            ),
            device_id=DEVICE_CONNECTION_DATA_INVALID["mock_device1"]["id"],
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )

        result = await handle.result()
        assert result == DeviceCableValidationResult(
            interfaces={
                "swp1": InvalidCable(
                    intended=InterfaceNeighborData(
                        name="Server BMC",
                        macs=["00-00-00-00-00-11"],
                        device_name="mock_server0",
                        device_serial="MOCKSERIAL0",
                        device_role="tenant-a-device",
                        device_rack="b01",
                        device_position=0,
                        link_up=None,
                        ts_info=None,
                    ),
                    actual=InterfaceNeighborData(
                        name=None,
                        macs=[],
                        device_name=None,
                        device_serial=None,
                        device_role=None,
                        device_rack=None,
                        device_position=None,
                        link_up=False,
                        ts_info=None,
                    ),
                ),
                "swp4": InvalidCable(
                    intended=InterfaceNeighborData(
                        name="Server BMC",
                        macs=["00-00-00-00-00-44"],
                        device_name="mock_server4",
                        device_serial="MOCKSERIAL4",
                        device_role="tenant-a-device",
                        device_rack="b01",
                        device_position=4,
                        link_up=None,
                        ts_info=None,
                    ),
                    actual=InterfaceNeighborData(
                        name="eth0",
                        macs=["00-00-00-00-00-44"],
                        device_name="mock_server3",
                        device_serial="MOCKSERIAL3",
                        device_role="tenant-a-device",
                        device_rack="b01",
                        device_position=3,
                        link_up=None,
                        ts_info=None,
                    ),
                ),
                "swp5": InvalidCable(
                    intended=InterfaceNeighborData(
                        name="eth0",
                        macs=["00-00-00-00-00-55"],
                        device_name="mock_server5",
                        device_serial="MOCKSERIAL5",
                        device_role="tenant-a-device",
                        device_rack="b01",
                        device_position=5,
                        link_up=None,
                        ts_info=None,
                    ),
                    actual=InterfaceNeighborData(
                        name="swp5-remote",
                        macs=["00-00-00-00-00-99"],
                        device_name="mock_server5_peer",
                        device_serial=None,
                        device_role=None,
                        device_rack=None,
                        device_position=None,
                        link_up=True,
                        ts_info=None,
                    ),
                ),
                "swp7": InvalidCable(
                    intended=InterfaceNeighborData(
                        name="eth0",
                        macs=["00-00-00-00-00-77"],
                        device_name="mock_server7",
                        device_serial="MOCKSERIAL7",
                        device_role="tenant-a-device",
                        device_rack="b01",
                        device_position=7,
                        link_up=None,
                        ts_info=None,
                    ),
                    actual=InterfaceNeighborData(
                        name="eth7-remote",
                        macs=["00-00-00-00-00-88"],
                        device_name="mock_server7_peer",
                        device_serial=None,
                        device_role=None,
                        device_rack=None,
                        device_position=None,
                        link_up=True,
                        ts_info=None,
                    ),
                ),
            }
        )

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_device_data"]["output"]["display"] == (
            "|    name    |rack|position|      role     | site|   platform  |\n"
            "|------------|----|--------|---------------|-----|-------------|\n"
            "|mock_device1| a01|    1   |tenant-a-device|SITEA|cumulus-linux|"
        )
        assert (
            stages_dict["validate_device_hostname"]["output"]["display"]
            == f"```{workflow_input.device.name}```"
        )
        assert stages_dict["get_device_intended_neighbors"]["output"]["display"] == (
            "|   name   |       macs      | device_name|device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|----------|-----------------|------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|Server BMC|00-00-00-00-00-11|mock_server0| MOCKSERIAL0 |tenant-a-device|    b01    |       0       |  None |  None |\n"
            "|Server BMC|00-00-00-00-00-22|mock_server1| MOCKSERIAL1 |tenant-a-device|    b01    |       1       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-33|mock_server2| MOCKSERIAL2 |tenant-a-device|    b01    |       2       |  None |  None |\n"
            "|Server BMC|00-00-00-00-00-44|mock_server4| MOCKSERIAL4 |tenant-a-device|    b01    |       4       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-55|mock_server5| MOCKSERIAL5 |tenant-a-device|    b01    |       5       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-66|mock_server6| MOCKSERIAL6 |tenant-a-device|    b01    |       6       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-77|mock_server7| MOCKSERIAL7 |tenant-a-device|    b01    |       7       |  None |  None |"
        )
        assert stages_dict["get_device_actual_neighbors"]["output"]["display"] == (
            "|name|       macs      | device_name|device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|----|-----------------|------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|eth0|00-00-00-00-00-33|mock_server2| MOCKSERIAL2 |tenant-a-device|    b01    |       2       |  None |  None |\n"
            "|eth0|00-00-00-00-00-44|mock_server3| MOCKSERIAL3 |tenant-a-device|    b01    |       3       |  None |  None |"
        )
        assert stages_dict["get_device_mac_table"]["output"]["display"] == (
            "|       mac       |interface|age|vlan|\n"
            "|-----------------|---------|---|----|\n"
            "|00-00-00-00-00-22|   swp2  |100|None|\n"
            "|00-00-00-00-00-99|   swp5  |200|None|"
        )
        validate_display = stages_dict["validate_connections"]["output"]["display"]
        assert validate_display.startswith("[Export to CSV](data:text/csv;base64,")


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_device_cable_validation_workflow_mac_validation_all_valid(
    _,
    env,
):
    # Test 4 healthy scenarios:
    # swp2: mac table matches intended mac, no lldp
    # swp3: lldp matches intended, no mac
    # swp4: lldp with interface MAC instead of name
    # swp5: lldp with device MAC instead of device name

    @activity.defn(name="get_device_actual_neighbors")
    async def mock_get_device_actual_neighbors_healthy(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # swp3: LLDP matches intended, no mac
                "swp3": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server2",
                    device_serial="MOCKSERIAL2",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=2,
                    macs=["00-00-00-00-00-33"],
                ),
                # swp4: LLDP with interface MAC instead of name
                "swp4": InterfaceNeighborData(
                    name="00-00-00-00-00-44",  # MAC address instead of interface name
                    device_name="mock_server4",
                    device_serial="MOCKSERIAL4",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=4,
                ),
                # swp5: LLDP with device MAC instead of device name
                "swp5": InterfaceNeighborData(
                    name="eth0",
                    device_name="00-00-00-00-00-55",  # MAC address instead of device name
                    device_serial="MOCKSERIAL5",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=5,
                ),
            },
            link_states={
                "swp2": True,  # Link up for MAC validation
                "swp3": True,  # Link up for LLDP validation
                "swp4": True,  # Link up for LLDP with interface MAC
                "swp5": True,  # Link up for LLDP with device MAC
            },
        )

    @activity.defn(name="get_device_intended_neighbors")
    async def mock_get_device_intended_neighbors_healthy(
        activity_input: NetworkDeviceData,
    ) -> DeviceNeighborData:
        return DeviceNeighborData(
            neighbors={
                # swp2: mac table matches intended mac, no lldp
                "swp2": InterfaceNeighborData(
                    name="Server BMC",
                    device_name="mock_server1",
                    device_serial="MOCKSERIAL1",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=1,
                    macs=["00-00-00-00-00-22"],
                ),
                # swp3: lldp matches intended, no mac
                "swp3": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server2",
                    device_serial="MOCKSERIAL2",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=2,
                    macs=["00-00-00-00-00-33"],
                ),
                # swp4: intended with MAC that matches actual interface name
                "swp4": InterfaceNeighborData(
                    name="eth0",  # Normal interface name
                    device_name="mock_server4",
                    device_serial="MOCKSERIAL4",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=4,
                    macs=["00-00-00-00-00-44"],  # This MAC matches actual interface name
                ),
                # swp5: intended with MAC that matches actual device name
                "swp5": InterfaceNeighborData(
                    name="eth0",
                    device_name="mock_server5",  # Normal device name
                    device_serial="MOCKSERIAL5",
                    device_role="tenant-a-device",
                    device_rack="b01",
                    device_position=5,
                    macs=["00-00-00-00-00-55"],  # This MAC matches actual device name
                ),
            },
        )

    @activity.defn(name="get_device_mac_table")
    async def mock_get_device_mac_table_healthy(
        activity_input: NetworkDeviceData,
    ) -> DeviceMacTable:
        from nv_config_manager.temporal.client.device import DeviceMacEntry, DeviceMacTable

        return DeviceMacTable(
            by_mac={
                # swp2: MAC matches intended (no LLDP)
                "00-00-00-00-00-22": DeviceMacEntry(
                    interface="swp2", mac="00-00-00-00-00-22", age=100
                ),
                # swp3 intentionally excluded from MAC table - should validate via LLDP
            },
            by_interface={
                "swp2": ["00-00-00-00-00-22"],
                # swp3 intentionally excluded from MAC table
            },
        )

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[DeviceCableValidationWorkflow],
        activities=[
            mock_validate_hostname_match,
            mock_get_device_actual_neighbors_healthy,
            mock_get_device_intended_neighbors_healthy,
            decorate_result,
            format_device_validation_result,
            validate_device_neighbors,
            mock_get_device_mac_table_healthy,
            mock_get_device_arp_table,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_MAC_VALIDATION["MOCK-IPMITOR-01"]
            ),
            device_id=DEVICE_CONNECTION_DATA_MAC_VALIDATION["MOCK-IPMITOR-01"]["id"],
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result == DeviceCableValidationResult()

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_device_data"]["output"]["display"] == (
            "|      name     |rack|position|      role     |  site  |   platform  |\n"
            "|---------------|----|--------|---------------|--------|-------------|\n"
            "|MOCK-IPMITOR-01| a01|    1   |tenant-a-device|LEGACY01|cumulus-linux|"
        )
        assert (
            stages_dict["validate_device_hostname"]["output"]["display"]
            == f"```{workflow_input.device.name}```"
        )
        assert stages_dict["get_device_intended_neighbors"]["output"]["display"] == (
            "|   name   |       macs      | device_name|device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|----------|-----------------|------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|Server BMC|00-00-00-00-00-22|mock_server1| MOCKSERIAL1 |tenant-a-device|    b01    |       1       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-33|mock_server2| MOCKSERIAL2 |tenant-a-device|    b01    |       2       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-44|mock_server4| MOCKSERIAL4 |tenant-a-device|    b01    |       4       |  None |  None |\n"
            "|   eth0   |00-00-00-00-00-55|mock_server5| MOCKSERIAL5 |tenant-a-device|    b01    |       5       |  None |  None |"
        )
        assert stages_dict["get_device_actual_neighbors"]["output"]["display"] == (
            "|       name      |       macs      |   device_name   |device_serial|  device_role  |device_rack|device_position|link_up|ts_info|\n"
            "|-----------------|-----------------|-----------------|-------------|---------------|-----------|---------------|-------|-------|\n"
            "|       eth0      |00-00-00-00-00-33|   mock_server2  | MOCKSERIAL2 |tenant-a-device|    b01    |       2       |  None |  None |\n"
            "|00-00-00-00-00-44|                 |   mock_server4  | MOCKSERIAL4 |tenant-a-device|    b01    |       4       |  None |  None |\n"
            "|       eth0      |                 |00-00-00-00-00-55| MOCKSERIAL5 |tenant-a-device|    b01    |       5       |  None |  None |"
        )
        assert stages_dict["get_device_mac_table"]["output"]["display"] == (
            "|       mac       |interface|age|vlan|\n"
            "|-----------------|---------|---|----|\n"
            "|00-00-00-00-00-22|   swp2  |100|None|"
        )
        assert (
            stages_dict["validate_connections"]["output"]["display"]
            == "All cable connections are valid."
        )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_device_cable_validation_workflow_hostname_mismatch(_, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[DeviceCableValidationWorkflow],
        activities=[
            mock_validate_hostname_mismatch,
            mock_get_device_actual_neighbors_for_mac_validation,
            mock_get_device_intended_neighbors_for_mac_validation,
            decorate_result,
            validate_device_neighbors,
            mock_get_device_mac_table_invalid,
            mock_get_device_arp_table_invalid,
        ],
        activity_executor=ThreadPoolExecutor(2),
    ):
        workflow_input = DeviceCableValidationInput(
            device=network_device_from_nautobot_graphql(
                DEVICE_CONNECTION_DATA_MAC_VALIDATION["MOCK-IPMITOR-01"]
            ),
            device_id=DEVICE_CONNECTION_DATA_MAC_VALIDATION["MOCK-IPMITOR-01"]["id"],
        )
        workflow_id = str(uuid.uuid4())

        handle = await env.client.start_workflow(
            DeviceCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )
        with pytest.raises(WorkflowFailureError) as error:
            await handle.result()

        assert re.match(
            (
                r"Workflow failed: Activity validate_hostname:[\w-]+ in "
                r"validate_device_hostname has failed and cannot be retried: "
                r"Hostname on 10\.0\.0\.1 \(MOCK-IPMITOR-01mismatch\) does not match nautobot "
                r"\(MOCK-IPMITOR-01\)\."
            ),
            str(error.value.cause),
        )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_site_cable_validation_no_devices_found(_, env):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteCableValidationWorkflow],
        activities=[
            mock_get_network_devices_empty,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = SiteCableValidationInput(
            site="NONEXISTENT_SITE",
            roles=ROLES_FOR_CABLE_VALIDATION,
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SiteCableValidationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert result.markdown == "No devices found matching the specified filters."

        stages = await handle.query("stages")
        stages_dict = {s["name"]: s for s in stages}
        assert stages_dict["get_devices_to_validate"]["state"] == "COMPLETE"
        assert (
            "No devices found matching the specified filters"
            in stages_dict["get_devices_to_validate"]["output"]["display"]
        )
        assert stages_dict["validate_devices"]["state"] == "UNREACHABLE"
        assert stages_dict["format_result"]["state"] == "UNREACHABLE"
