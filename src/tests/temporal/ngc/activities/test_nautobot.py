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
import pytest
from aioresponses import aioresponses
from nv_config_manager_dcim import DeviceInventoryFilter
from nv_config_manager_dcim_nautobot_2x.workflow import DeviceVrfInfo, NautobotClient
from temporalio.exceptions import ApplicationError

from nv_config_manager.dcim import DCIMError
from nv_config_manager.temporal.common.mixins.device import (
    DeviceBayData,
    HostDeviceData,
    InterfaceData,
    NetworkDeviceData,
)
from nv_config_manager.temporal.ngc.activities.nautobot import (
    AssignVrfToDeviceInput,
    AssignVrfToInterfaceInput,
    CheckRecordedConfigDriftInput,
    GetDeviceInterfacesInput,
    GetDeviceVrfsInput,
    GetHostDeviceInput,
    GetHostDevicesInput,
    GetNetworkDeviceInput,
    GetNetworkDevicesInput,
    assign_vrf_to_device,
    assign_vrf_to_interface,
    check_recorded_config_drift,
    get_device_interfaces,
    get_device_vrfs,
    get_host_device,
    get_host_devices,
    get_network_device,
    get_network_devices,
)
from tests.temporal.ngc.activities.test_nautobot_data import (
    DEVICE_INTERFACES_RESPONSE,
    DEVICE_VRFS_EMPTY_RESPONSE,
    DEVICE_VRFS_RESPONSE,
    HOST_DEVICE_V2,
    HOST_DEVICES_V2,
    INTERFACE_PATCH_RESPONSE,
    NETWORK_DEVICE_V2,
    NETWORK_DEVICES_V2,
)


@pytest.mark.asyncio
async def test_get_network_device_v2():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=NETWORK_DEVICE_V2,
        )

        output = await get_network_device(
            GetNetworkDeviceInput(device_id="7cd2a91d-f6af-480b-9162-381cdfb4a66c")
        )
    assert output.device == NetworkDeviceData(
        id="7cd2a91d-f6af-480b-9162-381cdfb4a66c",
        name="MOCK-LEAF-01",
        role="tenant-a-device",
        site="SITEA",
        device_type="msn4600",
        platform="cumulus-linux",
        primary_ip4="10.91.33.86",
        primary_ip6=None,
        render_enabled=True,
        deploy_enabled=True,
        backup_enabled=True,
        ztp_enabled=True,
    )

    assert output.device.host == "10.91.33.86"
    assert output.device.backup_path == "7cd2a91d-f6af-480b-9162-381cdfb4a66c/startup.yaml"
    assert output.device.intended_config_path == "7cd2a91d-f6af-480b-9162-381cdfb4a66c/startup.yaml"


@pytest.mark.asyncio
async def test_get_network_devices_v2():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=NETWORK_DEVICES_V2,
        )

        output = await get_network_devices(GetNetworkDevicesInput(site="SITEA"))
    assert output.devices[0] == NetworkDeviceData(
        id="7cd2a91d-f6af-480b-9162-381cdfb4a66c",
        name="MOCK-LEAF-01",
        role="tenant-a-device",
        site="SITEA",
        device_type="msn4600",
        platform="cumulus-linux",
        primary_ip4="10.91.33.86",
        primary_ip6=None,
        render_enabled=True,
        deploy_enabled=True,
        backup_enabled=True,
        ztp_enabled=True,
    )

    assert output.devices[0].host == "10.91.33.86"
    assert output.devices[0].backup_path == "7cd2a91d-f6af-480b-9162-381cdfb4a66c/startup.yaml"
    assert (
        output.devices[0].intended_config_path
        == "7cd2a91d-f6af-480b-9162-381cdfb4a66c/startup.yaml"
    )


