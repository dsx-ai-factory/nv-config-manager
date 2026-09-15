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
"""KEA DHCP Config Generation."""

from __future__ import annotations

import ipaddress
import os
import random
import time as _time
from typing import Any, cast

import macaddress
import netaddr
from jinja2 import BaseLoader, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment, SecurityError

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import DCIMClient
from nv_config_manager.dhcp.metrics import (
    DHCP_CONFIG_GENERATION_DURATION,
    DHCP_CONFIG_GENERATION_ERRORS,
)
from nv_config_manager.dhcp.redis import RedisClient

logger = get_logger(__name__, category=LogCategory.DHCP_DATA)


class DhcpConfigGenerationError(Exception):
    """DHCP Configuration Generation Error."""


_DHCP4_OPTION_SPACE = "dhcp4"
SiteOptionDefs = dict[str, dict[str, Any]]


def _site_option_defs(site_dhcp_options: list[dict[str, Any]]) -> SiteOptionDefs:
    """Index site option-def entries by option name."""
    return {opt["name"]: opt for opt in site_dhcp_options}


def _normalize_reservation_id(reservation_id: str) -> str:
    try:
        return str(macaddress.MAC(reservation_id))
    except ValueError:
        return reservation_id.lower()


def _check_duplicate_identifier(
    reservation: dict[str, Any],
    reservation_ids: set[str],
) -> None:
    """Raise if reservation's identifier is already in use."""
    reservation_id = reservation.get("hw-address") or reservation.get("client-id")
    if not reservation_id:
        return
    normalized = _normalize_reservation_id(reservation_id)
    if normalized in reservation_ids:
        hostname = reservation.get("hostname", "unknown")
        raise DhcpConfigGenerationError(
            f"Duplicate DHCP reservation: device '{hostname}' (identifier: "
            f"{reservation_id}) has multiple reserved IPs with the same client-id or "
            "hw-address. Kea allows only one reservation per identifier. "
            "Use dhcp-pool only (not dhcp-reserve) for interfaces that don't need "
            "per-device options, or ensure each interface has a unique MAC address."
        )
    reservation_ids.add(normalized)


def _add_auto_reservations(
    auto_subnet_reservations: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
    reservation_ids: set[str],
    ip_to_reservation: dict[str, dict[str, Any]],
) -> None:
    """Add auto reservations and track identifiers/IPs."""
    for reservation in auto_subnet_reservations:
        _check_duplicate_identifier(reservation, reservation_ids)
        reservations.append(reservation)
        ip_to_reservation[reservation["ip-address"]] = reservation


def _is_ip_conflict(
    reservation: dict[str, Any],
    ip_to_reservation: dict[str, dict[str, Any]],
) -> bool:
    """True if reservation's IP already exists in ip_to_reservation."""
    ip_address = reservation.get("ip-address")
    return bool(ip_address and ip_address in ip_to_reservation)


def _add_or_skip_static_reservation(
    reservation: dict[str, Any],
    reservations: list[dict[str, Any]],
    reservation_ids: set[str],
) -> bool:
    """Add static reservation if no duplicate identifier. Returns True if added."""
    reservation_id = reservation.get("hw-address") or reservation.get("client-id")
    if reservation_id and _normalize_reservation_id(reservation_id) in reservation_ids:
        logger.warning(
            "Static reservation for %s is being overwritten by a generated reservation",
            reservation_id,
        )
        return False
    reservations.append(reservation)
    if reservation_id:
        reservation_ids.add(_normalize_reservation_id(reservation_id))
    return True


def _log_reservation_conflicts(conflicts: list[dict[str, Any]]) -> None:
    """Log all IP address conflicts."""
    for conflict in conflicts:
        auto_res = conflict["conflicting_reservation"]
        auto_res_id = auto_res.get("hw-address") or auto_res.get("client-id")
        logger.warning(
            "IP address conflict: automatic reservation for %s (%s) "
            "has the same IP %s as %s reservation for %s. "
            "Using automatic reservation.",
            auto_res["hostname"],
            auto_res_id,
            conflict["ip_address"],
            conflict["type"],
            conflict["reservation_id"],
        )


