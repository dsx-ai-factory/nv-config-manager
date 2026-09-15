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
"""Test Suite for Cable Validation Workflow"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from nv_config_manager_dcim_nautobot_2x.workflow_models import (
        host_device_from_nautobot_graphql,
        network_device_from_nautobot_graphql,
    )

    from nv_config_manager.temporal.client.device import DeviceNeighborData
    from nv_config_manager.temporal.client.redfish import (
        RedfishDpu,
        RedfishDpuPort,
        RedfishHost,
        RedfishNic,
        RedfishServer,
        RedfishVendor,
    )
    from nv_config_manager.temporal.common.mixins.device import (
        HostDeviceData,
        InterfaceData,
        NetworkDeviceData,
    )
    from nv_config_manager.temporal.ngc.activities.bmc import (
        DiscoverHostsInput,
        DiscoverHostsOutput,
        GetDpuDetailsActivityInput,
        GetDpuDetailsActivityOutput,
        GetServerDetailsActivityInput,
        GetServerDetailsActivityOutput,
        PopulateRedfishMacsInput,
        PopulateRedfishMacsOutput,
        RedfishHostOutput,
        UpdateDpuDataActivityInput,
        UpdateDpuDataActivityOutput,
    )
    from nv_config_manager.temporal.ngc.activities.nautobot import (
        GetHostDeviceInput,
        GetHostDeviceOutput,
        GetHostDevicesInput,
        GetHostDevicesOutput,
        GetNetworkDevicesInput,
        GetNetworkDevicesOutput,
    )
    from nv_config_manager.temporal.ngc.workflows.bmc import (
        NIC_MANUFACTURER_MELLANOX,
        RedfishProvisioningInput,
        RedfishProvisioningResult,
        RedfishProvisioningWorkflow,
    )
    from tests.temporal.ngc.activities.test_bmc_data import (
        TEST_ARP_TABLES,
        TEST_BMC_SWITCHES,
        TEST_DPU_DEVICES,
        TEST_SERVERS,
    )


@activity.defn(name="discover_redfish_hosts")
def mock_discover_redfish_hosts(
    activity_input: DiscoverHostsInput,
) -> DiscoverHostsOutput:
    if ["127.0.0.1", "127.0.0.2"] == activity_input.ips_excluded:
        return DiscoverHostsOutput(
            hosts=[
                RedfishHost(
                    address="127.0.0.3",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac=None,
                ),
                RedfishHost(
                    address="127.0.0.4",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac=None,
                ),
                RedfishHost(
                    address="127.0.0.5",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac=None,
                ),
            ]
        )
    return DiscoverHostsOutput(
        hosts=[
            RedfishHost(
                address="127.0.0.1",
                port=443,
                vendor=RedfishVendor.DELL,
                mac=None,
            ),
            RedfishHost(
                address="127.0.0.2",
                port=443,
                vendor=RedfishVendor.LENOVO,
                mac=None,
            ),
        ]
    )


def set_redfish_password(
    activity_input: dict[str, Any],
) -> RedfishHostOutput:
    return RedfishHostOutput(host=RedfishHost(**activity_input["host"]))


def power_on_host(
    activity_input: dict[str, Any],
) -> RedfishHostOutput:
    host = (
        RedfishHostOutput(host=None)
        if activity_input["host"]["address"] in ("127.0.0.3", "127.0.0.4", "127.0.0.5")
        else RedfishHostOutput(host=RedfishHost(**activity_input["host"]))
    )
    return host


def factory_reset_bmc(
    activity_input: dict[str, Any],
) -> RedfishHostOutput:
    return RedfishHostOutput(host=RedfishHost(**activity_input["host"]))


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(
        devices=[
            network_device_from_nautobot_graphql(device)
            for device in TEST_BMC_SWITCHES.values()
            if device["location"]["name"] == activity_input.site
            and device["role"]["name"] in activity_input.roles
        ]
    )


@activity.defn(name="get_device_arp_table")
def mock_get_device_arp_table(device_data: NetworkDeviceData) -> DeviceNeighborData:
    if device_data.name == "mock_device1":
        return TEST_ARP_TABLES[0]
    if device_data.name == "mock_device2":
        return TEST_ARP_TABLES[1]
    raise ApplicationError(str(device_data), non_retryable=True)


@activity.defn(name="populate_redfish_macs")
def mock_populate_redfish_macs(
    activity_input: PopulateRedfishMacsInput,
) -> PopulateRedfishMacsOutput:
    if activity_input.hosts[0].address == "127.0.0.3":
        return PopulateRedfishMacsOutput(
            hosts=[
                RedfishHost(
                    address="127.0.0.3",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac="D0:8E:79:F8:92:44",
                ),
                RedfishHost(
                    address="127.0.0.4",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac="38:7C:76:8D:6f:13",
                ),
                RedfishHost(
                    address="127.0.0.5",
                    port=443,
                    vendor=RedfishVendor.BLUEFIELD,
                    mac="C8:4B:D6:7A:39:E2",
                ),
            ]
        )

    return PopulateRedfishMacsOutput(
        hosts=[
            RedfishHost(
                address="127.0.0.1",
                port=443,
                vendor=RedfishVendor.DELL,
                mac="C8:4B:D6:7A:E9:E2",
            ),
            RedfishHost(
                address="127.0.0.2",
                port=443,
                vendor=RedfishVendor.LENOVO,
                mac="38-7C-76-8D-6F-13",
            ),
        ]
    )


@activity.defn(name="get_server_details")
def mock_get_server_details(
    activity_input: GetServerDetailsActivityInput,
) -> GetServerDetailsActivityOutput:
    if (
        activity_input.host.address == "127.0.0.1"
        and activity_input.nic_manufacturers == NIC_MANUFACTURER_MELLANOX
    ):
        return GetServerDetailsActivityOutput(
            server=RedfishServer(
                address="127.0.0.1",
                port=443,
                vendor=RedfishVendor.DELL,
                mac="C8:4B:D6:7A:E9:E2",
                serial="TESTSERIAL1",
                nics=[
                    RedfishNic(
                        slot="NIC.Slot.4",
                        name="NIC.Slot.4-1",
                        mac="58-A2-E1-9A-02-F3",
                    ),
                    RedfishNic(
                        slot="NIC.Slot.5",
                        name="NIC.Slot.5-1",
                        mac="58-A2-E1-84-00-A2",
                    ),
                ],
            )
        )
    if (
        activity_input.host.address == "127.0.0.2"
        and activity_input.nic_manufacturers == NIC_MANUFACTURER_MELLANOX
    ):
        return GetServerDetailsActivityOutput(
            server=RedfishServer(
                address="127.0.0.2",
                port=443,
                vendor=RedfishVendor.LENOVO,
                mac="38-7C-76-8D-6F-13",
                serial="TESTSERIAL2",
                nics=[
                    RedfishNic(
                        slot="slot-1",
                        name="1",
                        mac="58-A2-E1-84-74-D7",
                    )
                ],
            )
        )
    raise ApplicationError(str(activity_input), non_retryable=True)


@activity.defn(name="get_dpu_details")
def mock_get_dpu_details(
    activity_input: GetDpuDetailsActivityInput,
) -> GetDpuDetailsActivityOutput:
    if activity_input.host.address == "127.0.0.3":
        return GetDpuDetailsActivityOutput(
            dpu=RedfishDpu(
                address="127.0.0.3",
                port=443,
                vendor=RedfishVendor.BLUEFIELD,
                mac="D0:8E:79:F8:92:44",
                base_mac="58-A2-E1-9A-02-F3",
                serial="TESTSERIAL3",
                ports=[
                    RedfishDpuPort(name="eth0", mac="58-A2-E1-9A-03-04"),
                    RedfishDpuPort(name="eth1", mac="58-A2-E1-9A-03-05"),
                ],
            )
        )
    if activity_input.host.address == "127.0.0.4":
        return GetDpuDetailsActivityOutput(
            dpu=RedfishDpu(
                address="127.0.0.4",
                port=443,
                vendor=RedfishVendor.BLUEFIELD,
                mac="38:7C:76:8D:6f:13",
                base_mac="58-A2-E1-84-00-A2",
                serial="TESTSERIAL4",
                ports=[
                    RedfishDpuPort(name="eth0", mac="58-A2-E1-84-00-B3"),
                    RedfishDpuPort(name="eth1", mac="58-A2-E1-84-00-B4"),
                ],
            )
        )
    if activity_input.host.address == "127.0.0.5":
        return GetDpuDetailsActivityOutput(
            dpu=RedfishDpu(
                address="127.0.0.5",
                port=443,
                vendor=RedfishVendor.BLUEFIELD,
                mac="C8:4B:D6:7A:39:E2",
                base_mac="58-A2-E1-84-74-D7",
                serial="TESTSERIAL5",
                ports=[
                    RedfishDpuPort(name="eth0", mac="58-A2-E1-84-74-E8"),
                ],
            )
        )
    raise ApplicationError(str(activity_input), non_retryable=True)


@activity.defn(name="get_host_devices")
async def mock_get_host_devices(
    activity_input: GetHostDevicesInput,
) -> GetHostDevicesOutput:
    if activity_input.site == "test_site" and activity_input.mac_addresses == ["C8-4B-D6-7A-E9-E2"]:
        return GetHostDevicesOutput(devices=[host_device_from_nautobot_graphql(TEST_SERVERS[0])])
    if activity_input.site == "test_site" and activity_input.mac_addresses == ["38-7C-76-8D-6F-13"]:
        return GetHostDevicesOutput(devices=[host_device_from_nautobot_graphql(TEST_SERVERS[1])])
    raise ApplicationError(str(activity_input), non_retryable=True)


@activity.defn(name="get_host_device")
async def mock_get_host_device(
    activity_input: GetHostDeviceInput,
) -> GetHostDeviceOutput:
    if activity_input.device_id == "3046d89c-5758-404a-879d-004fbdb96dd9":
        return GetHostDeviceOutput(device=host_device_from_nautobot_graphql(TEST_DPU_DEVICES[0]))
    if activity_input.device_id == "fff10e3c-05c8-4cb7-b4f4-636fa9060fd8":
        return GetHostDeviceOutput(device=host_device_from_nautobot_graphql(TEST_DPU_DEVICES[1]))
    if activity_input.device_id == "3bf3d6a7-df68-4616-97db-372005460fa0":
        return GetHostDeviceOutput(device=host_device_from_nautobot_graphql(TEST_DPU_DEVICES[2]))
    raise ApplicationError(str(activity_input), non_retryable=True)


@activity.defn(name="update_dpu_data")
def mock_update_dpu_data(
    activity_input: UpdateDpuDataActivityInput,
) -> UpdateDpuDataActivityOutput:
    if activity_input.server.address == "127.0.0.1":
        dpu1 = host_device_from_nautobot_graphql(TEST_DPU_DEVICES[0])
        dpu1.serial = "TESTSERIAL3"
        dpu1.interfaces[1].mac_address = "58-A2-E1-84-74-EE"
        dpu1.interfaces[2].mac_address = "58-A2-E1-84-74-EF"
        dpu2 = host_device_from_nautobot_graphql(TEST_DPU_DEVICES[1])
        dpu2.serial = "TESTSERIAL4"
        dpu2.interfaces[1].mac_address = "58-A2-E1-84-00-B3"
        dpu2.interfaces[2].mac_address = "58-A2-E1-84-00-B4"
        return UpdateDpuDataActivityOutput(
            device_data=[dpu1, dpu2],
        )
    if activity_input.server.address == "127.0.0.2":
        dpu = host_device_from_nautobot_graphql(TEST_DPU_DEVICES[2])
        dpu.serial = "TESTSERIAL5"
        dpu.interfaces[1].mac_address = "58-A2-E1-9A-03-04"
        return UpdateDpuDataActivityOutput(
            device_data=[dpu],
        )
    raise ApplicationError(str(activity_input), non_retryable=True)


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_redfish_provisioning_workflow(mock_time, mock_sleep, env):
    task_queue_name = str(uuid.uuid4())
    mock_power_on_host = activity.defn(
        MagicMock(wraps=power_on_host),
        name="power_on_host",
    )
    mock_set_redfish_password = activity.defn(
        MagicMock(wraps=set_redfish_password),
        name="set_redfish_password",
    )
    mock_factory_reset_bmc = activity.defn(
        MagicMock(wraps=factory_reset_bmc),
        name="factory_reset_bmc",
    )

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[RedfishProvisioningWorkflow],
        activities=[
            mock_power_on_host,
            mock_set_redfish_password,
            mock_discover_redfish_hosts,
            mock_factory_reset_bmc,
            mock_get_network_devices,
            mock_get_device_arp_table,
            mock_get_dpu_details,
            mock_get_host_device,
            mock_get_host_devices,
            mock_get_server_details,
            mock_update_dpu_data,
            mock_populate_redfish_macs,
        ],
        activity_executor=ThreadPoolExecutor(2),
    ):
        workflow_input = RedfishProvisioningInput(
            ip_range_start="127.0.0.1",
            ip_range_end="127.0.0.5",
            port=443,
            bmc_switch_roles=["smn-leaf"],
            site="SITEA",
        )
        workflow_id = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            RedfishProvisioningWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=5),
            search_attributes={"User": ["test"]},
        )

        while await handle.query("pending_approval") is False:
            await asyncio.sleep(1)
        await handle.signal("approve", {"stage_name": "approve_factory_reset", "user": "Test"})

        result = await handle.result()
        await handle.query("stages")
        assert result == RedfishProvisioningResult(
            redfish_servers=[
                RedfishServer(
                    address="127.0.0.1",
                    port=443,
                    vendor=RedfishVendor.DELL,
                    mac="C8-4B-D6-7A-E9-E2",
                    serial="TESTSERIAL1",
                    nics=[
                        RedfishNic(
                            name="NIC.Slot.4-1",
                            slot="NIC.Slot.4",
                            mac="58-A2-E1-9A-02-F3",
                            dpu=RedfishDpu(
                                address="127.0.0.3",
                                port=443,
                                vendor=RedfishVendor.BLUEFIELD,
                                mac="D0:8E:79:F8:92:44",
                                ports=[
                                    RedfishDpuPort(name="eth0", mac="58-A2-E1-9A-03-04"),
                                    RedfishDpuPort(name="eth1", mac="58-A2-E1-9A-03-05"),
                                ],
                                base_mac="58-A2-E1-9A-02-F3",
                                serial="TESTSERIAL3",
                            ),
                        ),
                        RedfishNic(
                            name="NIC.Slot.5-1",
                            slot="NIC.Slot.5",
                            mac="58-A2-E1-84-00-A2",
                            dpu=RedfishDpu(
                                address="127.0.0.4",
                                port=443,
                                vendor=RedfishVendor.BLUEFIELD,
                                mac="38:7C:76:8D:6f:13",
                                ports=[
                                    RedfishDpuPort(name="eth0", mac="58-A2-E1-84-00-B3"),
                                    RedfishDpuPort(name="eth1", mac="58-A2-E1-84-00-B4"),
                                ],
                                base_mac="58-A2-E1-84-00-A2",
                                serial="TESTSERIAL4",
                            ),
                        ),
                    ],
                ),
                RedfishServer(
                    address="127.0.0.2",
                    port=443,
                    vendor=RedfishVendor.LENOVO,
                    mac="38-7C-76-8D-6F-13",
                    serial="TESTSERIAL2",
                    nics=[
                        RedfishNic(
                            name="1",
                            slot="slot-1",
                            mac="58-A2-E1-84-74-D7",
                            dpu=RedfishDpu(
                                address="127.0.0.5",
                                port=443,
                                vendor=RedfishVendor.BLUEFIELD,
                                mac="C8:4B:D6:7A:39:E2",
                                ports=[RedfishDpuPort(name="eth0", mac="58-A2-E1-84-74-E8")],
                                base_mac="58-A2-E1-84-74-D7",
                                serial="TESTSERIAL5",
                            ),
                        )
                    ],
                ),
            ],
            updated_devices=[
                HostDeviceData(
                    id="3046d89c-5758-404a-879d-004fbdb96dd9",
                    name="rno1-m04-c10-server1-dpu1.lab1",
                    role="gpu",
                    site="RNO1-NVIDIA Config Manager-LAB",
                    device_type="bluefield-3140",
                    serial="TESTSERIAL3",
                    device_bays=[],
                    interfaces=[
                        InterfaceData(
                            name="DPU BMC",
                            id="1ac00501-7ada-4edb-94fc-ec39fe0fb0ed",
                            host="rno1-m04-c10-server1-dpu1.lab1",
                            mac_address=None,
                            vrf_id=None,
                        ),
                        InterfaceData(
                            name="DPU Port 1",
                            id="d29c23c5-ee99-4b1b-a3b7-242482817213",
                            host="rno1-m04-c10-server1-dpu1.lab1",
                            mac_address="58-A2-E1-84-74-EE",
                            vrf_id=None,
                        ),
                        InterfaceData(
                            name="DPU Port 2",
                            id="336a0f83-d05e-46d3-92af-7b806733153f",
                            host="rno1-m04-c10-server1-dpu1.lab1",
                            mac_address="58-A2-E1-84-74-EF",
                            vrf_id=None,
                        ),
                    ],
                ),
                HostDeviceData(
                    id="fff10e3c-05c8-4cb7-b4f4-636fa9060fd8",
                    name="rno1-m04-c10-server1-dpu2.lab1",
                    role="gpu",
                    site="RNO1-NVIDIA Config Manager-LAB",
                    device_type="bluefield-3140",
                    serial="TESTSERIAL4",
                    device_bays=[],
                    interfaces=[
                        InterfaceData(
                            name="DPU BMC",
                            id="7c3a1063-50a1-45c5-aa47-b04afe18e498",
                            host="rno1-m04-c10-server1-dpu2.lab1",
                            mac_address=None,
                            vrf_id=None,
                        ),
                        InterfaceData(
                            name="DPU Port 1",
                            id="ee1e0539-cec0-473e-b490-a792055a219d",
                            host="rno1-m04-c10-server1-dpu2.lab1",
                            mac_address="58-A2-E1-84-00-B3",
                            vrf_id=None,
                        ),
                        InterfaceData(
                            name="DPU Port 2",
                            id="88136029-2d9d-49a8-b820-3d6b884d544e",
                            host="rno1-m04-c10-server1-dpu2.lab1",
                            mac_address="58-A2-E1-84-00-B4",
                            vrf_id=None,
                        ),
                    ],
                ),
                HostDeviceData(
                    id="3bf3d6a7-df68-4616-97db-372005460fa0",
                    name="rno1-m04-c10-server4-dpu1.lab1",
                    role="dpu",
                    site="RNO1-NVIDIA Config Manager-LAB",
                    device_type="bluefield-3140",
                    serial="TESTSERIAL5",
                    device_bays=[],
                    interfaces=[
                        InterfaceData(
                            name="DPU BMC",
                            id="be8e95da-ce03-47fa-9dcf-2fbbf340f08a",
                            host="rno1-m04-c10-server4-dpu1.lab1",
                            mac_address="58-A2-E1-84-74-FB",
                            vrf_id=None,
                        ),
                        InterfaceData(
                            name="DPU Port 1",
                            id="36364607-21f5-45d2-9908-d08fee457aab",
                            host="rno1-m04-c10-server4-dpu1.lab1",
                            mac_address="58-A2-E1-9A-03-04",
                            vrf_id=None,
                        ),
                    ],
                ),
            ],
        )

        assert mock_power_on_host.call_count == 5
        assert mock_set_redfish_password.call_count == 4
        for args in mock_set_redfish_password.call_args_list:
            assert not args[0][0]["host"]["vendor"] == RedfishVendor.DELL
        assert mock_factory_reset_bmc.call_count == 4
        for args in mock_factory_reset_bmc.call_args_list:
            assert not args[0][0]["host"]["vendor"] == RedfishVendor.DELL
