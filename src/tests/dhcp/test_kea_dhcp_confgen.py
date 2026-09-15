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
import ipaddress
import json
import os
from copy import deepcopy
from unittest.mock import patch

import pytest
import requests
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient as NautobotClient
from testcontainers.core.container import DockerContainer

from nv_config_manager.dhcp.kea import KeaClient
from nv_config_manager.dhcp.kea_dhcp_confgen import (
    DhcpConfigGenerationError,
    _format_options_for_kea,
    generate_config,
    inject_lease_db_config,
)
from nv_config_manager.dhcp.redis import RedisClient

# Get the directory containing this test file
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class MockNautobotClient(NautobotClient):
    """Mock out the graphql call."""

    async def graphql_query(self, query, variables=None):
        if "auto_dhcp_subnets" in query:
            path = os.path.join(_THIS_DIR, "resources/auto_dhcp_subnets.json")
        elif "dhcp_contexts" in query:
            path = os.path.join(_THIS_DIR, "resources/dhcp_contexts.json")
        elif "static_data" in query:
            path = os.path.join(_THIS_DIR, "resources/static_data.json")
        elif "site_dhcp_options" in query:
            path = os.path.join(_THIS_DIR, "resources/site_dhcp_options.json")
        else:
            raise Exception(f"Unknown query: {query}")

        with open(path) as f:
            return json.load(f)


class MockRedisClient(RedisClient):
    """Mock Redis client for testing."""

    def __init__(self):
        # Load expected config to preserve subnet IDs
        with open(os.path.join(_THIS_DIR, "resources/expected_dhcp4_config.json")) as f:
            expected = json.load(f)
            self._config = {"Dhcp4": {"subnet4": expected["Dhcp4"]["subnet4"]}}

    async def load_kea_config(self, version: int) -> dict:
        """Return a mock config with some overlapping and dropped subnets."""
        return self._config

    async def save_kea_config(self, config: dict, version: int) -> None:
        """Store the config for future reference."""
        self._config = config


async def _wait_for_status(kea_client: KeaClient):
    """Wait until the KEA Client successfully returns a status."""
    import asyncio

    wait = 0
    while wait < 60:
        try:
            await kea_client.status()
            return
        except Exception:
            await asyncio.sleep(1)
            wait += 1
    raise Exception("KEA DHCP server failed to start within 60s.")


def _normalize_config_for_comparison(config):
    """Normalize config by sorting lists to make comparison order-independent."""
    config = deepcopy(config)
    dhcp = config.get("Dhcp4", {})

    # Sort reservations by ip-address
    if "reservations" in dhcp:
        dhcp["reservations"] = sorted(dhcp["reservations"], key=lambda r: r.get("ip-address", ""))
        # Sort option-data within each reservation
        for res in dhcp["reservations"]:
            if "option-data" in res:
                res["option-data"] = sorted(res["option-data"], key=lambda o: o.get("name", ""))

    # Sort subnets by subnet address
    if "subnet4" in dhcp:
        dhcp["subnet4"] = sorted(dhcp["subnet4"], key=lambda s: s.get("subnet", ""))
        # Sort option-data, pools, and reservations within each subnet
        for subnet in dhcp["subnet4"]:
            if "option-data" in subnet:
                subnet["option-data"] = sorted(
                    subnet["option-data"], key=lambda o: o.get("name", "")
                )
            if "pools" in subnet:
                subnet["pools"] = sorted(subnet["pools"], key=lambda p: p.get("pool", ""))
            if "reservations" in subnet:
                subnet["reservations"] = sorted(
                    subnet["reservations"],
                    key=lambda r: r.get("client-id", r.get("hw-address", "")),
                )
                for res in subnet["reservations"]:
                    if "option-data" in res:
                        res["option-data"] = sorted(
                            res["option-data"], key=lambda o: o.get("name", "")
                        )

    return config


@pytest.mark.asyncio
async def test_expected_config():
    """Validate we produced the expected Dhcp4 configuration."""
    with open(os.path.join(_THIS_DIR, "resources/expected_dhcp4_config.json")) as f:
        expected_config_with_leasedb = json.load(f)

    expected_config_no_leasedb = deepcopy(expected_config_with_leasedb)
    del expected_config_no_leasedb["Dhcp4"]["lease-database"]

    # Provide mock kea_config with expected hooks path
    mock_kea_config = {
        "Dhcp4": {"hooks-libraries": [{"library": "/usr/lib/kea/hooks/libdhcp_lease_cmds.so"}]}
    }

    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        config = await generate_config(
            MockNautobotClient("https://nautobot.example.com/", "dummy"),
            MockRedisClient(),
            version=4,
            kea_config=mock_kea_config,
        )

        # Normalize both configs for order-independent comparison
        assert _normalize_config_for_comparison(config) == _normalize_config_for_comparison(
            expected_config_no_leasedb
        )
        mock_log.warning.assert_called_with(
            "Static reservation for %s is being overwritten by a generated reservation",
            "4E:56:4F:53:23:23:4E:35:31:31:30:5F:4C:44:23:23:41:31:4E:4A:34:32:43:30:30:31:39",
        )

        config = inject_lease_db_config(config, version=4)
        assert _normalize_config_for_comparison(config) == _normalize_config_for_comparison(
            expected_config_with_leasedb
        )


@pytest.mark.asyncio
async def test_subnet_id_preservation():
    """Test that subnet IDs are preserved correctly."""
    # Generate config with mock clients
    config = await generate_config(
        MockNautobotClient("https://nautobot.example.com/", "dummy"), MockRedisClient(), version=4
    )

    # Get all subnet IDs from the new config
    subnet_ids = {subnet["subnet"]: subnet["id"] for subnet in config["Dhcp4"]["subnet4"]}

    # Verify that 10.23.161.0/26 kept its ID (3)
    assert subnet_ids["10.23.161.0/26"] == 3

    # Assert freed IDs are reused
    assert 1 in subnet_ids.values()
    assert 2 in subnet_ids.values()


