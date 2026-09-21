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
"""Tests for optimized Nautobot DHCP data retrieval."""

import ipaddress
from typing import Any

import pytest
from nv_config_manager_dcim_nautobot_2x.dhcp import DHCPDataError
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient


class _DHCPContextClient(NautobotDCIMClient):
    """Return controlled responses for the two DHCP context query stages."""

    def __init__(self, devices: list[dict[str, Any]]) -> None:
        super().__init__("https://nautobot.example.com", "token")
        self.devices = devices
        self.calls: list[tuple[str | None, dict[str, Any]]] = []

    async def graphql_query(self, query, variables=None):  # noqa: ANN001
        self.calls.append((query.operation_name, variables or {}))
        if query.operation_name == "dhcp_context_device_ids":
            return {
                "data": {
                    "config_manager_devices": [
                        {"device": {"id": device["id"]}} for device in self.devices
                    ]
                }
            }
        if query.operation_name == "dhcp_device_contexts":
            requested_ids = set(variables["ids"])
            return {
                "data": {
                    "devices": [device for device in self.devices if device["id"] in requested_ids]
                }
            }
        raise AssertionError(f"Unexpected operation {query.operation_name}")


@pytest.mark.asyncio
async def test_load_dhcp_contexts_fetches_eligible_ids_then_root_devices() -> None:
    """Device contexts use Nautobot's optimized root device resolver."""
    client = _DHCPContextClient(
        [
            {"id": "device-1", "config_context": {"dhcp": {"enabled": True}}},
            {"id": "device-2", "config_context": {}},
        ]
    )

    contexts = await client.load_dhcp_contexts(is_aggregate_managed=False)

    assert contexts == {
        "device-1": {"dhcp": {"enabled": True}},
        "device-2": {},
    }
    assert client.calls == [
        ("dhcp_context_device_ids", {"is_aggregate_managed": False}),
        ("dhcp_device_contexts", {"ids": ["device-1", "device-2"]}),
    ]


@pytest.mark.asyncio
async def test_load_dhcp_contexts_avoids_second_query_when_no_devices() -> None:
    """An empty eligible set does not issue an unnecessary device query."""
    client = _DHCPContextClient([])

    assert await client.load_dhcp_contexts() == {}
    assert client.calls == [
        ("dhcp_context_device_ids", {"is_aggregate_managed": None}),
    ]


@pytest.mark.asyncio
async def test_load_dhcp_contexts_rejects_incomplete_device_results() -> None:
    """Missing contexts cannot silently produce an incomplete DHCP configuration."""

    class _MissingDeviceClient(_DHCPContextClient):
        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            response = await super().graphql_query(query, variables)
            if query.operation_name == "dhcp_device_contexts":
                response["data"]["devices"].pop()
            return response

    client = _MissingDeviceClient(
        [
            {"id": "device-1", "config_context": {}},
            {"id": "device-2", "config_context": {}},
        ]
    )

    with pytest.raises(DHCPDataError, match="1 of 2 eligible devices"):
        await client.load_dhcp_contexts()


class _DHCPSubnetClient(NautobotDCIMClient):
    """Return controlled responses for subnet and gateway query stages."""

    def __init__(self, gateways: list[dict[str, Any]]) -> None:
        super().__init__("https://nautobot.example.com", "token")
        self.gateways = gateways
        self.calls: list[tuple[str | None, dict[str, Any]]] = []

    async def graphql_query(self, query, variables=None):  # noqa: ANN001
        self.calls.append((query.operation_name, variables or {}))
        if query.operation_name == "auto_dhcp_subnets":
            return {
                "data": {
                    "prefixes": [
                        {"id": "prefix-v4", "prefix": "192.0.2.0/24", "ip_version": 4},
                        {"id": "prefix-v6", "prefix": "2001:db8::/64", "ip_version": 6},
                    ],
                    "pool_ips": [],
                    "reserved_ips": [],
                }
            }
        if query.operation_name == "dhcp_subnet_gateways":
            return {"data": {"prefixes": self.gateways}}
        raise AssertionError(f"Unexpected operation {query.operation_name}")


@pytest.mark.asyncio
async def test_load_auto_subnets_joins_separately_queried_gateways() -> None:
    """Gateway relationships are isolated and rejoined before normalization."""
    client = _DHCPSubnetClient(
        [
            {
                "id": "prefix-v4",
                "rel_prefix_to_gateway": {"address": "192.0.2.254/24"},
            }
        ]
    )

    subnets = await client.load_auto_dhcp_subnets(family=4)

    assert len(subnets) == 1
    assert subnets[0]["gateway"] == ipaddress.ip_address("192.0.2.254")
    assert client.calls == [
        ("auto_dhcp_subnets", {}),
        ("dhcp_subnet_gateways", {"ids": ["prefix-v4"]}),
    ]


@pytest.mark.asyncio
async def test_load_auto_subnets_rejects_incomplete_gateway_results() -> None:
    """A partial second-stage response cannot silently use fallback gateways."""
    client = _DHCPSubnetClient([])

    with pytest.raises(DHCPDataError, match="0 of 1 DHCP prefixes"):
        await client.load_auto_dhcp_subnets(family=4)