def _generate_reservations(
    static_data: list[dict[str, Any]],
    auto_subnet_reservations: list[dict[str, Any]] | None = None,
    version: int = 4,
) -> list[dict[str, Any]]:
    """Generate DHCP reservations from multiple sources with conflict detection."""

    reservations: list[dict[str, Any]] = []
    reservation_ids: set[str] = set()
    ip_to_reservation: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    if auto_subnet_reservations:
        _add_auto_reservations(
            auto_subnet_reservations, reservations, reservation_ids, ip_to_reservation
        )

    dhcp_key = f"Dhcp{version}"
    for config_context in static_data:
        if dhcp_key not in config_context:
            continue

        for reservation in config_context[dhcp_key].get("reservations", []):
            if _is_ip_conflict(reservation, ip_to_reservation):
                conflicts.append(
                    {
                        "type": "static",
                        "conflicting_reservation": ip_to_reservation[reservation["ip-address"]],
                        "conflicted_reservation": reservation,
                        "ip_address": reservation["ip-address"],
                        "reservation_id": reservation.get(
                            "hw-address", reservation.get("client-id", "unknown")
                        ),
                    }
                )
                continue
            _add_or_skip_static_reservation(reservation, reservations, reservation_ids)

    _log_reservation_conflicts(conflicts)
    return reservations


def _generate_subnets(
    static_data: list[dict[str, Any]],
    version: int = 4,
) -> list[dict[str, Any]]:
    # Generate Subnets
    # Merge in any static subnets, subnet definitions in the static file
    # will overwrite anything auto-generated
    # NOTE: this behavior differs from reservations,
    # there are use cases for merging subnets
    # but no valid use cases for merging reservations.
    subnet_map = {}
    dhcp_key = f"Dhcp{version}"
    for config_context in static_data:
        if dhcp_key not in config_context:
            continue
        static_subnets = config_context[dhcp_key].get(f"subnet{version}", [])
        subnet_map.update({entry["subnet"]: entry for entry in static_subnets})

    return sorted(subnet_map.values(), key=lambda x: x["subnet"])


def _filter_pool_reservation_overlaps(subnet_data: dict[str, Any]) -> None:
    """When an IP has both dhcp-pool and dhcp-reserve tags, ignore dhcp-reserve and log a warning."""
    pool_addresses = {str(ip) for ip in subnet_data.get("pool_ips", [])}
    reservations = subnet_data.get("reservations", [])
    filtered = []
    for reservation in reservations:
        if str(reservation["address"]) in pool_addresses:
            logger.warning(
                "IP address %s in subnet %s has both dhcp-pool and dhcp-reserve tags; "
                "ignoring dhcp-reserve and treating as pool only",
                reservation["address"],
                subnet_data["prefix"],
            )
        else:
            filtered.append(reservation)
    subnet_data["reservations"] = filtered


def _process_subnet_reservations(
    subnet_data: dict[str, Any],
    dhcp_contexts: dict[str, dict[str, Any]],
    site_option_codes: SiteOptionDefs,
    options: dict[str, Any],
    config: dict[str, Any],
    reservations: list[dict[str, Any]],
    conflicts: list[str],
) -> None:
    """Process reservations, append to reservations list, merge into options/config."""
    for reservation in subnet_data.get("reservations", []):
        result = _get_subnet_options(reservation, dhcp_contexts, conflicts)
        if result is None:
            continue
        subnet_options, reservation_options, subnet_config = result
        _merge_options_and_config(
            options,
            config,
            subnet_options,
            subnet_config,
            subnet_data["prefix"],
            reservation,
            "reservation",
            conflicts,
        )
        reservation_data = {
            "ip-address": str(reservation["address"]),
            "hostname": reservation["device_name"],
            "option-data": _format_options_for_kea(reservation_options, site_option_codes),
        }
        _add_reservation_identifier(reservation_data, reservation, dhcp_contexts)
        reservations.append(reservation_data)