@pytest.mark.asyncio
async def test_get_host_device_v2():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=HOST_DEVICE_V2,
        )

        output = await get_host_device(
            GetHostDeviceInput(device_id="7cd2a91d-f6af-480b-9162-381cdfb4a66c")
        )
    assert output.device == HostDeviceData(
        id="192efd58-927b-4a56-8653-1864a40ffed9",
        name="MOCK-SERVER-01",
        role="tenant-a-device",
        site="SITEA",
        serial="MOCKSERIAL1",
        device_type="thinksystem-sr670-v2",
        device_bays=[
            DeviceBayData(
                name="0",
                id="d07b2a83-cb7d-4615-bc35-c8099a6b2dd1",
                installed_device_id="39a1af57-80a3-435c-8904-318164bde1f4",
            )
        ],
        interfaces=[
            InterfaceData(
                name="Server BMC",
                id="b0c0cb7d-0213-4849-9ec4-265cc3cde4ca",
                host="MOCK-SERVER-01",
                mac_address="08-8F-C3-A6-E6-8F",
                vrf_id=None,
            ),
            InterfaceData(
                name="lo",
                id="24d5c6f9-e2fe-442e-b412-f7092eed4798",
                host="MOCK-SERVER-01",
                mac_address=None,
                vrf_id=None,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_get_host_devices_v2():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=HOST_DEVICES_V2,
        )

        output = await get_host_devices(GetHostDevicesInput(site="SITEA"))
    assert output.devices[0] == HostDeviceData(
        id="192efd58-927b-4a56-8653-1864a40ffed9",
        name="MOCK-SERVER-01",
        role="tenant-a-device",
        site="SITEA",
        serial="MOCKSERIAL1",
        device_type="thinksystem-sr670-v2",
        device_bays=[
            DeviceBayData(
                name="0",
                id="d07b2a83-cb7d-4615-bc35-c8099a6b2dd1",
                installed_device_id="39a1af57-80a3-435c-8904-318164bde1f4",
            )
        ],
        interfaces=[
            InterfaceData(
                name="Server BMC",
                id="b0c0cb7d-0213-4849-9ec4-265cc3cde4ca",
                host="MOCK-SERVER-01",
                mac_address="08-8F-C3-A6-E6-8F",
                vrf_id=None,
            ),
            InterfaceData(
                name="lo",
                id="24d5c6f9-e2fe-442e-b412-f7092eed4798",
                host="MOCK-SERVER-01",
                mac_address=None,
                vrf_id=None,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_check_recorded_config_drift_no_drift():
    """Test check_recorded_config_drift when there is no drift."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "config_manager_device": {
                        "intended_config": {"commit_id": 12345},
                        "backup_config": {"deployed_commit_id": 12345},
                    }
                }
            },
        )

        result = await check_recorded_config_drift(
            CheckRecordedConfigDriftInput(device_id="test-device")
        )

        assert result is False


@pytest.mark.asyncio
async def test_check_recorded_config_drift_with_drift():
    """Test check_recorded_config_drift when there is drift."""
    # First check mocks scenario where there's a pending config that hasn't been deployed yet
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "config_manager_device": {
                        "intended_config": {"commit_id": 12345},
                        "backup_config": {"deployed_commit_id": 67890},
                    }
                }
            },
        )

        result = await check_recorded_config_drift(
            CheckRecordedConfigDriftInput(device_id="test-device")
        )

        assert result is True

    # Second check mocks scenario where the configuration has been hand edited by a user and therefore
    # there is no commit ID to associate with the backup.
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "config_manager_device": {
                        "intended_config": {"commit_id": 12345},
                        "backup_config": {"deployed_commit_id": None},
                    }
                }
            },
        )

        result = await check_recorded_config_drift(
            CheckRecordedConfigDriftInput(device_id="test-device")
        )

        assert result is True


@pytest.mark.asyncio
async def test_check_recorded_config_drift_partial_configs():
    """Test check_recorded_config_drift when only one config is present."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "config_manager_device": {
                        "intended_config": {"commit_id": 12345},
                        "backup_config": None,
                    }
                }
            },
        )

        result = await check_recorded_config_drift(
            CheckRecordedConfigDriftInput(device_id="test-device")
        )

        assert result is True


@pytest.mark.asyncio
async def test_get_device_interfaces():
    """Test getting interfaces for a device."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=DEVICE_INTERFACES_RESPONSE,
        )

        output = await get_device_interfaces(GetDeviceInterfacesInput(device_id="device-1"))

    assert len(output.interfaces) == 2
    assert output.interfaces[0] == InterfaceData(
        id="interface-1",
        name="swp1",
        host="test-device",
        mac_address="00-00-00-00-00-01",
        vrf_id=None,
    )
    assert output.interfaces[1] == InterfaceData(
        id="interface-2",
        name="swp2",
        host="test-device",
        mac_address="00-00-00-00-00-02",
        vrf_id="vrf-1",
    )


@pytest.mark.asyncio
async def test_get_device_interfaces_with_filter():
    """Test getting specific interfaces by name."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=DEVICE_INTERFACES_RESPONSE,
        )

        output = await get_device_interfaces(
            GetDeviceInterfacesInput(device_id="device-1", interface_names=["swp1"])
        )

    assert len(output.interfaces) == 1
    assert output.interfaces[0].name == "swp1"


