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
import json
import logging
from typing import Any, cast

from nv_config_manager_dcim.errors import DCIMInvalidDataError

from nv_config_manager_dcim_nautobot_2x.queries import load_graphql_query

logger = logging.getLogger(__name__)

# Match other Nautobot GraphQL inventory pages. DHCP still needs a full snapshot;
# paging only splits the Nautobot request so a large cell cannot 504 one query.
GRAPHQL_PAGE_SIZE = 100
# Nautobot exposes no cursor pagination, so pages are positional: a row deleted
# mid-snapshot shifts every later row left and the next page would start past an
# unread one. Re-reading the tail of each page absorbs a shift of up to this many
# rows; callers collapse the repeats.
GRAPHQL_PAGE_OVERLAP = 10


class DHCPDataError(DCIMInvalidDataError):
    """Nautobot returned invalid data required for DHCP configuration."""


def _row_signature(row: dict[str, Any]) -> str:
    """Identify a row so repeated pages can be told from new ones.

    Every DHCP query selects Nautobot's unique ``id``; ``dhcp_contexts`` carries
    it on the wrapped device. Serializing the whole row is the fallback for
    fixtures and future selections that omit it.
    """
    identity = row.get("id")
    if identity is None:
        device = row.get("device")
        identity = device.get("id") if isinstance(device, dict) else None
    if identity is not None:
        return str(identity)
    return json.dumps(row, sort_keys=True, default=str)