def _process_option_candidates(
    subnet_data: dict[str, Any],
    dhcp_contexts: dict[str, dict[str, Any]],
    site_option_codes: SiteOptionDefs,
    options: dict[str, Any],
    config: dict[str, Any],
    conflicts: list[str],
) -> list[dict[str, Any]]:
    """Process option_candidates into subnet-level reservations."""
    reserved_addresses = {str(r["address"]) for r in subnet_data.get("reservations", [])}
    result = []
    for candidate in subnet_data.get("option_candidates", []):
        if str(candidate["address"]) in reserved_addresses:
            continue
        options_result = _get_subnet_options(candidate, dhcp_contexts, conflicts)
        if options_result is None:
            continue
        subnet_options, reservation_options, subnet_cfg = options_result
        _merge_options_and_config(
            options,
            config,
            subnet_options,
            subnet_cfg,
            subnet_data["prefix"],
            candidate,
            "option_candidate",
            conflicts,
        )
        if not reservation_options:
            continue
        entry = {
            "hostname": candidate["device_name"],
            "option-data": _format_options_for_kea(reservation_options, site_option_codes),
        }
        _add_reservation_identifier(entry, candidate, dhcp_contexts)
        result.append(entry)
    return result


def _build_subnet_config(
    subnet_data: dict[str, Any],
    options: dict[str, Any],
    site_option_codes: SiteOptionDefs,
    subnet_option_reservations: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build subnet config with option-data, pools, and optional reservations."""
    prefix = subnet_data["prefix"]
    gateway_ip = subnet_data["gateway"]
    options["routers"] = str(gateway_ip)
    option_data = _format_options_for_kea(dict(options), site_option_codes)
    pools = _generate_dhcp_pool_ranges(subnet_data["pool_ips"], gateway_ip)

    subnet_config: dict[str, Any] = {
        "subnet": str(prefix),
        "option-data": option_data,
        "pools": pools,
    }
    if subnet_option_reservations:
        subnet_config["reservations"] = subnet_option_reservations
        subnet_config["reservations-in-subnet"] = True

    for opt, value in config.items():
        if opt in subnet_config and subnet_config[opt] != value:
            raise DhcpConfigGenerationError(f"Cannot override subnet config {opt} with {value}")
        subnet_config[opt] = value
    return subnet_config


async def _generate_automatic_dhcp_subnets_and_reservations(
    dcim_client: DCIMClient,
    dhcp_contexts: dict[str, dict[str, Any]],
    version: int = 4,
    site_dhcp_options: list[dict[str, Any]] | None = None,
    is_aggregate_managed: bool | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate automatic DHCP subnet configurations and reservations from Nautobot data."""
    site_dhcp_options = site_dhcp_options or []
    dhcp_subnets = await dcim_client.get_dhcp_auto_subnets(
        family=version, is_aggregate_managed=is_aggregate_managed
    )

    subnet_configs: list[dict[str, Any]] = []
    reservations: list[dict[str, Any]] = []
    conflicts: list[str] = []

    for subnet_data in dhcp_subnets:
        options: dict[str, Any] = {}
        config: dict[str, Any] = {}
        site_option_codes = _site_option_defs(site_dhcp_options)

        _filter_pool_reservation_overlaps(subnet_data)
        _process_subnet_reservations(
            subnet_data, dhcp_contexts, site_option_codes, options, config, reservations, conflicts
        )
        subnet_option_reservations = _process_option_candidates(
            subnet_data, dhcp_contexts, site_option_codes, options, config, conflicts
        )
        subnet_config = _build_subnet_config(
            subnet_data, options, site_option_codes, subnet_option_reservations, config
        )
        subnet_configs.append(subnet_config)

    if conflicts:
        for msg in conflicts:
            logger.error(msg)
        raise DhcpConfigGenerationError(
            "\n".join(conflicts) + f"\n({len(conflicts)} conflict(s) total)"
        )

    return subnet_configs, reservations


def _get_client_id(reservation: dict[str, Any], device_context: dict[str, Any]) -> str:
    """Get the client ID for a reservation using Jinja2 template from context."""
    serial = reservation.get("serial")

    if not serial:
        DHCP_CONFIG_GENERATION_ERRORS.labels(error_type="missing_serial", ip_version="").inc()
        raise DhcpConfigGenerationError(
            f"Serial number is required for client ID generation for device "
            f"{reservation['device_name']}"
        )

    client_id_template = device_context.get("dhcp", {}).get("options", {}).get("client_id_template")

    if client_id_template:
        env = SandboxedEnvironment(loader=BaseLoader(), autoescape=True)

        def hex_filter(value: str) -> str:
            """Convert string to colon-separated hex format."""
            hexchars = [f"{ord(c):02x}" for c in value]
            return ":".join(hexchars)

        env.filters["hex"] = hex_filter

        context = {
            "serial": serial,
        }

        try:
            template = env.from_string(client_id_template)
            return str(template.render(context))
        except SecurityError as e:
            DHCP_CONFIG_GENERATION_ERRORS.labels(
                error_type="template_security", ip_version=""
            ).inc()
            raise DhcpConfigGenerationError(
                f"Unsafe template expression in client_id_template for "
                f"{reservation['device_name']}: {e}"
            ) from e
        except (UndefinedError, TemplateSyntaxError) as e:
            DHCP_CONFIG_GENERATION_ERRORS.labels(error_type="template_error", ip_version="").inc()
            raise DhcpConfigGenerationError(
                f"Error rendering client_id_template for {reservation['device_name']}: {str(e)}"
            ) from e

    return f"'{serial}'"


def _merge_options_and_config(
    options: dict[str, Any],
    config: dict[str, Any],
    subnet_options: dict[str, Any],
    subnet_config: dict[str, Any],
    prefix: Any,
    entry: dict[str, Any],
    label: str,
    conflicts: list[str],
) -> None:
    """Merge subnet_options and subnet_config, appending conflict messages to conflicts."""
    for opt, value in subnet_options.items():
        if opt in options and options[opt] != value:
            msg = (
                f"Subnet {prefix} {label} for {entry['address']} "
                f"conflicts with existing option {opt}: {options[opt]} != {value}"
            )
            conflicts.append(msg)
        else:
            options[opt] = value
    for opt, value in subnet_config.items():
        if opt in config and config[opt] != value:
            msg = (
                f"Subnet {prefix} {label} for {entry['address']} "
                f"conflicts with existing config {opt}: {config[opt]} != {value}"
            )
            conflicts.append(msg)
        else:
            config[opt] = value


def _format_options_for_kea(
    reservation_options: dict[str, str],
    site_option_codes: SiteOptionDefs,
) -> list[dict[str, Any]]:
    """Convert reservation_options dict to Kea option-data format."""
    option_data: list[dict[str, Any]] = []
    for opt, value in reservation_options.items():
        entry: dict[str, Any] = {"name": opt, "data": value}
        option_def = site_option_codes.get(opt)
        if option_def is None:
            option_data.append(entry)
            continue
        if "code" in option_def:
            entry["code"] = option_def["code"]
        space = option_def.get("space")
        if space and space != _DHCP4_OPTION_SPACE:
            entry["space"] = space
        option_data.append(entry)
    return option_data


def _add_reservation_identifier(
    entry: dict[str, Any],
    candidate: dict[str, Any],
    dhcp_contexts: dict[str, dict[str, Any]],
) -> None:
    """Add hw-address or client-id to reservation entry."""
    if candidate.get("mac_address"):
        entry["hw-address"] = candidate["mac_address"]
    elif candidate.get("serial"):
        device_id: str | None = candidate.get("device_id")
        device_context = dhcp_contexts.get(device_id or "", {})
        entry["client-id"] = _get_client_id(candidate, device_context)
    else:
        raise DhcpConfigGenerationError(
            f"Reservation for {candidate['device_name']} on interface "
            f"{candidate['interface_name']} with address {candidate['address']} "
            "has no MAC address or serial"
        )


def _get_subnet_options(
    reservation: dict[str, Any],
    dhcp_contexts: dict[str, dict[str, Any]],
    conflicts: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Get the subnet options for a reservation.

    Args:
        reservation: Reservation data including device_id, interface_name,
        interface_role, etc. and address.
        dhcp_contexts: Dict mapping device_id -> device DHCP context
        conflicts: If provided, append reservation option conflicts here and return None

    Returns:
        (subnet_options, reservation_options, subnet_config) or None if conflict
    """
    subnet_options: dict[str, Any] = {}
    reservation_options: dict[str, Any] = {}
    subnet_config: dict[str, Any] = {}

    # Look up the device's DHCP context by device_id
    device_id = reservation.get("device_id")
    if not device_id:
        return subnet_options, reservation_options, subnet_config

    device_context = dhcp_contexts.get(device_id, {})
    dhcp_section = device_context.get("dhcp", {})
    if not dhcp_section.get("options"):
        return subnet_options, reservation_options, subnet_config

    subnet_options_role, reservation_options_role, subnet_config_role = _get_options(
        dhcp_section["options"].get("interface_roles", {}).get(reservation["interface_role"], {})
    )
    subnet_options_name, reservation_options_name, subnet_config_name = _get_options(
        dhcp_section["options"].get("interface_names", {}).get(reservation["interface_name"], {})
    )

    combined_subnet = _combine_options(subnet_options_role, subnet_options_name, conflicts)
    combined_reservation = _combine_options(
        reservation_options_role, reservation_options_name, conflicts
    )
    combined_config = _combine_options(subnet_config_role, subnet_config_name, conflicts)
    if combined_subnet is None or combined_reservation is None or combined_config is None:
        return None

    return (
        _substitute_options(combined_subnet, device_context, reservation),
        _substitute_options(combined_reservation, device_context, reservation),
        combined_config,
    )


def _combine_options(
    opts1: dict[str, str],
    opts2: dict[str, str],
    conflicts: list[str] | None = None,
) -> dict[str, str] | None:
    """Combine two sets of options. On conflict: append to conflicts (if provided) and return None."""
    result: dict[str, str] = {}
    for opt, value in opts1.items():
        result[opt] = value
    for opt, value in opts2.items():
        if opt in result and result[opt] != value:
            msg = f"Conflicting values for option '{opt}': '{result[opt]}' vs '{value}'"
            if conflicts is not None:
                conflicts.append(msg)
                return None
            logger.error(msg)
            raise DhcpConfigGenerationError(msg)
        result[opt] = value
    return result


def _get_options(
    opts: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Get the options for a reservation."""
    result: dict[str, dict[str, str]] = {}
    for option_type in ["subnet_options", "reservation_options", "subnet_config"]:
        result[option_type] = {}
        for opt, value in opts.get(option_type, {}).items():
            if opt == "routers":
                DHCP_CONFIG_GENERATION_ERRORS.labels(
                    error_type="router_override", ip_version=""
                ).inc()
                raise DhcpConfigGenerationError(
                    f"Cannot override router option with value: {value}"
                )
            result[option_type][opt] = value
    return (
        result["subnet_options"],
        result["reservation_options"],
        result["subnet_config"],
    )


def _substitute_options(
    options: dict[str, str], device_context: dict[str, Any], reservation: dict[str, Any]
) -> dict[str, str]:
    """Substitute variables in DHCP options using Jinja2 templating."""
    env = SandboxedEnvironment(loader=BaseLoader(), autoescape=True)

    if reservation["address"].version == 4:
        ztp_servers = device_context.get("ztp", {}).get("ipv4", [])
    else:
        ztp_servers = device_context.get("ztp", {}).get("ipv6", [])

    ztp_server = random.choice(ztp_servers) if ztp_servers else None

    for value in options.values():
        if "ztp_server" in value and ztp_server is None:
            DHCP_CONFIG_GENERATION_ERRORS.labels(error_type="no_ztp_server", ip_version="").inc()
            raise DhcpConfigGenerationError(
                f"No ZTP server found for {reservation['address']} in DHCP context"
            )

    context = {
        "device_id": reservation.get("device_id", ""),
        "ztp_server": ztp_server,
    }

    result = {}
    for opt, value in options.items():
        try:
            template = env.from_string(value)
            result[opt] = template.render(context)
        except SecurityError as e:
            DHCP_CONFIG_GENERATION_ERRORS.labels(
                error_type="template_security", ip_version=""
            ).inc()
            raise DhcpConfigGenerationError(
                f"Unsafe template expression in DHCP option '{opt}' "
                f"for {reservation['address']}: {e}"
            ) from e
        except UndefinedError as e:
            logger.warning(
                "Undefined variable in option %s: %s, leaving template as is.",
                opt,
                str(e),
            )
            result[opt] = value
        except TemplateSyntaxError as e:
            logger.warning("Template syntax error in option %s: %s, leaving as is.", opt, str(e))
            result[opt] = value

    return result


def _generate_dhcp_pool_ranges(
    pool_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    gateway_ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[dict[str, str]]:
    """Generate pool ranges from tagged IP addresses using netaddr.IPSet."""
    if not pool_ips:
        return []

    # Filter out gateway IP and create IPSet
    filtered_ips = [str(ip) for ip in pool_ips if ip != gateway_ip]

    if not filtered_ips:
        return []

    # Create IPSet and get contiguous ranges
    ip_set = netaddr.IPSet(filtered_ips)
    ranges = []
    for ip_range in ip_set.iter_ipranges():
        first_ip = netaddr.IPAddress(ip_range.first)
        last_ip = netaddr.IPAddress(ip_range.last)
        ranges.append({"pool": f"{first_ip}-{last_ip}"})

    return ranges


def _preserve_subnet_ids(
    previous_config: dict[str, Any],
    new_config: dict[str, Any],
    version: int = 4,
) -> dict[str, Any]:
    """Preserve the subnet IDs from the previous configuration."""
    dhcp_key = f"Dhcp{version}"
    new_subnets = {
        subnet["subnet"] for subnet in new_config.get(dhcp_key, {}).get(f"subnet{version}", [])
    }

    previous_subnets = previous_config.get(dhcp_key, {}).get(f"subnet{version}", [])
    subnet_map = {
        subnet["subnet"]: subnet["id"]
        for subnet in previous_subnets
        if subnet["subnet"] in new_subnets
    }
    max_id = max(subnet_map.values(), default=0)

    # Get all previously used IDs
    used_ids = set(subnet_map.values())
    # Get all possible IDs up to max_id
    all_possible_ids = set(range(1, max_id + 1))
    # Find freed IDs (IDs that were used before but aren't in the new config)
    freed_ids = all_possible_ids - used_ids

    # For new subnets, first try to use freed IDs, then create new ones
    next_new_id = max_id + 1
    for subnet in sorted(new_config[dhcp_key][f"subnet{version}"], key=lambda x: x["subnet"]):
        if subnet["subnet"] in subnet_map:
            # Preserve existing ID
            subnet["id"] = subnet_map[subnet["subnet"]]
        else:
            # Try to use a freed ID first
            if freed_ids:
                subnet["id"] = freed_ids.pop()
            else:
                # If no freed IDs available, use a new one
                subnet["id"] = next_new_id
                next_new_id += 1

    return new_config


def _extract_hooks_path(kea_config: dict[str, Any], version: int = 4) -> str:
    """Extract hooks library path from existing Kea config."""
    default_path = "/usr/lib/x86_64-linux-gnu/kea/hooks"
    dhcp_key = f"Dhcp{version}"

    try:
        hooks_libraries = kea_config.get(dhcp_key, {}).get("hooks-libraries", [])
        if hooks_libraries:
            # Get the directory from the first hooks library path
            library_path: str = hooks_libraries[0].get("library", "")
            if library_path:
                # Extract directory path (remove the .so filename)
                return os.path.dirname(library_path)
    except (KeyError, IndexError, TypeError):
        pass

    return default_path


async def generate_config(
    dcim_client: DCIMClient,
    redis_client: RedisClient | None = None,
    version: int = 4,
    kea_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a KEA DHCP Configuration.

    Args:
        dcim_client: Provider client for fetching normalized DHCP data
        redis_client: Optional Redis client for preserving subnet IDs
        version: DHCP version (4 or 6)
        kea_config: Optional existing Kea config to extract hooks path from
    """
    _start = _time.monotonic()
    config = load_config()
    is_aggregate = config.getboolean("general", "aggregate", fallback=False)

    # Get hooks path from existing Kea config (architecture-specific path set at build time)
    hooks_path = _extract_hooks_path(kea_config or {}, version)

    site_dhcp_options_data = await dcim_client.get_dhcp_site_options()
    family_options = cast(dict[str, Any], site_dhcp_options_data.get(f"Dhcp{version}", {}))
    site_dhcp_options = family_options.get("option-def", [])
    dhcp_key = f"Dhcp{version}"
    dhcp_config = {
        dhcp_key: {
            "interfaces-config": {
                "interfaces": ["eth0"],
                "service-sockets-max-retries": 5,
                "service-sockets-require-all": True,
            },
            "control-socket": {
                "socket-type": "unix",
                "socket-name": f"/var/run/kea/control_socket_{version}",
            },
            "loggers": [
                {
                    "name": f"kea-dhcp{version}",
                    "output-options": [{"output": "stdout"}],
                    "severity": "DEBUG",
                }
            ],
            "hooks-libraries": [{"library": f"{hooks_path}/libdhcp_lease_cmds.so"}],
            "option-def": site_dhcp_options,
            "reservations-global": True,
            "reservations": [],
            f"subnet{version}": [],
        }
    }

    static_data = await dcim_client.get_dhcp_static_data()
    dhcp_contexts = await dcim_client.get_dhcp_contexts(is_aggregate_managed=is_aggregate)

    (
        auto_subnets,
        auto_subnet_reservations,
    ) = await _generate_automatic_dhcp_subnets_and_reservations(
        dcim_client,
        dhcp_contexts,
        version,
        site_dhcp_options,
        is_aggregate_managed=is_aggregate,
    )

    dhcp_config[dhcp_key]["reservations"] = _generate_reservations(
        static_data, auto_subnet_reservations, version
    )

    all_subnets = _generate_subnets(static_data, version)

    # Check for conflicts between auto subnets and existing subnets
    # Create lookup tables for O(1) subnet lookups
    existing_subnet_map = {subnet["subnet"]: subnet for subnet in all_subnets}
    auto_subnet_map = {subnet["subnet"]: subnet for subnet in auto_subnets}
    existing_subnet_addresses = set(existing_subnet_map.keys())
    auto_subnet_addresses = set(auto_subnet_map.keys())
    conflicts = existing_subnet_addresses.intersection(auto_subnet_addresses)

    if conflicts:
        # Log warnings for each conflicting subnet
        for conflict_subnet in sorted(conflicts):
            existing_subnet = existing_subnet_map.get(conflict_subnet)
            auto_subnet = auto_subnet_map.get(conflict_subnet)

            if existing_subnet and auto_subnet:
                logger.warning(
                    "Subnet conflict: auto-generated subnet %s conflicts with existing "
                    "subnet. Using auto-generated subnet.",
                    conflict_subnet,
                )

        # Remove conflicting existing subnets (auto subnets take precedence)
        all_subnets = [subnet for subnet in all_subnets if subnet["subnet"] not in conflicts]

    # Add auto subnets to the configuration
    all_subnets.extend(auto_subnets)

    dhcp_config[dhcp_key][f"subnet{version}"] = all_subnets

    previous_config = {}
    if redis_client:
        previous_config = await redis_client.load_kea_config(version) or {}
    dhcp_config = _preserve_subnet_ids(previous_config, dhcp_config, version)

    DHCP_CONFIG_GENERATION_DURATION.labels(ip_version=str(version)).observe(
        _time.monotonic() - _start
    )
    return dhcp_config


def inject_lease_db_config(
    dhcp_config: dict[str, Any],
    version: int = 4,
) -> dict[str, Any]:
    """Inject the lease database configuration into the DHCP configuration."""
    app_config = load_config()
    dhcp_key = f"Dhcp{version}"
    if not app_config["dhcp.lease_db"].getboolean("local"):
        dhcp_config[dhcp_key]["lease-database"] = {
            "type": "postgresql",
            "name": app_config["dhcp.lease_db"]["database"],
            "host": app_config["dhcp.lease_db"]["host"],
            "user": app_config["dhcp.lease_db"]["user"],
            "password": app_config["dhcp.lease_db"]["password"],
        }
    return dhcp_config