@pytest.mark.asyncio
async def test_get_device_interfaces_missing_interface():
    """Test error when requested interface doesn't exist."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=DEVICE_INTERFACES_RESPONSE,
        )

        with pytest.raises(ApplicationError) as exc_info:
            await get_device_interfaces(
                GetDeviceInterfacesInput(device_id="device-1", interface_names=["swp1", "swp99"])
            )

    assert "swp99" in str(exc_info.value)
    assert "Interfaces not found on device" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_device_vrfs():
    """Test getting VRFs assigned to a device."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=DEVICE_VRFS_RESPONSE,
        )

        output = await get_device_vrfs(GetDeviceVrfsInput(device_id="device-1"))

    assert len(output.vrfs) == 2
    assert output.vrfs[0] == DeviceVrfInfo(vrf_id="vrf-1", vrf_name="vrf-tenant-1")
    assert output.vrfs[1] == DeviceVrfInfo(vrf_id="vrf-2", vrf_name="vrf-tenant-2")


@pytest.mark.asyncio
async def test_get_device_vrfs_empty():
    """Test getting VRFs when device has none assigned."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=DEVICE_VRFS_EMPTY_RESPONSE,
        )

        output = await get_device_vrfs(GetDeviceVrfsInput(device_id="device-1"))

    assert len(output.vrfs) == 0


@pytest.mark.asyncio
async def test_assign_vrf_to_device_new():
    """Test assigning a new VRF to a device."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/ipam/vrf-device-assignments/",
            payload={"device": "device-1", "vrf": "vrf-2"},
        )

        await assign_vrf_to_device(AssignVrfToDeviceInput(device_id="device-1", vrf_id="vrf-2"))


@pytest.mark.asyncio
async def test_assign_vrf_to_interface():
    """Test assigning VRF to an interface."""
    with aioresponses() as m:
        m.patch(
            "https://nautobot.example.com/api/dcim/interfaces/interface-1/",
            payload=INTERFACE_PATCH_RESPONSE,
        )

        await assign_vrf_to_interface(
            AssignVrfToInterfaceInput(interface_id="interface-1", vrf_id="vrf-1")
        )


@pytest.mark.asyncio
@pytest.mark.timeout(120)  # 7 network calls; each DNS/connection failure can take 10-15s
async def test_nautobot_client_context_manager_errors():
    """Test that NautobotClient creates sessions lazily when not used as context manager."""
    from aiohttp.client_exceptions import ClientConnectorDNSError
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    client = NautobotClient()

    # Base class creates sessions lazily, so it will try to connect and fail with DNS error
    with pytest.raises(ClientConnectorDNSError):
        await client.graphql_query("query { }")

    with pytest.raises(ClientConnectorDNSError):
        await client.installed_plugins()

    with pytest.raises(ClientConnectorDNSError):
        await client.post("path/", {})

    with pytest.raises(ClientConnectorDNSError):
        await client.delete("path/")

    with pytest.raises(ClientConnectorDNSError):
        await client.get("path/", {})

    with pytest.raises(ClientConnectorDNSError):
        await client.patch("path/", {})

    with pytest.raises(ClientConnectorDNSError):
        await client.load_config_manager_plugin_backup_config("device-id")

    with pytest.raises(ClientConnectorDNSError):
        await client.update_config_manager_plugin_backup_config(
            "device-id", "instance", "commit", "path", "user", "message", "workflow"
        )


