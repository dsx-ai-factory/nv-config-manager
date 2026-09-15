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
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.client.device import DeviceMacEntry, DeviceMacTable


DEVICE_CONNECTION_DATA_VALID: dict[str, dict[str, Any]] = {
    "mock_device1": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "mock_device1",
        "rack": {"name": "a01"},
        "position": 1,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "intent": None,
        "actual_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device2",
                        "serial": None,
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": None,
                },
                "link_up": True,
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device2",
                        "serial": None,
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/1",
                    "device": {
                        "name": "mock_device3",
                        "serial": None,
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": None,
                },
                "link_up": True,
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/2",
                    "device": {
                        "name": "mock_device3",
                        "serial": None,
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": None,
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/1",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/2",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
        ],
    },
    "mock_device2": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
        "name": "mock_device2",
        "rack": {"name": "a01"},
        "position": 2,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.2"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/3",
                    "device": {
                        "name": "mock_device3",
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/4",
                    "device": {
                        "name": "mock_device3",
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/3",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/4",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
        ],
    },
    "mock_device3": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
        "name": "mock_device3",
        "rack": {"name": "a01"},
        "position": 3,
        "platform": {"name": "Arista EOS"},
        "role": {"name": "Tenant A Device"},
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
        "actual_connections": [
            {
                "name": "Ethernet1/1",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "Ethernet1/2",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "Ethernet1/3",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device2",
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "Ethernet1/4",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device3",
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "Ethernet1/1",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "Ethernet1/2",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "Ethernet1/3",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "Ethernet1/4",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
        ],
    },
    "MOCK-LEAF-04": {
        "id": "c2c2b336-d4f6-4645-8ac8-a4a968050232",
        "name": "MOCK-LEAF-04",
        "rack": {"name": "a01"},
        "position": 4,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "LEGACY01"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.4"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "swp0",
                "link_up": True,
                "connected_interface": {},
            },
            {
                "name": "swp1",
                "link_up": False,
                "connected_interface": {},
            },
            {
                "name": "swp2",
                "link_up": False,
                "connected_interface": {},
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "eth1",
                    "device": {
                        "name": "MOCK-Server-04",
                        "rack": {"name": "b01"},
                        "position": 4,
                        "serial": "MOCKSERIAL4",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:44",
                },
            },
        ],
    },
}

