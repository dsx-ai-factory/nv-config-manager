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
"""Nautobot implementation details for provider-owned DHCP operations."""

from __future__ import annotations

import ipaddress
from typing import Any, cast

from nv_config_manager_dcim.errors import DCIMInvalidDataError

from nv_config_manager_dcim_nautobot_2x.queries import load_graphql_query


class DHCPDataError(DCIMInvalidDataError):
    """Nautobot returned invalid data required for DHCP configuration."""


def _get_gateway_ip(
    prefix_entry: dict[str, Any], prefix: ipaddress.IPv4Network | ipaddress.IPv6Network
) -> str:
    """Extract a gateway IP or derive the first usable address."""
    gateway_address = (
        prefix_entry["rel_prefix_to_gateway"].get("address")
        if prefix_entry["rel_prefix_to_gateway"]
        else None
    )
    if gateway_address:
        gateway_ip = str(ipaddress.ip_interface(gateway_address).ip)
        if ipaddress.ip_address(gateway_ip) not in prefix:
            raise DHCPDataError(f"Gateway {gateway_ip} is not within subnet {prefix}")
        return gateway_ip
    if prefix.prefixlen in (31, 127):
        return str(prefix.network_address)
    return str(prefix.network_address + 1)


def _build_interface_entry(iface: dict[str, Any]) -> dict[str, Any]:
    """Build common option-candidate or reservation fields."""
    return {
        "mac_address": iface.get("mac_address"),
        "serial": iface["device"].get("serial"),
        "platform": (iface["device"]["platform"]["name"] if iface["device"]["platform"] else None),
        "interface_name": iface["name"],
        "interface_role": iface["role"]["name"] if iface["role"] else None,
        "device_name": iface["device"]["name"],
        "device_id": iface["device"]["id"],
    }


def _passes_ztp_aggregate_filter(
    status: dict[str, Any] | None, is_aggregate_managed: bool | None
) -> bool:
    """Return whether a device passes ZTP and aggregate-management filters."""
    if not status:
        return True
    if not status.get("ztp_enabled", True):
        return False
    return cast(bool, status.get("is_aggregate_managed", False) == is_aggregate_managed)


def _ip_matches_prefix(ip: dict[str, Any], prefix_entry_id: str, family: int) -> bool:
    """Return whether an address belongs to the requested prefix and family."""
    return cast(bool, ip["ip_version"] == family and ip["parent"]["id"] == prefix_entry_id)


def _has_identifier(iface: dict[str, Any]) -> bool:
    """Return whether an interface has a DHCP reservation identifier."""
    return bool(iface.get("mac_address") or iface["device"].get("serial"))


def _try_build_option_candidate(
    pool_ip: dict[str, Any], is_aggregate_managed: bool | None
) -> dict[str, Any] | None:
    """Build an eligible option candidate from a pool address."""
    interfaces = pool_ip.get("interfaces")
    if not interfaces or len(interfaces) > 1:
        return None
    iface = interfaces[0]
    if not _passes_ztp_aggregate_filter(
        iface["device"].get("configmanagerdevicestatus"), is_aggregate_managed
    ):
        return None
    if not _has_identifier(iface):
        return None
    return {
        "address": ipaddress.ip_interface(pool_ip["address"]).ip,
        **_build_interface_entry(iface),
    }


