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
"""Nautobot workflow-provider response fixtures."""

NETWORK_DEVICE_V2 = {
    "data": {
        "device": {
            "id": "7cd2a91d-f6af-480b-9162-381cdfb4a66c",
            "name": "MOCK-LEAF-01",
            "role": {"name": "Tenant A Device"},
            "tenant": {"name": "Tenant A"},
            "device_type": {"model": "MSN4600"},
            "platform": {"name": "Cumulus Linux"},
            "location": {
                "name": "SITEA",
                "location_type": {"name": "Site"},
                "parent": {
                    "name": "REGION-A",
                    "location_type": {"name": "Region"},
                    "parent": {"name": "CLOUD-A", "location_type": {"name": "Region"}},
                },
            },
            "primary_ip4": {"host": "10.91.33.86"},
            "primary_ip6": None,
            "configmanagerdevicestatus": {
                "render_enabled": True,
                "deploy_enabled": True,
                "backup_enabled": True,
                "ztp_enabled": True,
            },
        }
    }
}

NETWORK_DEVICES_V2 = {
    "data": {
        "devices": [
            {
                "id": "7cd2a91d-f6af-480b-9162-381cdfb4a66c",
                "name": "MOCK-LEAF-01",
                "role": {"name": "Tenant A Device"},
                "tenant": {"name": "Tenant A"},
                "device_type": {"model": "MSN4600"},
                "platform": {"name": "Cumulus Linux"},
                "location": {
                    "name": "SITEA",
                    "location_type": {"name": "Site"},
                    "parent": {
                        "name": "REGION-A",
                        "location_type": {"name": "Region"},
                        "parent": {
                            "name": "CLOUD-A",
                            "location_type": {"name": "Region"},
                        },
                    },
                },
                "primary_ip4": {"host": "10.91.33.86"},
                "primary_ip6": None,
                "configmanagerdevicestatus": {
                    "render_enabled": True,
                    "deploy_enabled": True,
                    "backup_enabled": True,
                    "ztp_enabled": True,
                },
            }
        ]
    }
}

HOST_DEVICE_V2 = {
    "data": {
        "device": {
            "id": "192efd58-927b-4a56-8653-1864a40ffed9",
            "name": "MOCK-SERVER-01",
            "role": {"name": "Tenant A Device"},
            "device_type": {"model": "ThinkSystem SR670 V2"},
            "serial": "MOCKSERIAL1",
            "location": {
                "name": "SITEA",
                "location_type": {"name": "Site"},
                "parent": {
                    "name": "REGION-A",
                    "location_type": {"name": "Region"},
                    "parent": {"name": "CLOUD-A", "location_type": {"name": "Region"}},
                },
            },
            "device_bays": [
                {
                    "id": "d07b2a83-cb7d-4615-bc35-c8099a6b2dd1",
                    "name": "0",
                    "installed_device": {"id": "39a1af57-80a3-435c-8904-318164bde1f4"},
                }
            ],
            "interfaces": [
                {
                    "id": "b0c0cb7d-0213-4849-9ec4-265cc3cde4ca",
                    "name": "Server BMC",
                    "mac_address": "08:8F:C3:A6:E6:8F",
                    "device": {"name": "MOCK-SERVER-01"},
                },
                {
                    "id": "24d5c6f9-e2fe-442e-b412-f7092eed4798",
                    "name": "lo",
                    "mac_address": None,
                    "device": {"name": "MOCK-SERVER-01"},
                },
            ],
        }
    }
}

HOST_DEVICES_V2 = {
    "data": {
        "devices": [
            {
                "id": "192efd58-927b-4a56-8653-1864a40ffed9",
                "name": "MOCK-SERVER-01",
                "role": {"name": "Tenant A Device"},
                "device_type": {"model": "ThinkSystem SR670 V2"},
                "serial": "MOCKSERIAL1",
                "location": {
                    "name": "SITEA",
                    "location_type": {"name": "Site"},
                    "parent": {
                        "name": "REGION-A",
                        "location_type": {"name": "Region"},
                        "parent": {
                            "name": "CLOUD-A",
                            "location_type": {"name": "Region"},
                        },
                    },
                },
                "device_bays": [
                    {
                        "id": "d07b2a83-cb7d-4615-bc35-c8099a6b2dd1",
                        "name": "0",
                        "installed_device": {"id": "39a1af57-80a3-435c-8904-318164bde1f4"},
                    }
                ],
                "interfaces": [
                    {
                        "id": "b0c0cb7d-0213-4849-9ec4-265cc3cde4ca",
                        "name": "Server BMC",
                        "mac_address": "08:8F:C3:A6:E6:8F",
                        "device": {"name": "MOCK-SERVER-01"},
                    },
                    {
                        "id": "24d5c6f9-e2fe-442e-b412-f7092eed4798",
                        "name": "lo",
                        "mac_address": None,
                        "device": {"name": "MOCK-SERVER-01"},
                    },
                ],
            }
        ]
    }
}

DEVICE_INTERFACES_RESPONSE = {
    "data": {
        "interfaces": [
            {
                "id": "interface-1",
                "name": "swp1",
                "mac_address": "00-00-00-00-00-01",
                "device": {"name": "test-device"},
                "ip_addresses": [{"address": "10.0.0.1/24"}],
                "vrf": None,
            },
            {
                "id": "interface-2",
                "name": "swp2",
                "mac_address": "00-00-00-00-00-02",
                "device": {"name": "test-device"},
                "ip_addresses": [],
                "vrf": {"id": "vrf-1", "name": "test-vrf"},
            },
        ]
    }
}

DEVICE_VRFS_RESPONSE = {
    "data": {
        "device": {
            "vrfs": [
                {"id": "vrf-1", "name": "vrf-tenant-1"},
                {"id": "vrf-2", "name": "vrf-tenant-2"},
            ]
        }
    }
}

DEVICE_VRFS_EMPTY_RESPONSE: dict = {"data": {"device": {"vrfs": []}}}

DEVICE_GET_RESPONSE = {
    "id": "device-1",
    "name": "test-device",
    "vrfs": [
        {"id": "vrf-1", "name": "existing-vrf"},
    ],
}

DEVICE_GET_RESPONSE_NO_VRFS = {
    "id": "device-1",
    "name": "test-device",
    "vrfs": [],
}

DEVICE_PATCH_RESPONSE = {
    "id": "device-1",
    "name": "test-device",
    "vrfs": [
        {"id": "vrf-1", "name": "existing-vrf"},
        {"id": "vrf-2", "name": "new-vrf"},
    ],
}

INTERFACE_PATCH_RESPONSE = {
    "id": "interface-1",
    "name": "swp1",
    "mac_address": "00-00-00-00-00-01",
    "device": {"id": "test-device"},
    "vrf": {"id": "vrf-1"},
}