@pytest.mark.asyncio
async def test_graphql_query_error():
    """Test that graphql_query handles 400 errors correctly."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            status=400,
            payload={"errors": ["GraphQL syntax error"]},
        )

        async with NautobotClient() as client:
            with pytest.raises(DCIMError, match="GraphQL error") as exc_info:
                await client.graphql_query("bad query")
            assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_get_device_vrfs_not_found():
    """Test get_device_vrfs when device doesn't exist."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"device": None}},
        )

        with pytest.raises(ApplicationError, match="not found"):
            await get_device_vrfs(GetDeviceVrfsInput(device_id="nonexistent"))


@pytest.mark.asyncio
async def test_get_interfaces_by_mac():
    """Test getting interfaces by MAC addresses."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "interfaces": [
                        {
                            "id": "interface-1",
                            "name": "eth0",
                            "mac_address": "00:00:00:00:00:01",
                            "device": {"name": "test-device"},
                            "module": None,
                        },
                        {
                            "id": "interface-2",
                            "name": "eth1",
                            "mac_address": "00:00:00:00:00:02",
                            "device": {"name": "test-device"},
                            "module": None,
                        },
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            interfaces = await client.get_interfaces_by_mac(
                ["00:00:00:00:00:01", "00:00:00:00:00:02"]
            )

        assert len(interfaces) == 2
        assert interfaces[0].mac_address == "00-00-00-00-00-01"


@pytest.mark.asyncio
async def test_load_config_manager_plugin_backup_config():
    """Test loading NVIDIA Config Manager plugin backup config."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.get(
            "https://nautobot.example.com/api/plugins/nv-config-manager/backupconfig/device-1/",
            payload={"commit_id": 12345, "path": "device-1/startup.yaml"},
        )

        async with NautobotClient() as client:
            config = await client.load_config_manager_plugin_backup_config("device-1")

        assert config["commit_id"] == 12345
        request_call = next(iter(m.requests.values()))[0]
        assert request_call.kwargs["headers"] == {"Authorization": "Token DUMMY"}


@pytest.mark.asyncio
async def test_load_config_manager_plugin_backup_config_not_found():
    """Test loading NVIDIA Config Manager plugin backup config when not found."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.get(
            "https://nautobot.example.com/api/plugins/nv-config-manager/backupconfig/device-1/",
            status=404,
        )

        async with NautobotClient() as client:
            config = await client.load_config_manager_plugin_backup_config("device-1")

        assert config == {}


@pytest.mark.asyncio
async def test_update_config_manager_plugin_backup_config():
    """Test updating NVIDIA Config Manager plugin backup config."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/plugins/nv-config-manager/backupconfig/",
            payload={"status": "ok"},
        )

        async with NautobotClient() as client:
            await client.update_config_manager_plugin_backup_config(
                device_id="device-1",
                config_store_instance="gitlab",
                commit_id=12345,
                path="device-1/startup.yaml",
                user="test-user",
                commit_message="Updated config",
                workflow_id="workflow-1",
            )

        request_call = next(iter(m.requests.values()))[0]
        assert request_call.kwargs["headers"] == {"Authorization": "Token DUMMY"}


def test_get_device_ui_url():
    """Test getting device UI URL."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    client = NautobotClient()
    url = client.get_device_ui_url("device-123")
    assert "/dcim/devices/device-123" in url


@pytest.mark.asyncio
async def test_device_filter_variations():
    """Test various device filter parameter combinations."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    # Test status filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", status="active")

    # Test role filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", role="leaf")

    # Test tenant filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", tenant="tenant-1")

    # Test device_type_id filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", device_type_id="type-1")

    # Test mac_address filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", mac_address="00:00:00:00:00:01")

    # Test device_ids filter
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", device_ids="device-1")

    # Test platform filter (single)
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            from nv_config_manager.temporal.common.mixins.device import Platform

            await client.get_devices(fields="id", platform=Platform.CUMULUS_LINUX)