def _get_pool_ips_and_candidates(
    all_pool_ips: list[dict[str, Any]],
    prefix_entry: dict[str, Any],
    family: int,
    is_aggregate_managed: bool | None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Build pool addresses and option candidates for a prefix."""
    pool_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    option_candidates: list[dict[str, Any]] = []
    for pool_ip in all_pool_ips:
        if not _ip_matches_prefix(pool_ip, prefix_entry["id"], family):
            continue
        pool_ips.append(ipaddress.ip_interface(pool_ip["address"]).ip)
        candidate = _try_build_option_candidate(pool_ip, is_aggregate_managed)
        if candidate:
            option_candidates.append(candidate)
    return pool_ips, option_candidates


def _validate_reserved_ip_interfaces(reserved_ip: dict[str, Any]) -> dict[str, Any]:
    """Return the only interface assigned to a reserved address."""
    interfaces = reserved_ip.get("interfaces")
    if not interfaces:
        raise DHCPDataError(f"Reserved IP {reserved_ip['address']} has no interfaces assigned")
    if len(interfaces) > 1:
        raise DHCPDataError(
            f"Reserved IP {reserved_ip['address']} has multiple interfaces assigned"
        )
    return cast(dict[str, Any], interfaces[0])


def _get_reservations_for_prefix(
    all_reserved_ips: list[dict[str, Any]],
    prefix_entry: dict[str, Any],
    family: int,
    is_aggregate_managed: bool | None,
) -> list[dict[str, Any]]:
    """Build provider-normalized reservations for a prefix."""
    reservations: list[dict[str, Any]] = []
    for reserved_ip in all_reserved_ips:
        if not _ip_matches_prefix(reserved_ip, prefix_entry["id"], family):
            continue
        iface = _validate_reserved_ip_interfaces(reserved_ip)
        status = iface["device"].get("configmanagerdevicestatus")
        if not _passes_ztp_aggregate_filter(status, is_aggregate_managed):
            continue
        if not _has_identifier(iface):
            raise DHCPDataError(
                f"Interface {iface['name']} on IP {reserved_ip['address']} has no MAC address "
                "or serial number"
            )
        reservations.append(
            {
                "address": ipaddress.ip_interface(reserved_ip["address"]).ip,
                **_build_interface_entry(iface),
                "device_status": iface["device"]["status"]["name"],
            }
        )
    return reservations


class NautobotDHCPOperations:
    """Mixin implementing the built-in provider's normalized DHCP operations."""

    async def get_dhcp_site_options(self) -> dict[str, object]:
        """Return site-level DHCP options from the selected DCIM."""
        return await self.load_site_dhcp_options()

    async def get_dhcp_contexts(
        self, is_aggregate_managed: bool | None = None
    ) -> dict[str, dict[str, object]]:
        """Return eligible managed-device DHCP contexts."""
        return await self.load_dhcp_contexts(is_aggregate_managed)

    async def get_dhcp_static_data(self) -> list[dict[str, object]]:
        """Return static DHCP configuration data."""
        return await self.load_static_data()

    async def get_dhcp_auto_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ) -> list[dict[str, object]]:
        """Return automatic DHCP subnets and reservations."""
        return await self.load_auto_dhcp_subnets(family, is_aggregate_managed)

    async def load_site_dhcp_options(self) -> dict[str, object]:
        """Compatibility hook implemented by the built-in Nautobot provider."""
        response = await self.graphql_query(
            load_graphql_query("provider/dhcp.graphql", "site_dhcp_options")
        )
        contexts = response["data"].get("config_contexts", [])
        return contexts[0].get("data", {}) if contexts else {}

    async def load_dhcp_contexts(
        self, is_aggregate_managed: bool | None = None
    ) -> dict[str, dict[str, object]]:
        """Compatibility hook returning DHCP contexts from Nautobot GraphQL."""
        response = await self.graphql_query(
            load_graphql_query("provider/dhcp.graphql", "dhcp_contexts"),
            {"is_aggregate_managed": is_aggregate_managed},
        )
        return {
            entry["device"]["id"]: entry["device"]["config_context"]
            for entry in response["data"]["config_manager_devices"]
        }

    async def load_static_data(self) -> list[dict[str, object]]:
        """Compatibility hook returning static DHCP contexts."""
        response = await self.graphql_query(
            load_graphql_query("provider/dhcp.graphql", "static_data")
        )
        return [entry["data"] for entry in response["data"].get("config_contexts", [])]

    async def load_auto_dhcp_subnets(
        self, family: int = 4, is_aggregate_managed: bool | None = None
    ) -> list[dict[str, object]]:
        """Compatibility hook returning normalized automatic DHCP subnet data."""
        response = await self.graphql_query(
            load_graphql_query("provider/dhcp.graphql", "auto_dhcp_subnets")
        )
        prefixes = response["data"].get("prefixes", [])
        if not prefixes:
            return []
        all_pool_ips = response["data"].get("pool_ips", [])
        all_reserved_ips = response["data"].get("reserved_ips", [])
        subnets: list[dict[str, object]] = []
        for prefix_entry in prefixes:
            if prefix_entry["ip_version"] != family:
                continue
            prefix = ipaddress.ip_network(prefix_entry["prefix"])
            gateway_ip = _get_gateway_ip(prefix_entry, prefix)
            pool_ips, option_candidates = _get_pool_ips_and_candidates(
                all_pool_ips, prefix_entry, family, is_aggregate_managed
            )
            reservations = _get_reservations_for_prefix(
                all_reserved_ips, prefix_entry, family, is_aggregate_managed
            )
            subnets.append(
                {
                    "prefix": prefix,
                    "gateway": ipaddress.ip_address(gateway_ip),
                    "id": prefix_entry["id"],
                    "pool_ips": pool_ips,
                    "option_candidates": option_candidates,
                    "reservations": reservations,
                }
            )
        return subnets