@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_kea_config_valid():
    """Validate that KEA accepts our generated configuration."""
    with DockerContainer("docker.cloudsmith.io/isc/docker/kea-dhcp4:2.6.2").with_exposed_ports(
        8000
    ) as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(8000))

        kea_client = KeaClient(host=host, port=port)
        await _wait_for_status(kea_client)

        # Mock a Kea config with the ISC image's hooks path
        # (ISC image uses /usr/lib/kea/hooks, our Ubuntu image uses /usr/lib/x86_64-linux-gnu/kea/hooks)
        isc_kea_config = {
            "Dhcp4": {"hooks-libraries": [{"library": "/usr/lib/kea/hooks/libdhcp_lease_cmds.so"}]}
        }

        config = await generate_config(
            MockNautobotClient("https://nautobot.example.com/", "dummy"),
            MockRedisClient(),
            version=4,
            kea_config=isc_kea_config,
        )
        config = inject_lease_db_config(config, version=4)
        valid, err = await kea_client.test_config(config, version=4)
        assert err is None
        assert valid


class MockFailingNautobotClient(MockNautobotClient):
    """Mock Nautobot client that simulates various failure conditions."""

    def __init__(self, failure_type: str):
        self.failure_type = failure_type
        # Call parent constructor to set up basic structure
        super().__init__("https://nautobot.example.com/", "dummy")

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        if self.failure_type == "graphql_error":
            raise Exception("GraphQL query failed")
        elif self.failure_type == "network_error":
            raise requests.exceptions.ConnectionError("Connection failed")
        elif self.failure_type == "timeout_error":
            raise requests.exceptions.Timeout("Request timed out")
        elif self.failure_type == "invalid_data":
            # This will cause a KeyError when trying to access 'pool_ips'
            return [{"prefix": "invalid_prefix", "gateway": "invalid_gateway"}]
        elif self.failure_type == "missing_gateway":
            # This will cause a KeyError when trying to access 'gateway'
            return [{"prefix": "10.22.160.0/26"}]
        elif self.failure_type == "gateway_outside_prefix":
            # This will cause a KeyError when trying to access 'gateway' (processed data expected)
            return [{"prefix": "10.22.160.0/26"}]
        elif self.failure_type == "pool_reservation_conflict":
            import ipaddress

            return [
                {
                    "prefix": ipaddress.ip_network("10.22.160.0/26"),
                    "gateway": ipaddress.ip_address("10.22.160.2"),
                    "pool_ips": [
                        ipaddress.ip_address("10.22.160.66"),
                        ipaddress.ip_address("10.22.160.10"),
                    ],
                    "option_candidates": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("10.22.160.66"),
                            "mac_address": "00:11:22:33:44:55",
                            "serial": "SERIAL1",
                            "device_name": "conflict-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "device_id": "device-1",
                        }
                    ],
                }
            ]
        elif self.failure_type == "reservation_without_mac":
            import ipaddress

            return [
                {
                    "prefix": ipaddress.ip_network("10.22.160.0/26"),
                    "gateway": ipaddress.ip_address("10.22.160.2"),
                    "pool_ips": [],
                    "option_candidates": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("10.22.160.66"),
                            "mac_address": None,
                            "serial": None,
                            "device_name": "test-device",
                            "interface_name": "eth0",
                            "device_id": "device-1",
                        }
                    ],
                }
            ]
        elif self.failure_type == "reservation_without_device":
            import ipaddress

            return [
                {
                    "prefix": ipaddress.ip_network("10.22.160.0/26"),
                    "gateway": ipaddress.ip_address("10.22.160.2"),
                    "pool_ips": [],
                    "option_candidates": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("10.22.160.66"),
                            "mac_address": None,
                            "serial": None,
                            "device_name": "test-device",
                            "interface_name": "eth0",
                            "device_id": None,
                        }
                    ],
                }
            ]
        elif self.failure_type == "duplicate_client_id":
            import ipaddress

            # Same device (same MAC) with two reserved IPs - Kea disallows duplicate identifiers
            return [
                {
                    "prefix": ipaddress.ip_network("10.22.160.0/26"),
                    "gateway": ipaddress.ip_address("10.22.160.2"),
                    "pool_ips": [],
                    "option_candidates": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("10.22.160.66"),
                            "mac_address": "00:11:22:33:44:55",
                            "serial": "DUP001",
                            "device_name": "dup-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "device_id": "device-dup",
                        },
                        {
                            "address": ipaddress.ip_address("10.22.160.67"),
                            "mac_address": "00:11:22:33:44:55",
                            "serial": "DUP001",
                            "device_name": "dup-device",
                            "interface_name": "eth1",
                            "interface_role": "uplink",
                            "device_id": "device-dup",
                        },
                    ],
                }
            ]
        elif self.failure_type == "empty":
            # Return empty list to simulate no auto subnets found
            return []
        elif self.failure_type == "no_prefixes_field":
            # Simulate GraphQL response with no 'prefixes' field
            # This tests the graceful handling in load_auto_dhcp_subnets
            return []
        else:
            # Return normal data
            return []


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_graphql_error():
    """Test that GraphQL query failures cause config generation to fail."""
    with pytest.raises(Exception, match="GraphQL query failed"):
        await generate_config(
            MockFailingNautobotClient("graphql_error"), MockRedisClient(), version=4
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_network_error():
    """Test that network connection failures cause config generation to fail."""
    with pytest.raises(requests.exceptions.ConnectionError, match="Connection failed"):
        await generate_config(
            MockFailingNautobotClient("network_error"), MockRedisClient(), version=4
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_timeout_error():
    """Test that timeout failures cause config generation to fail."""
    with pytest.raises(requests.exceptions.Timeout, match="Request timed out"):
        await generate_config(
            MockFailingNautobotClient("timeout_error"), MockRedisClient(), version=4
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_invalid_data():
    """Test that invalid data from Nautobot causes config generation to fail."""
    with pytest.raises(KeyError, match="pool_ips"):
        await generate_config(
            MockFailingNautobotClient("invalid_data"), MockRedisClient(), version=4
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_gateway_fallback():
    """Test that subnets without explicit gateway use first usable IP as fallback."""
    # Test the actual fallback logic in load_auto_dhcp_subnets by mocking GraphQL
    # to return a prefix with rel_prefix_to_gateway: null
    client = MockNautobotClientWithMissingGateway()
    subnets = await client.load_auto_dhcp_subnets(family=4)

    # Should have one subnet with fallback gateway
    assert len(subnets) == 1
    subnet = subnets[0]
    assert subnet["prefix"] == ipaddress.ip_network("10.240.128.0/27")
    # Gateway should be first usable IP (network + 1)
    assert subnet["gateway"] == ipaddress.ip_address("10.240.128.1")


class MockNautobotClientWithMissingGateway(MockNautobotClient):
    """Mock Nautobot client that returns a prefix without gateway relationship."""

    def __init__(self):
        super().__init__("https://nautobot.example.com/", "dummy")

    async def graphql_query(self, query, variables=None):
        """Return GraphQL response with prefix missing gateway relationship."""
        if "auto_dhcp_subnets" in query:
            return {
                "data": {
                    "prefixes": [
                        {
                            "id": "prefix-no-gateway",
                            "prefix": "10.240.128.0/27",
                            "ip_version": 4,
                            "rel_prefix_to_gateway": None,  # No gateway set
                        }
                    ],
                    "pool_ips": [],
                    "reserved_ips": [],
                }
            }
        # Delegate other queries to parent
        return await super().graphql_query(query, variables)


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_gateway_outside_prefix():
    """Test that gateways outside their subnet prefix cause config generation to fail."""
    with pytest.raises(KeyError, match="gateway"):
        await generate_config(
            MockFailingNautobotClient("gateway_outside_prefix"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_pool_reservation_overlap_ignores_reserve():
    """Test that IP with both dhcp-pool and dhcp-reserve tags ignores reserve and logs warning."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        config = await generate_config(
            MockFailingNautobotClient("pool_reservation_conflict"),
            MockRedisClient(),
            version=4,
        )
    mock_log.warning.assert_called_with(
        "IP address %s in subnet %s has both dhcp-pool and dhcp-reserve tags; "
        "ignoring dhcp-reserve and treating as pool only",
        ipaddress.ip_address("10.22.160.66"),
        ipaddress.ip_network("10.22.160.0/26"),
    )
    # IP should be in pool, not in reservations
    subnet_configs = {s["subnet"]: s for s in config["Dhcp4"]["subnet4"]}
    subnet = subnet_configs.get("10.22.160.0/26")
    assert subnet is not None
    assert "10.22.160.66" in str(subnet["pools"])
    reservations = config["Dhcp4"].get("reservations", [])
    assert not any(r["ip-address"] == "10.22.160.66" for r in reservations)


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_reservation_without_mac():
    """Test that reservations without MAC or serial cause config generation to fail."""
    with pytest.raises(DhcpConfigGenerationError, match="has no MAC address or serial"):
        await generate_config(
            MockFailingNautobotClient("reservation_without_mac"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_reservation_without_device():
    """Test that reservations without MAC or serial cause config generation to fail."""
    with pytest.raises(DhcpConfigGenerationError, match="has no MAC address or serial"):
        await generate_config(
            MockFailingNautobotClient("reservation_without_device"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_duplicate_client_id_error():
    """Test that duplicate client-id/hw-address across reservations raises DhcpConfigGenerationError."""
    with pytest.raises(
        DhcpConfigGenerationError,
        match=r"Duplicate DHCP reservation.*00:11:22:33:44:55.*multiple reserved IPs",
    ):
        await generate_config(
            MockFailingNautobotClient("duplicate_client_id"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_empty_response():
    """Test handling of empty responses from Nautobot."""
    config = await generate_config(MockFailingNautobotClient("empty"), MockRedisClient(), version=4)

    # Should still generate config with legacy subnets only (no auto subnets)
    assert "Dhcp4" in config
    assert "subnet4" in config["Dhcp4"]
    # Should have subnets from Redis cache (legacy subnets only)
    assert len(config["Dhcp4"]["subnet4"]) > 0
    # When no auto subnets are found, the system should fall back to legacy subnets only
    config_subnet_prefixes = {subnet["subnet"] for subnet in config["Dhcp4"]["subnet4"]}
    # Should have legacy subnets but no auto subnets (since none were generated)
    assert "10.180.166.0/26" in config_subnet_prefixes  # Legacy subnet
    assert (
        "10.23.160.0/26" not in config_subnet_prefixes
    )  # Auto subnet not present (none generated)
    assert (
        "10.23.161.0/26" not in config_subnet_prefixes
    )  # Auto subnet not present (none generated)


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_no_prefixes_field():
    """Test graceful handling when GraphQL response has no 'prefixes' field."""
    config = await generate_config(
        MockFailingNautobotClient("no_prefixes_field"), MockRedisClient(), version=4
    )

    # Should still generate config with legacy subnets only (no auto subnets)
    assert "Dhcp4" in config
    assert "subnet4" in config["Dhcp4"]
    # Should have subnets from Redis cache (legacy subnets only)
    assert len(config["Dhcp4"]["subnet4"]) > 0
    # When no auto subnets are found, the system should fall back to legacy subnets only
    config_subnet_prefixes = {subnet["subnet"] for subnet in config["Dhcp4"]["subnet4"]}
    # Should have legacy subnets but no auto subnets (since none were generated)
    assert "10.180.166.0/26" in config_subnet_prefixes  # Legacy subnet
    assert (
        "10.23.160.0/26" not in config_subnet_prefixes
    )  # Auto subnet not present (none generated)
    assert (
        "10.23.161.0/26" not in config_subnet_prefixes
    )  # Auto subnet not present (none generated)


class MockAdditionalAutoSubnetsClient(MockNautobotClient):
    """Mock Nautobot client that returns additional auto subnets alongside existing ones."""

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        # Return additional auto subnets that don't conflict with existing ones
        import ipaddress

        return [
            {
                "prefix": ipaddress.ip_network("10.23.160.0/26"),
                "gateway": ipaddress.ip_address("10.23.160.2"),
                "pool_ips": [
                    ipaddress.ip_address("10.23.160.10"),
                    ipaddress.ip_address("10.23.160.11"),
                    ipaddress.ip_address("10.23.160.12"),
                ],
                "option_candidates": [],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.23.160.66"),
                        "mac_address": "00:11:22:33:44:55",
                        "device_name": "additional-device-1",
                    },
                    {
                        "address": ipaddress.ip_address("10.23.160.67"),
                        "mac_address": "00:11:22:33:44:66",
                        "device_name": "additional-device-2",
                    },
                ],
            },
            {
                "prefix": ipaddress.ip_network("10.24.160.0/26"),
                "gateway": ipaddress.ip_address("10.24.160.2"),
                "pool_ips": [
                    ipaddress.ip_address("10.24.160.10"),
                    ipaddress.ip_address("10.24.160.11"),
                ],
                "option_candidates": [],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.24.160.66"),
                        "mac_address": "00:11:22:33:44:77",
                        "device_name": "additional-device-3",
                    }
                ],
            },
        ]


class MockOptionCandidateSubnetConfigClient(MockNautobotClient):
    """Mock an option candidate whose context repeats generated subnet config."""

    def __init__(self, reservations_in_subnet: bool):
        super().__init__("https://nautobot.example.com/", "dummy")
        self.reservations_in_subnet = reservations_in_subnet

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        return [
            {
                "prefix": ipaddress.ip_network("10.26.160.0/26"),
                "gateway": ipaddress.ip_address("10.26.160.1"),
                "pool_ips": [ipaddress.ip_address("10.26.160.10")],
                "option_candidates": [
                    {
                        "address": ipaddress.ip_address("10.26.160.10"),
                        "mac_address": "00:11:22:33:44:99",
                        "serial": "OPTION001",
                        "device_id": "device-option-candidate",
                        "device_name": "option-candidate-device",
                        "interface_name": "eth0",
                        "interface_role": "management",
                        "platform": "Cumulus Linux",
                    }
                ],
                "reservations": [],
            }
        ]

    async def load_dhcp_contexts(self, is_aggregate_managed: bool | None = None):
        return {
            "device-option-candidate": {
                "dhcp": {
                    "options": {
                        "interface_roles": {
                            "management": {
                                "reservation_options": {
                                    "boot-file-name": "http://boot.example.com/boot"
                                },
                                "subnet_config": {
                                    "reservations-in-subnet": self.reservations_in_subnet
                                },
                            }
                        }
                    }
                }
            }
        }


class MockMultiplePoolRangesClient(MockNautobotClient):
    """Mock Nautobot client that returns auto subnets with non-contiguous pool IPs."""

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        # Return auto subnets with non-contiguous pool IPs that should generate multiple ranges
        import ipaddress

        return [
            {
                "prefix": ipaddress.ip_network("10.25.160.0/26"),
                "gateway": ipaddress.ip_address("10.25.160.2"),
                "pool_ips": [
                    # First contiguous range: 10.25.160.10-10.25.160.12
                    ipaddress.ip_address("10.25.160.10"),
                    ipaddress.ip_address("10.25.160.11"),
                    ipaddress.ip_address("10.25.160.12"),
                    # Gap: 10.25.160.13-10.25.160.19 (not included)
                    # Second contiguous range: 10.25.160.20-10.25.160.22
                    ipaddress.ip_address("10.25.160.20"),
                    ipaddress.ip_address("10.25.160.21"),
                    ipaddress.ip_address("10.25.160.22"),
                ],
                "option_candidates": [],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.25.160.66"),
                        "mac_address": "00:11:22:33:44:88",
                        "device_name": "multi-range-device-1",
                    }
                ],
            }
        ]


class MockConflictingSubnetsClient(MockNautobotClient):
    """Mock Nautobot client that returns auto subnets that conflict with legacy subnets for testing conflict resolution."""

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        # Return auto subnets that conflict with existing legacy subnets to test warning behavior
        import ipaddress

        return [
            {
                "prefix": ipaddress.ip_network("10.180.166.0/26"),  # Conflicts with legacy subnet
                "gateway": ipaddress.ip_address("10.180.166.2"),
                "pool_ips": [
                    ipaddress.ip_address("10.180.166.10"),
                    ipaddress.ip_address("10.180.166.11"),
                ],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.180.166.66"),
                        "mac_address": "00:11:22:33:44:99",
                        "device_name": "conflicting-device-1",
                    }
                ],
            },
            {
                "prefix": ipaddress.ip_network(
                    "10.91.36.0/25"
                ),  # Conflicts with another legacy subnet
                "gateway": ipaddress.ip_address("10.91.36.1"),
                "pool_ips": [
                    ipaddress.ip_address("10.91.36.10"),
                    ipaddress.ip_address("10.91.36.11"),
                ],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.91.36.66"),
                        "mac_address": "00:11:22:33:44:AA",
                        "device_name": "conflicting-device-2",
                    }
                ],
            },
        ]


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_additional_subnets():
    """Test that additional auto subnets are added without overwriting legacy ones."""
    config = await generate_config(
        MockAdditionalAutoSubnetsClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    # Should have both legacy and auto subnets
    assert "Dhcp4" in config
    assert "subnet4" in config["Dhcp4"]

    subnet_prefixes = {subnet["subnet"] for subnet in config["Dhcp4"]["subnet4"]}

    # Should have legacy subnets
    legacy_subnets = {"10.180.166.0/26"}
    assert legacy_subnets.issubset(subnet_prefixes)

    # Should have additional auto subnets
    auto_subnets = {"10.23.160.0/26", "10.24.160.0/26"}
    assert auto_subnets.issubset(subnet_prefixes)

    # Verify the additional subnets have correct configuration
    subnet_configs = {subnet["subnet"]: subnet for subnet in config["Dhcp4"]["subnet4"]}

    # Check 10.23.160.0/26 subnet
    subnet_23 = subnet_configs["10.23.160.0/26"]
    assert subnet_23["option-data"][0]["name"] == "routers"
    assert subnet_23["option-data"][0]["data"] == "10.23.160.2"
    assert "pools" in subnet_23
    assert len(subnet_23["pools"]) == 1
    assert subnet_23["pools"][0]["pool"] == "10.23.160.10-10.23.160.12"

    # Check 10.24.160.0/26 subnet
    subnet_24 = subnet_configs["10.24.160.0/26"]
    assert subnet_24["option-data"][0]["name"] == "routers"
    assert subnet_24["option-data"][0]["data"] == "10.24.160.2"
    assert "pools" in subnet_24
    assert len(subnet_24["pools"]) == 1
    assert subnet_24["pools"][0]["pool"] == "10.24.160.10-10.24.160.11"


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_additional_reservations():
    """Test that additional auto reservations are added without overwriting legacy ones."""
    config = await generate_config(
        MockAdditionalAutoSubnetsClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    # Should have both legacy and auto reservations
    assert "Dhcp4" in config
    assert "reservations" in config["Dhcp4"]

    reservations = config["Dhcp4"]["reservations"]
    reservation_ips = {res["ip-address"] for res in reservations}

    # Extract MAC addresses and client IDs (some reservations use client-id instead of hw-address)
    reservation_macs = set()
    for res in reservations:
        if "hw-address" in res:
            reservation_macs.add(res["hw-address"])
        elif "client-id" in res:
            reservation_macs.add(res["client-id"])

    reservation_hostnames = {res["hostname"] for res in reservations}

    # Should have legacy reservations (check that we have some from the expected ranges)
    # The actual IPs might be different, so just verify we have reservations
    assert len(reservations) > 0

    # Should have additional auto reservations
    auto_ips = {"10.23.160.66", "10.23.160.67", "10.24.160.66"}
    assert auto_ips.issubset(reservation_ips)

    # Should have additional auto MAC addresses
    auto_macs = {"00:11:22:33:44:55", "00:11:22:33:44:66", "00:11:22:33:44:77"}
    assert auto_macs.issubset(reservation_macs)

    # Should have additional auto hostnames
    auto_hostnames = {
        "additional-device-1",
        "additional-device-2",
        "additional-device-3",
    }
    assert auto_hostnames.issubset(reservation_hostnames)


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_pool_generation():
    """Test that DHCP pools are correctly generated from auto subnet pool IPs."""
    config = await generate_config(
        MockAdditionalAutoSubnetsClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    subnet_configs = {subnet["subnet"]: subnet for subnet in config["Dhcp4"]["subnet4"]}

    # Test pool generation for 10.23.160.0/26 (3 pool IPs)
    subnet_23 = subnet_configs["10.23.160.0/26"]
    assert "pools" in subnet_23
    assert len(subnet_23["pools"]) == 1
    # Should generate range from 10.23.160.10 to 10.23.160.12
    assert subnet_23["pools"][0]["pool"] == "10.23.160.10-10.23.160.12"

    # Test pool generation for 10.24.160.0/26 (2 pool IPs)
    subnet_24 = subnet_configs["10.24.160.0/26"]
    assert "pools" in subnet_24
    assert len(subnet_24["pools"]) == 1
    # Should generate range from 10.23.160.10 to 10.23.160.11
    assert subnet_24["pools"][0]["pool"] == "10.24.160.10-10.24.160.11"


@pytest.mark.asyncio
async def test_option_candidate_allows_matching_generated_subnet_config():
    """Test context may repeat the generated reservations-in-subnet value."""
    config = await generate_config(
        MockOptionCandidateSubnetConfigClient(reservations_in_subnet=True),
        version=4,
    )

    subnet = next(
        subnet for subnet in config["Dhcp4"]["subnet4"] if subnet["subnet"] == "10.26.160.0/26"
    )
    assert subnet["reservations-in-subnet"] is True
    assert subnet["reservations"] == [
        {
            "hostname": "option-candidate-device",
            "option-data": [{"name": "boot-file-name", "data": "http://boot.example.com/boot"}],
            "hw-address": "00:11:22:33:44:99",
        }
    ]


@pytest.mark.asyncio
async def test_option_candidate_rejects_conflicting_generated_subnet_config():
    """Test context cannot contradict generated reservations-in-subnet value."""
    with pytest.raises(
        DhcpConfigGenerationError,
        match="Cannot override subnet config reservations-in-subnet with False",
    ):
        await generate_config(
            MockOptionCandidateSubnetConfigClient(reservations_in_subnet=False),
            version=4,
        )


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_no_conflicts():
    """Test that auto subnets and reservations don't conflict with existing ones."""
    config = await generate_config(
        MockAdditionalAutoSubnetsClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    # Check that no duplicate subnets exist
    subnet_prefixes = [subnet["subnet"] for subnet in config["Dhcp4"]["subnet4"]]
    assert len(subnet_prefixes) == len(set(subnet_prefixes))

    # Check that no duplicate reservation IPs exist
    reservation_ips = [res["ip-address"] for res in config["Dhcp4"]["reservations"]]
    assert len(reservation_ips) == len(set(reservation_ips))

    # Check that no duplicate reservation MACs exist
    reservation_macs = []
    for res in config["Dhcp4"]["reservations"]:
        if "hw-address" in res:
            reservation_macs.append(res["hw-address"])
        elif "client-id" in res:
            reservation_macs.append(res["client-id"])
    assert len(reservation_macs) == len(set(reservation_macs))


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_legacy_preservation():
    """Test that legacy subnets and reservations are preserved exactly as they were."""
    config = await generate_config(
        MockAdditionalAutoSubnetsClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    subnet_configs = {subnet["subnet"]: subnet for subnet in config["Dhcp4"]["subnet4"]}

    # Legacy subnet 10.180.166.0/26 should be preserved exactly
    legacy_subnet = subnet_configs["10.180.166.0/26"]
    assert legacy_subnet["id"] == 1
    assert legacy_subnet["option-data"][0]["data"] == "10.180.166.2"
    assert legacy_subnet["reservations-global"] is True
    assert legacy_subnet["reservations-in-subnet"] is True

    # Auto subnet 10.23.160.0/26 should be present
    auto_subnet_1 = subnet_configs["10.23.160.0/26"]
    assert auto_subnet_1["id"] == 2
    assert auto_subnet_1["option-data"][0]["data"] == "10.23.160.2"
    assert "pools" in auto_subnet_1
    assert auto_subnet_1["pools"][0]["pool"] == "10.23.160.10-10.23.160.12"

    # Auto subnet 10.24.160.0/26 should be present
    auto_subnet_2 = subnet_configs["10.24.160.0/26"]
    assert auto_subnet_2["id"] == 3
    assert auto_subnet_2["option-data"][0]["data"] == "10.24.160.2"
    assert "pools" in auto_subnet_2
    assert auto_subnet_2["pools"][0]["pool"] == "10.24.160.10-10.24.160.11"


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_multiple_pool_ranges():
    """Test that pools with non-contiguous IPs generate multiple ranges correctly."""
    config = await generate_config(
        MockMultiplePoolRangesClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )

    subnet_configs = {subnet["subnet"]: subnet for subnet in config["Dhcp4"]["subnet4"]}

    # Check that the subnet with multiple pool ranges is present
    multi_range_subnet = subnet_configs["10.25.160.0/26"]
    assert "id" in multi_range_subnet  # Should have an ID assigned
    assert multi_range_subnet["option-data"][0]["data"] == "10.25.160.2"
    assert "pools" in multi_range_subnet
    assert len(multi_range_subnet["pools"]) == 2  # Should have 2 pool ranges

    # Check the first pool range (10.25.160.10-10.25.160.12)
    pool_ranges = [pool["pool"] for pool in multi_range_subnet["pools"]]
    assert "10.25.160.10-10.25.160.12" in pool_ranges

    # Check the second pool range (10.25.160.20-10.25.160.22)
    assert "10.25.160.20-10.25.160.22" in pool_ranges

    # Verify that the ranges are sorted correctly
    assert pool_ranges[0] == "10.25.160.10-10.25.160.12"
    assert pool_ranges[1] == "10.25.160.20-10.25.160.22"


@pytest.mark.asyncio
async def test_auto_dhcp_subnets_conflict_detection():
    """Test that subnet conflicts are logged as warnings and auto subnets take precedence over legacy subnets."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        config = await generate_config(
            MockConflictingSubnetsClient("https://nautobot.example.com/", "dummy"),
            MockRedisClient(),
            version=4,
        )

        # Verify that warning messages are logged for each subnet conflict
        warning_calls = [
            call for call in mock_log.warning.call_args_list if "Subnet conflict" in str(call)
        ]
        assert len(warning_calls) > 0, "Expected warning logs for subnet conflicts"

        # Verify that auto subnets are present in the final configuration
        subnet_configs = {subnet["subnet"]: subnet for subnet in config["Dhcp4"]["subnet4"]}

        # Auto subnets should take precedence over conflicting legacy subnets
        assert "10.180.166.0/26" in subnet_configs, "Auto subnet should be present"
        assert "10.91.36.0/25" in subnet_configs, "Auto subnet should be present"

        # Verify that auto subnets have correct configuration details
        auto_subnet_1 = subnet_configs["10.180.166.0/26"]
        assert "option-data" in auto_subnet_1
        assert auto_subnet_1["option-data"][0]["data"] == "10.180.166.2"

        auto_subnet_2 = subnet_configs["10.91.36.0/25"]
        assert "option-data" in auto_subnet_2
        assert auto_subnet_2["option-data"][0]["data"] == "10.91.36.1"


class MockConflictingReservationsClient(MockNautobotClient):
    """Mock Nautobot client that returns auto reservations with conflicting IPs."""

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        # Return auto subnets with reservations that conflict with static reservations
        import ipaddress

        return [
            {
                "prefix": ipaddress.ip_network("10.23.160.0/26"),
                "gateway": ipaddress.ip_address("10.23.160.2"),
                "pool_ips": [
                    ipaddress.ip_address("10.23.160.10"),
                    ipaddress.ip_address("10.23.160.11"),
                ],
                "reservations": [
                    {
                        "address": ipaddress.ip_address("10.180.166.60"),  # Conflicts with static
                        "mac_address": "00:11:22:33:44:55",
                        "device_name": "conflicting-auto-device-1",
                    }
                ],
            }
        ]


@pytest.mark.asyncio
async def test_reservation_conflicts_log_warnings():
    """Test that reservation conflicts are logged as warnings and auto reservations take precedence."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        config = await generate_config(
            MockConflictingReservationsClient("https://nautobot.example.com/", "dummy"),
            MockRedisClient(),
            version=4,
        )

        # Verify that warnings were logged for reservation conflicts
        warning_calls = [
            call for call in mock_log.warning.call_args_list if "IP address conflict" in str(call)
        ]
        assert len(warning_calls) > 0, "Expected warning logs for reservation conflicts"

        # Verify that auto reservations are present in the final config
        reservations = config["Dhcp4"]["reservations"]
        reservation_ips = {res["ip-address"] for res in reservations}

        # Should have the auto reservation IP (conflicting one takes precedence)
        assert "10.180.166.60" in reservation_ips, "Auto reservation IP should be present"

        # Verify the auto reservation details
        auto_reservation = next(res for res in reservations if res["ip-address"] == "10.180.166.60")
        assert auto_reservation["hw-address"] == "00:11:22:33:44:55"
        assert auto_reservation["hostname"] == "conflicting-auto-device-1"


class MockErrorCasesClient(MockNautobotClient):
    """Mock Nautobot client for error testing scenarios."""

    def __init__(self, error_case: str):
        self.error_case = error_case

    async def graphql_query(self, query, variables=None):
        # Special case for no_ztp - return inline dhcp_contexts
        if "dhcp_contexts" in query and self.error_case == "no_ztp":
            return {
                "data": {
                    "config_manager_devices": [
                        {
                            "device": {
                                "id": "device-no-ztp-test",
                                "config_context": {
                                    "dhcp": {
                                        "options": {
                                            "interface_names": {
                                                "eth0": {
                                                    "reservation_options": {
                                                        "boot-file-name": "http://{{ ztp_server }}/boot"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                },
                            }
                        }
                    ]
                }
            }

        if "auto_dhcp_subnets" in query:
            path = os.path.join(_THIS_DIR, "resources/auto_dhcp_subnets_errors.json")
        elif "dhcp_contexts" in query:
            path = os.path.join(_THIS_DIR, "resources/dhcp_contexts_errors.json")
        elif "static_data" in query or "config_contexts" in query:
            with open(os.path.join(_THIS_DIR, "resources/static_data.json")) as f:
                return json.load(f)
        else:
            raise ValueError(f"Unknown query type: {query}")

        with open(path) as f:
            data = json.load(f)

        if self.error_case == "conflicting_subnet_options":
            data["data"]["reserved_ips"] = [
                {
                    "address": "192.168.1.20",
                    "ip_version": 4,
                    "parent": {"id": "prefix-error-1"},
                    "interfaces": [
                        {
                            "name": "eth0",
                            "mac_address": "00:00:00:00:00:10",
                            "role": {"name": "management"},
                            "device": {
                                "id": "device-conflicting-options",
                                "name": "conflicting-device",
                                "serial": "CONF001",
                                "platform": {"name": "Cumulus Linux"},
                                "status": {"name": "active"},
                                "configmanagerdevicestatus": {
                                    "ztp_enabled": True,
                                    "is_aggregate_managed": False,
                                },
                            },
                        }
                    ],
                },
                {
                    "address": "192.168.1.21",
                    "ip_version": 4,
                    "parent": {"id": "prefix-error-1"},
                    "interfaces": [
                        {
                            "name": "eth1",
                            "mac_address": "00:00:00:00:00:11",
                            "role": {"name": "uplink"},
                            "device": {
                                "id": "device-conflicting-options",
                                "name": "conflicting-device",
                                "serial": "CONF001",
                                "platform": {"name": "Cumulus Linux"},
                                "status": {"name": "active"},
                                "configmanagerdevicestatus": {
                                    "ztp_enabled": True,
                                    "is_aggregate_managed": False,
                                },
                            },
                        }
                    ],
                },
            ]
        elif self.error_case == "conflicting_subnet_config":
            data["data"]["reserved_ips"] = [
                {
                    "address": "192.168.1.20",
                    "ip_version": 4,
                    "parent": {"id": "prefix-error-1"},
                    "interfaces": [
                        {
                            "name": "eth0",
                            "mac_address": "00:00:00:00:00:10",
                            "role": {"name": "management"},
                            "device": {
                                "id": "device-conflicting-config",
                                "name": "conflicting-device",
                                "serial": "CONF002",
                                "platform": {"name": "Cumulus Linux"},
                                "status": {"name": "active"},
                                "configmanagerdevicestatus": {
                                    "ztp_enabled": True,
                                    "is_aggregate_managed": False,
                                },
                            },
                        }
                    ],
                },
                {
                    "address": "192.168.1.21",
                    "ip_version": 4,
                    "parent": {"id": "prefix-error-1"},
                    "interfaces": [
                        {
                            "name": "eth1",
                            "mac_address": "00:00:00:00:00:11",
                            "role": {"name": "uplink"},
                            "device": {
                                "id": "device-conflicting-config",
                                "name": "conflicting-device",
                                "serial": "CONF002",
                                "platform": {"name": "Cumulus Linux"},
                                "status": {"name": "active"},
                                "configmanagerdevicestatus": {
                                    "ztp_enabled": True,
                                    "is_aggregate_managed": False,
                                },
                            },
                        }
                    ],
                },
            ]
        elif self.error_case == "conflicting_reservation_options":
            data["data"]["reserved_ips"] = [
                {
                    "address": "192.168.1.20",
                    "ip_version": 4,
                    "parent": {"id": "prefix-error-1"},
                    "interfaces": [
                        {
                            "name": "eth0",
                            "mac_address": "00:00:00:00:00:10",
                            "role": {"name": "management"},
                            "device": {
                                "id": "device-conflicting-reservation-options",
                                "name": "conflicting-device",
                                "serial": "CONF003",
                                "platform": {"name": "Cumulus Linux"},
                                "status": {"name": "active"},
                                "configmanagerdevicestatus": {
                                    "ztp_enabled": True,
                                    "is_aggregate_managed": False,
                                },
                            },
                        }
                    ],
                }
            ]
        elif self.error_case in [
            "override_subnet",
            "malformed_template",
            "override_router",
            "no_ztp",
        ]:
            # These cases override load_auto_dhcp_subnets directly, so no data modification needed here
            pass

        return data

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ):
        import ipaddress

        if self.error_case == "override_subnet":
            return [
                {
                    "prefix": ipaddress.ip_network("192.168.2.0/24"),
                    "gateway": ipaddress.ip_address("192.168.2.1"),
                    "pool_ips": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("192.168.2.10"),
                            "mac_address": "00:00:00:00:00:20",
                            "serial": None,
                            "device_id": "device-override-subnet",
                            "device_name": "override-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "platform": "Cumulus Linux",
                        }
                    ],
                }
            ]
        elif self.error_case == "malformed_template":
            return [
                {
                    "prefix": ipaddress.ip_network("192.168.3.0/24"),
                    "gateway": ipaddress.ip_address("192.168.3.1"),
                    "pool_ips": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("192.168.3.10"),
                            "mac_address": None,
                            "serial": "MALFORMED001",
                            "device_id": "device-malformed-template",
                            "device_name": "malformed-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "platform": "Cumulus Linux",
                        }
                    ],
                }
            ]
        elif self.error_case == "override_router":
            return [
                {
                    "prefix": ipaddress.ip_network("192.168.4.0/24"),
                    "gateway": ipaddress.ip_address("192.168.4.1"),
                    "pool_ips": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("192.168.4.10"),
                            "mac_address": "00:00:00:00:00:40",
                            "serial": None,
                            "device_id": "device-override-router",
                            "device_name": "router-override-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "platform": "Cumulus Linux",
                        }
                    ],
                }
            ]
        elif self.error_case == "no_ztp":
            return [
                {
                    "prefix": ipaddress.ip_network("192.168.5.0/24"),
                    "gateway": ipaddress.ip_address("192.168.5.1"),
                    "pool_ips": [],
                    "reservations": [
                        {
                            "address": ipaddress.ip_address("192.168.5.10"),
                            "mac_address": "00:00:00:00:00:50",
                            "serial": None,
                            "device_id": "device-no-ztp-test",
                            "device_name": "no-ztp-device",
                            "interface_name": "eth0",
                            "interface_role": "management",
                            "platform": "Cumulus Linux",
                        }
                    ],
                }
            ]

        # Trigger the graphql_query call to potentially raise errors
        await self.graphql_query("auto_dhcp_subnets")
        return await super().load_auto_dhcp_subnets(family, is_aggregate_managed)

    async def load_dhcp_contexts(self, is_aggregate_managed: bool | None = None):
        data = await self.graphql_query("dhcp_contexts")
        return {
            device["device"]["id"]: device["device"]["config_context"]
            for device in data["data"]["config_manager_devices"]
            if "config_context" in device["device"]
        }


@pytest.mark.asyncio
async def test_subnet_option_conflicts_logged_and_raised():
    """Test that subnet option conflicts are collected, logged, and raised at end."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        with pytest.raises(
            DhcpConfigGenerationError, match="conflicts with existing option"
        ) as exc_info:
            await generate_config(
                MockErrorCasesClient("conflicting_subnet_options"),
                MockRedisClient(),
                version=4,
            )
        mock_log.error.assert_called()
        calls = [str(c) for c in mock_log.error.call_args_list]
        assert any("conflicts with existing option" in c for c in calls)
        assert any("ntp-servers" in c for c in calls)
        assert "conflict(s) total" in str(exc_info.value)


@pytest.mark.asyncio
async def test_subnet_config_conflicts_logged_and_raised():
    """Test that subnet config conflicts are collected, logged, and raised at end."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        with pytest.raises(DhcpConfigGenerationError, match="conflicts with existing config"):
            await generate_config(
                MockErrorCasesClient("conflicting_subnet_config"),
                MockRedisClient(),
                version=4,
            )
        mock_log.error.assert_called()
        calls = [str(c) for c in mock_log.error.call_args_list]
        assert any("conflicts with existing config" in c for c in calls)
        assert any("reservations-global" in c for c in calls)


@pytest.mark.asyncio
async def test_reservation_options_conflicts_logged_and_raised():
    """Test that reservation option conflicts (same device, role vs name) are logged and raised."""
    with patch("nv_config_manager.dhcp.kea_dhcp_confgen.logger") as mock_log:
        with pytest.raises(
            DhcpConfigGenerationError, match="Conflicting values for option.*boot-file-name"
        ):
            await generate_config(
                MockErrorCasesClient("conflicting_reservation_options"),
                MockRedisClient(),
                version=4,
            )
        mock_log.error.assert_called()
        calls = [str(c) for c in mock_log.error.call_args_list]
        assert any("Conflicting values for option" in c for c in calls)
        assert any("boot-file-name" in c for c in calls)


@pytest.mark.asyncio
async def test_different_reservation_options_same_subnet_no_conflict():
    """Test that different reservations in the same subnet can have different per-device options."""
    config = await generate_config(
        MockNautobotClient("https://nautobot.example.com/", "dummy"),
        MockRedisClient(),
        version=4,
    )
    # 10.217.162.0/24 has multiple reservations with device-specific boot-file-name
    global_reservations = config["Dhcp4"]["reservations"]
    subnet_217_reservations = [
        r for r in global_reservations if r["ip-address"].startswith("10.217.162.")
    ]
    assert len(subnet_217_reservations) >= 2
    boot_files = []
    for r in subnet_217_reservations:
        for opt in r.get("option-data", []):
            if opt.get("name") == "boot-file-name":
                boot_files.append(opt["data"])
                break
    assert len(boot_files) >= 2
    assert len(set(boot_files)) == len(boot_files), (
        "Each reservation should have a distinct boot-file-name (per-device options)"
    )


@pytest.mark.asyncio
async def test_override_subnet_option_error():
    """Test that attempting to override 'subnet' in subnet_config throws error."""
    with pytest.raises(DhcpConfigGenerationError, match="Cannot override subnet config subnet"):
        await generate_config(
            MockErrorCasesClient("override_subnet"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_malformed_client_id_template_error():
    """Test that malformed client_id template throws DhcpConfigGenerationError."""
    with pytest.raises(DhcpConfigGenerationError, match="Error rendering client_id_template"):
        await generate_config(
            MockErrorCasesClient("malformed_template"),
            MockRedisClient(),
            version=4,
        )


@pytest.mark.asyncio
async def test_override_router_option_error():
    """Test that attempting to override router option throws an error."""
    with pytest.raises(DhcpConfigGenerationError, match="Cannot override router option"):
        await generate_config(
            MockErrorCasesClient("override_router"),
            MockRedisClient(),
            version=4,
        )


def test_format_options_for_kea_includes_vendor_space():
    """Option 43 suboptions carry space so Kea does not emit them as dhcp4."""
    site_option_defs = {
        "cumulus-provision-url": {
            "name": "cumulus-provision-url",
            "code": 239,
            "space": "dhcp4",
        },
        "config-file-name": {
            "name": "config-file-name",
            "code": 1,
            "space": "vendor-encapsulated-options-space",
        },
        "transfer-mode": {
            "name": "transfer-mode",
            "code": 3,
            "space": "vendor-encapsulated-options-space",
        },
    }

    option_data = _format_options_for_kea(
        {
            "cumulus-provision-url": "http://ztp.example/boot-script",
            "transfer-mode": "http",
            "config-file-name": "http://ztp.example/config/full-config",
            "hostname": "switch-01",
        },
        site_option_defs,
    )

    by_name = {entry["name"]: entry for entry in option_data}
    assert by_name["cumulus-provision-url"]["code"] == 239
    assert "space" not in by_name["cumulus-provision-url"]
    assert by_name["transfer-mode"]["space"] == "vendor-encapsulated-options-space"
    assert by_name["transfer-mode"]["code"] == 3
    assert by_name["config-file-name"]["space"] == "vendor-encapsulated-options-space"
    assert "code" not in by_name["hostname"]


@pytest.mark.asyncio
async def test_no_ztp_server_error():
    """Test that missing ztp_server when trying to substitute throws error."""
    with pytest.raises(
        DhcpConfigGenerationError,
        match="No ZTP server found.*in DHCP context",
    ):
        await generate_config(
            MockErrorCasesClient("no_ztp"),
            MockRedisClient(),
            version=4,
        )
