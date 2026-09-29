# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused Redfish provisioning workflow test data."""

from typing import Any

from nv_config_manager_workflows.clients.device.models import DeviceArpTable

TEST_ARP_TABLES = [
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.1": ["C8:4B:D6:7A:E9:E2"],
            "127.0.0.2": ["38:7C:76:8D:6F:13"],
            "127.0.0.3": ["D0:8E:79:F8:92:44"],
            "127.0.0.4": ["38:7C:76:8D:6f:13"],
        }
    ),
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.5": ["C8:4B:D6:7A:39:E2"],
            "127.0.0.6": ["C8:4B:D6:7A:28:F2"],
            "127.0.0.7": ["D0:8E:79:F8:12:44"],
            "127.0.0.8": ["38:7C:76:8D:8f:13"],
        }
    ),
    DeviceArpTable(
        ip_to_mac={
            "127.0.0.11": ["C8:4B:26:7B:39:C2"],
        }
    ),
]


TEST_BMC_SWITCHES: dict[str, dict[str, Any]] = {
    "mock_device1": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "mock_device1",
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "smn-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "msn4600-cs2fc"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
    "mock_device2": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
        "name": "mock_device2",
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "smn-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "msn4600-cs2fc"},
        "primary_ip4": {"host": "10.0.0.2"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
    "mock_device3": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
        "name": "mock_device3",
        "platform": {"name": "Arista EOS"},
        "role": {"name": "tan-leaf"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "dcs-7368x-128-bnd-r"},
        "primary_ip4": {"host": "10.0.0.3"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
    },
}

TEST_SERVERS = [
    {
        "id": "86d41e26-2520-58b5-a3f0-2d5e507e8b22",
        "name": "rno1-m04-c10-server1.lab1",
        "role": {"name": "tenant-a-device"},
        "tenant": {"name": "invalid-tenant"},
        "device_type": {"model": "poweredge-r750"},
        "platform": {"name": "Linux"},
        "location": {"location_type": {"name": "Site"}, "name": "test_site"},
        "primary_ip4": {"host": "10.180.166.60"},
        "primary_ip6": None,
        "device_bays": [
            {
                "id": "9e81c424-fd20-494d-a9cd-68fe713a33c9",
                "name": "1",
                "installed_device": {"id": "3046d89c-5758-404a-879d-004fbdb96dd9"},
            },
            {
                "id": "39a92e0a-a6be-41d0-8ebc-8fe1b40356b2",
                "name": "2",
                "installed_device": {"id": "fff10e3c-05c8-4cb7-b4f4-636fa9060fd8"},
            },
        ],
        "interfaces": [
            {
                "id": "aa9d8e49-16d4-43a1-beba-069ae9e8c55f",
                "name": "bmc",
                "mac_address": "C8:4B:D6:7A:E9:E2",
                "ip_addresses": [{"host": "10.180.166.60"}],
                "device": {"name": "rno1-m04-c10-server1.lab1"},
            }
        ],
    },
    {
        "id": "a5e9d91d-f089-4e76-8e94-5566e3963b03",
        "name": "rno1-m04-c10-server4.lab1",
        "role": {"name": "server"},
        "platform": None,
        "device_type": {"model": "thinksystem-sr655"},
        "primary_ip4": None,
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [
            {
                "id": "9e3401cc-71cf-424f-9032-234384870340",
                "name": "1",
                "installed_device": {"id": "3bf3d6a7-df68-4616-97db-372005460fa0"},
            }
        ],
        "interfaces": [
            {
                "id": "eea2ebd5-201a-4738-9294-f4b60ed7cc8d",
                "name": "bmc",
                "mac_address": "38:7C:76:8D:6F:13",
                "ip_addresses": [],
            }
        ],
    },
]


TEST_DPU_DEVICES: Any = [
    {
        "id": "3046d89c-5758-404a-879d-004fbdb96dd9",
        "name": "rno1-m04-c10-server1-dpu1.lab1",
        "role": {"name": "gpu"},
        "platform": {"name": "Linux"},
        "device_type": {"model": "bluefield-3140"},
        "primary_ip4": {"host": "10.180.166.41"},
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "1ac00501-7ada-4edb-94fc-ec39fe0fb0ed",
                "name": "DPU BMC",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
            {
                "id": "d29c23c5-ee99-4b1b-a3b7-242482817213",
                "name": "DPU Port 1",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
            {
                "id": "336a0f83-d05e-46d3-92af-7b806733153f",
                "name": "DPU Port 2",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu1.lab1"},
            },
        ],
    },
    {
        "id": "fff10e3c-05c8-4cb7-b4f4-636fa9060fd8",
        "name": "rno1-m04-c10-server1-dpu2.lab1",
        "role": {"name": "gpu"},
        "platform": {"name": "Linux"},
        "device_type": {"model": "bluefield-3140"},
        "primary_ip4": {"host": "10.180.166.41"},
        "primary_ip6": None,
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "7c3a1063-50a1-45c5-aa47-b04afe18e498",
                "name": "DPU BMC",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
            {
                "id": "ee1e0539-cec0-473e-b490-a792055a219d",
                "name": "DPU Port 1",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
            {
                "id": "88136029-2d9d-49a8-b820-3d6b884d544e",
                "name": "DPU Port 2",
                "mac_address": None,
                "ip_addresses": [],
                "device": {"name": "rno1-m04-c10-server1-dpu2.lab1"},
            },
        ],
    },
    {
        "id": "3bf3d6a7-df68-4616-97db-372005460fa0",
        "name": "rno1-m04-c10-server4-dpu1.lab1",
        "role": {"name": "dpu"},
        "device_type": {"model": "bluefield-3140"},
        "location": {"location_type": {"name": "Site"}, "name": "RNO1-NVIDIA Config Manager-LAB"},
        "device_bays": [],
        "serial": "",
        "interfaces": [
            {
                "id": "be8e95da-ce03-47fa-9dcf-2fbbf340f08a",
                "name": "DPU BMC",
                "mac_address": "58:A2:E1:84:74:FB",
                "device": {"name": "rno1-m04-c10-server4-dpu1.lab1"},
            },
            {
                "id": "36364607-21f5-45d2-9908-d08fee457aab",
                "name": "DPU Port 1",
                "mac_address": "58:A2:E1:84:74:D7",
                "device": {"name": "rno1-m04-c10-server4-dpu1.lab1"},
            },
        ],
    },
]