@pytest.mark.asyncio
async def test_device_filter_managed_only_variable():
    """Test managed_only is sent as the Nautobot managed-device GraphQL filter."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": []}},
        )

        async with NautobotClient() as client:
            await client.get_devices(fields="id", site="test-site", managed_only=True)

        request_call = next(iter(m.requests.values()))[0]
        assert request_call.kwargs["json"]["variables"] == {
            "site": ["test-site"],
            "managed_only": True,
        }
        assert (
            "nv_config_manager_device_status: $managed_only" in request_call.kwargs["json"]["query"]
        )


@pytest.mark.asyncio
async def test_no_filter_exception():
    """Test that get_devices raises exception when no filters provided."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient, NautobotException

    async with NautobotClient() as client:
        with pytest.raises(NautobotException, match="Must apply at least one filter"):
            await client.get_devices(fields="id")


@pytest.mark.asyncio
async def test_duplicate_devices_exception():
    """Test that get_devices raises exception on duplicate device names."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient, NautobotException

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "devices": [
                        {"id": "1", "name": "device-1"},
                        {"id": "2", "name": "device-1"},
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            with pytest.raises(NautobotException, match="Duplicate device names"):
                await client.get_devices(fields="id name", site="test-site")


@pytest.mark.asyncio
async def test_device_status_filters():
    """Test filtering devices by status flags using NautobotClient directly."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "devices": [
                        {
                            "id": "1",
                            "name": "device-1",
                            "configmanagerdevicestatus": {
                                "render_enabled": True,
                                "deploy_enabled": True,
                                "backup_enabled": True,
                                "ztp_enabled": False,
                            },
                            "rack": {"name": "rack1"},
                            "position": 1,
                            "role": {"name": "leaf"},
                            "platform": {"name": "cumulus-linux"},
                            "device_type": {"model": "msn4600"},
                            "location": {
                                "name": "site1",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                            "primary_ip4": {"host": "10.0.0.1"},
                            "primary_ip6": None,
                            "config_context": {},
                        },
                        {
                            "id": "2",
                            "name": "device-2",
                            "configmanagerdevicestatus": {
                                "render_enabled": False,
                                "deploy_enabled": True,
                                "backup_enabled": True,
                                "ztp_enabled": True,
                            },
                            "rack": {"name": "rack1"},
                            "position": 2,
                            "role": {"name": "leaf"},
                            "platform": {"name": "cumulus-linux"},
                            "device_type": {"model": "msn4600"},
                            "location": {
                                "name": "site1",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                            "primary_ip4": {"host": "10.0.0.2"},
                            "primary_ip6": None,
                            "config_context": {},
                        },
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            devices = await client.get_network_devices(
                DeviceInventoryFilter(site="test-site", render_enabled=True)
            )

        assert len(devices) == 1
        assert devices[0].name == "device-1"


@pytest.mark.asyncio
async def test_device_status_filters_deploy():
    """Test filtering devices by deploy_enabled."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "devices": [
                        {
                            "id": "1",
                            "name": "device-1",
                            "configmanagerdevicestatus": {
                                "render_enabled": True,
                                "deploy_enabled": True,
                                "backup_enabled": True,
                                "ztp_enabled": False,
                            },
                            "rack": {"name": "rack1"},
                            "position": 1,
                            "role": {"name": "leaf"},
                            "platform": {"name": "cumulus-linux"},
                            "device_type": {"model": "msn4600"},
                            "location": {
                                "name": "site1",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                            "primary_ip4": {"host": "10.0.0.1"},
                            "primary_ip6": None,
                            "config_context": {},
                        }
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            devices = await client.get_network_devices(
                DeviceInventoryFilter(site="test-site", deploy_enabled=True)
            )

        assert len(devices) == 1


@pytest.mark.asyncio
async def test_device_status_filters_backup():
    """Test filtering devices by backup_enabled."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "devices": [
                        {
                            "id": "1",
                            "name": "device-1",
                            "configmanagerdevicestatus": {
                                "render_enabled": True,
                                "deploy_enabled": True,
                                "backup_enabled": True,
                                "ztp_enabled": False,
                            },
                            "rack": {"name": "rack1"},
                            "position": 1,
                            "role": {"name": "leaf"},
                            "platform": {"name": "cumulus-linux"},
                            "device_type": {"model": "msn4600"},
                            "location": {
                                "name": "site1",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                            "primary_ip4": {"host": "10.0.0.1"},
                            "primary_ip6": None,
                            "config_context": {},
                        }
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            devices = await client.get_network_devices(
                DeviceInventoryFilter(site="test-site", backup_enabled=True)
            )

        assert len(devices) == 1


@pytest.mark.asyncio
async def test_device_status_filters_ztp():
    """Test filtering devices by ztp_enabled."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={
                "data": {
                    "devices": [
                        {
                            "id": "1",
                            "name": "device-1",
                            "configmanagerdevicestatus": {
                                "render_enabled": True,
                                "deploy_enabled": True,
                                "backup_enabled": True,
                                "ztp_enabled": True,
                            },
                            "rack": {"name": "rack1"},
                            "position": 1,
                            "role": {"name": "leaf"},
                            "platform": {"name": "cumulus-linux"},
                            "device_type": {"model": "msn4600"},
                            "location": {
                                "name": "site1",
                                "location_type": {"name": "Site"},
                                "parent": None,
                            },
                            "primary_ip4": {"host": "10.0.0.1"},
                            "primary_ip6": None,
                            "config_context": {},
                        }
                    ]
                }
            },
        )

        async with NautobotClient() as client:
            devices = await client.get_network_devices(
                DeviceInventoryFilter(site="test-site", ztp_enabled=True)
            )

        assert len(devices) == 1


@pytest.mark.asyncio
async def test_update_host_device():
    """Test updating a host device."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.patch(
            "https://nautobot.example.com/api/dcim/devices/device-1/",
            payload={
                "id": "device-1",
                "name": "updated-device",
                "role": {"name": "compute"},
                "rack": {"name": "rack1"},
                "position": 1,
                "location": {
                    "name": "site1",
                    "location_type": {"name": "Site"},
                    "parent": None,
                },
                "device_type": {"model": "server"},
                "device_bays": [],
                "interfaces": [],
            },
        )

        async with NautobotClient() as client:
            device = await client.update_host_device("device-1", {"name": "updated-device"})

        assert device.name == "updated-device"


@pytest.mark.asyncio
async def test_create_vrf():
    """Test creating a VRF."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/ipam/vrfs/",
            payload={"id": "vrf-1", "name": "new-vrf"},
        )

        async with NautobotClient() as client:
            result = await client.create_vrf({"name": "new-vrf"})

        assert result["name"] == "new-vrf"


@pytest.mark.asyncio
async def test_delete_vrf():
    """Test deleting a VRF."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.delete("https://nautobot.example.com/api/ipam/vrfs/vrf-1/", status=204)

        async with NautobotClient() as client:
            await client.delete_vrf("vrf-1")


@pytest.mark.asyncio
async def test_merge_config_context():
    """Test merging config context data."""

    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        # Mock GET with query parameters
        m.get(
            "https://nautobot.example.com/api/dcim/devices/device-1/?depth=1",
            payload={
                "id": "device-1",
                "name": "device",
                "local_config_context_data": {"existing": "value"},
            },
        )
        # Mock PATCH
        m.patch(
            "https://nautobot.example.com/api/dcim/devices/device-1/",
            payload={"id": "device-1", "name": "device"},
        )

        async with NautobotClient() as client:
            await client.merge_config_context("device-1", {"new": "data"})


@pytest.mark.asyncio
async def test_installed_plugins():
    """Test getting installed plugins."""
    from nv_config_manager_dcim_nautobot_2x.workflow import NautobotClient

    with aioresponses() as m:
        m.get(
            "https://nautobot.example.com/api/status/",
            payload={"plugins": {"plugin1": "1.0.0", "plugin2": "2.0.0"}},
        )

        async with NautobotClient() as client:
            plugins = await client.installed_plugins()

        assert plugins == {"plugin1": "1.0.0", "plugin2": "2.0.0"}