def _dedupe_keep_first(items: list[Any], key: str) -> list[dict[str, Any]]:
    """Drop later rows that repeat ``key`` (offset paging can overlap on a moving set).

    DHCP IP records prefer Nautobot's unique ``id``. Fixtures that omit ``id``
    still collapse overlapping pages by ``address``.
    """
    seen: set[Any] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        value = item.get(key) or item.get("address")
        if value in seen:
            continue
        seen.add(value)
        unique.append(item)
    return unique


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

    async def _iter_graphql_pages(
        self,
        query: str,
        result_key: str,
        variables: dict[str, Any] | None = None,
        page_size: int = GRAPHQL_PAGE_SIZE,
        overlap: int = GRAPHQL_PAGE_OVERLAP,
    ) -> list[Any]:
        """Fetch every page of a Nautobot GraphQL list field.

        Consecutive requests re-read the last ``overlap`` rows, so callers must
        collapse the repeated rows.

        Stops on an empty or short page. Raises when a full page adds no rows
        the page before it did not already have, which means the server (or a
        mock) is ignoring limit/offset and would otherwise page forever.
        """
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}")
        if overlap < 0:
            raise ValueError(f"overlap must be >= 0, got {overlap}")
        step = page_size - min(overlap, page_size - 1)
        collected: list[Any] = []
        extra = dict(variables or {})
        offset = 0
        seen_signatures: set[str] = set()
        while True:
            page_vars = {**extra, "limit": page_size, "offset": offset}
            rsp = await self.graphql_query(query, page_vars)
            data = rsp.get("data")
            if not isinstance(data, dict) or not isinstance(data.get(result_key), list):
                raise DHCPDataError(f"Nautobot returned invalid {result_key} data")
            page = data[result_key]
            if not page:
                break
            if any(not isinstance(item, dict) for item in page):
                raise DHCPDataError(f"Nautobot returned invalid {result_key} data")
            collected.extend(page)
            if len(page) < page_size:
                break
            # Overlapping pages repeat rows on purpose, but a full page that
            # adds none means the server served rows we already have for a new
            # offset. Compare against every page so far, not just the one
            # before: a server cycling between two pages would otherwise look
            # like progress forever. Only full pages qualify, since a short
            # final page can legitimately fall inside the previous overlap.
            signatures = {_row_signature(row) for row in page}
            if signatures <= seen_signatures:
                raise DHCPDataError(
                    f"Nautobot returned no new {result_key} rows at offset {offset}, "
                    "so it is ignoring limit/offset"
                )
            seen_signatures |= signatures
            logger.info(
                "Fetched %d %s at offset %d (%d fetched)",
                len(page),
                result_key,
                offset,
                len(collected),
            )
            offset += step
        return collected

    async def _load_stable_pages(
        self,
        query: str,
        result_key: str,
        variables: dict[str, Any] | None = None,
        page_size: int = GRAPHQL_PAGE_SIZE,
        overlap: int = GRAPHQL_PAGE_OVERLAP,
    ) -> list[Any]:
        """Page a list twice and keep the first walk only if unique ids match.

        Nautobot GraphQL has no snapshot, so a tear can still skip rows after
        overlap. A second full walk that disagrees means the table moved (or
        tiled wrong); raising here skips Redis so Kea keeps the last good config.
        """
        first = await self._iter_graphql_pages(
            query,
            result_key,
            variables=variables,
            page_size=page_size,
            overlap=overlap,
        )
        second = await self._iter_graphql_pages(
            query,
            result_key,
            variables=variables,
            page_size=page_size,
            overlap=overlap,
        )
        first_ids = {_row_signature(row) for row in first}
        second_ids = {_row_signature(row) for row in second}
        if first_ids != second_ids:
            raise DHCPDataError(
                f"Nautobot {result_key} changed during paging "
                f"({len(first_ids)} then {len(second_ids)} unique rows); "
                "not publishing this cycle"
            )
        return first

    async def load_dhcp_contexts(
        self,
        is_aggregate_managed: bool | None = None,
        page_size: int = GRAPHQL_PAGE_SIZE,
        overlap: int = GRAPHQL_PAGE_OVERLAP,
    ) -> dict[str, dict[str, object]]:
        """Compatibility hook returning DHCP contexts from Nautobot GraphQL."""
        entries = await self._load_stable_pages(
            load_graphql_query("provider/dhcp.graphql", "dhcp_contexts"),
            "config_manager_devices",
            variables={"is_aggregate_managed": is_aggregate_managed},
            page_size=page_size,
            overlap=overlap,
        )
        # Keying by device id is what collapses the rows repeated across pages.
        contexts: dict[str, dict[str, object]] = {}
        for entry in entries:
            device = entry.get("device") if isinstance(entry, dict) else None
            if not device:
                continue
            contexts[device["id"]] = device["config_context"]
        return contexts

    async def load_static_data(self) -> list[dict[str, object]]:
        """Compatibility hook returning static DHCP contexts."""
        response = await self.graphql_query(
            load_graphql_query("provider/dhcp.graphql", "static_data")
        )
        return [entry["data"] for entry in response["data"].get("config_contexts", [])]

    async def load_auto_dhcp_subnets(
        self,
        family: int = 4,
        is_aggregate_managed: bool | None = None,
        page_size: int = GRAPHQL_PAGE_SIZE,
        overlap: int = GRAPHQL_PAGE_OVERLAP,
    ) -> list[dict[str, object]]:
        """Compatibility hook returning normalized automatic DHCP subnet data."""
        prefixes = _dedupe_keep_first(
            await self._load_stable_pages(
                load_graphql_query("provider/dhcp.graphql", "auto_dhcp_subnets_prefixes"),
                "prefixes",
                page_size=page_size,
                overlap=overlap,
            ),
            "id",
        )
        if not prefixes:
            return []

        all_pool_ips = _dedupe_keep_first(
            await self._load_stable_pages(
                load_graphql_query("provider/dhcp.graphql", "auto_dhcp_subnets_pool_ips"),
                "pool_ips",
                page_size=page_size,
                overlap=overlap,
            ),
            "id",
        )
        all_reserved_ips = _dedupe_keep_first(
            await self._load_stable_pages(
                load_graphql_query("provider/dhcp.graphql", "auto_dhcp_subnets_reserved_ips"),
                "reserved_ips",
                page_size=page_size,
                overlap=overlap,
            ),
            "id",
        )
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