DEVICE_CONNECTION_DATA_INVALID: dict[str, dict[str, Any]] = {
    "mock_device1": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "mock_device1",
        "rack": {"name": "a01"},
        "position": 1,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp6",
                    "device": {
                        "name": "MOCK_DEVICE2",
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "SWP1",
                    "device": {
                        "name": "MOCK_DEVICE2",
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "swp9",
                    "device": {
                        "name": "mock_device2",
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/2",
                    "device": {
                        "name": "mock_device3",
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/1",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/2",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
        ],
    },
    "mock_device2": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050232",
        "name": "mock_device2",
        "rack": {"name": "a01"},
        "position": 2,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.2"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "swp6",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
            {
                "name": "swp0",
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
            {
                "name": "swp2",
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
            {
                "name": "swp9",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/4",
                    "device": {
                        "name": "mock_device3",
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
                "link_up": True,
            },
            {
                "name": "swp10",
                "connected_interface": {
                    "name": "swp11",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "swp0",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "swp1",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "Ethernet1/3",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
            {
                "name": "swp3",
                "connected_interface": {
                    "name": "Ethernet1/4",
                    "device": {
                        "name": "mock_device3",
                        "rack": {"name": "a01"},
                        "position": 3,
                        "serial": "MOCKSERIAL3",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
        ],
    },
    "mock_device3": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050214",
        "name": "mock_device3",
        "rack": {"name": "a01"},
        "position": 3,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.3"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "Ethernet1/2",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device1",
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
                "link_up": True,
            },
            {
                "name": "Ethernet1/4",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device2",
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
                "link_up": True,
            },
            {
                "name": "Ethernet1/1",
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
            {
                "name": "Ethernet1/3",
                "link_up": True,
            },
        ],
        "intended_connections": [
            {
                "name": "Ethernet1/1",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "Ethernet1/2",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device1",
                        "rack": {"name": "a01"},
                        "position": 1,
                        "serial": "MOCKSERIAL1",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "Ethernet1/3",
                "connected_interface": {
                    "name": "swp2",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "Ethernet1/4",
                "connected_interface": {
                    "name": "swp3",
                    "device": {
                        "name": "mock_device2",
                        "rack": {"name": "a01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
        ],
    },
    "MOCK-LEAF-04": {
        "id": "c2c2b336-d4f6-4645-8ac8-a4a968050232",
        "name": "MOCK-LEAF-04",
        "rack": {"name": "a01"},
        "position": 4,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "SITEA"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.4"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "actual_connections": [
            {
                "name": "swp0",
                "link_up": True,
            },
            {
                "name": "swp1",
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
            {
                "name": "swp2",
                "link_up": False,
                "ts_info": "Cable is unplugged.",
            },
        ],
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "eth1",
                    "device": {
                        "name": "MOCK-Server-04",
                        "rack": {"name": "b01"},
                        "position": 4,
                        "serial": "MOCKSERIAL4",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:44",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "eth1",
                    "device": {
                        "name": "MOCK-Server-05",
                        "rack": {"name": "b01"},
                        "position": 5,
                        "serial": "MOCKSERIAL5",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:45",
                },
            },
        ],
    },
}


DEVICE_CONNECTION_DATA_MAC_VALIDATION: dict[str, dict[str, Any]] = {
    "MOCK-IPMITOR-01": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "MOCK-IPMITOR-01",
        "rack": {"name": "a01"},
        "position": 1,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "LEGACY01"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "Server BMC",
                    "device": {
                        "name": "MOCK-Server-01",
                        "rack": {"name": "b01"},
                        "position": 1,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "DPU BMC",
                    "device": {
                        "name": "MOCK-Server-01-dpu01",
                        "rack": {"name": "b01"},
                        "position": 1,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "DPU BMC",
                    "device": {
                        "name": "MOCK-STR-01-dpu02",
                        "rack": {"name": "b01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:03",
                },
            },
        ],
        "actual_connections": [
            {
                "name": "swp0",
                "link_up": True,
                "connected_interface": {},
            },
            {
                "name": "swp1",
                "link_up": False,
                "connected_interface": {},
            },
            {
                "name": "swp2",
                "link_up": True,
                "connected_interface": {},
            },
        ],
    },
    "MOCK-IPMITOR-02": {
        "id": "c2c2b006-d4f6-4645-8ac8-a4a968050273",
        "name": "MOCK-IPMITOR-02",
        "rack": {"name": "a01"},
        "position": 2,
        "platform": {"name": "Cumulus Linux"},
        "role": {"name": "Tenant A Device"},
        "location": {"location_type": {"name": "Site"}, "name": "LEGACY01"},
        "device_type": {"model": "MSN4600-CS2FC"},
        "primary_ip4": {"host": "10.0.0.1"},
        "primary_ip6": None,
        "configmanagerdevicestatus": {
            "render_enabled": True,
            "deploy_enabled": True,
            "backup_enabled": True,
            "ztp_enabled": True,
        },
        "intended_connections": [
            {
                "name": "swp0",
                "connected_interface": {
                    "name": "Server BMC",
                    "device": {
                        "name": "MOCK-Server-01",
                        "rack": {"name": "b01"},
                        "position": 1,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:01",
                },
            },
            {
                "name": "swp1",
                "connected_interface": {
                    "name": "DPU BMC",
                    "device": {
                        "name": "MOCK-Server-01-dpu01",
                        "rack": {"name": "b01"},
                        "position": 1,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": "00:00:00:00:00:02",
                },
            },
            {
                "name": "swp2",
                "connected_interface": {
                    "name": "DPU BMC",
                    "device": {
                        "name": "MOCK-STR-01-dpu02",
                        "rack": {"name": "b01"},
                        "position": 2,
                        "serial": "MOCKSERIAL2",
                        "role": {"name": "Tenant A Device"},
                    },
                    "mac_address": None,
                },
            },
        ],
        "actual_connections": [
            {
                "name": "swp0",
                "link_up": True,
                "connected_interface": {},
            },
            {
                "name": "swp1",
                "link_up": False,
                "connected_interface": {},
            },
            {
                "name": "swp2",
                "link_up": False,
                "connected_interface": {},
            },
        ],
    },
}


DEVICE_CONNECTION_DATA_MAC_TABLE = DeviceMacTable(
    by_mac={
        "00-00-00-00-00-01": DeviceMacEntry(interface="swp0", mac="00-00-00-00-00-01", age=2798641),
        "00-00-00-00-00-02": DeviceMacEntry(interface="swp1", mac="00-00-00-00-00-02", age=2798641),
        "00-00-00-00-00-03": DeviceMacEntry(interface="swp2", mac="00-00-00-00-00-03", age=2798641),
    },
    by_interface={
        "swp0": ["00-00-00-00-00-01"],
        "swp1": ["00-00-00-00-00-02"],
        "swp2": ["00-00-00-00-00-03"],
    },
)


DEVICE_CONNECTION_DATA_MAC_TABLE_INVALID = DeviceMacTable(
    by_mac={
        "00-00-00-00-00-02": DeviceMacEntry(interface="swp0", mac="00-00-00-00-00-02", age=2798641),
        "00-00-00-00-00-03": DeviceMacEntry(interface="swp2", mac="00-00-00-00-00-03", age=2798641),
    },
    by_interface={"swp0": ["00-00-00-00-00-02"], "swp2": ["00-00-00-00-00-03"]},
)
