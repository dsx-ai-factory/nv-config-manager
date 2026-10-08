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
"""Custom Jinja2 Filters for IP Related objects."""

import ipaddress
from math import ceil

from nv_config_manager_templates.filters import FilterException


def gateway(value: str) -> str:
    """Extract the gateway IP from a given CIDR."""
    try:
        ip_interface = ipaddress.ip_interface(value)
        # Working under the assumption that by default the gateway IP address
        # should be the first IP in the subnet
        # Use cases that do not comply with this assumption must have appropriate data
        # in nautobot to use instead.
        if ip_interface.network.prefixlen in [32, 128]:
            raise FilterException("Gateway is not supported for /32 or /128 subnets.")
        if ip_interface.network.prefixlen in [31, 127]:
            return str(ip_interface.network.network_address)
        return str(ip_interface.network.network_address + 1)
    except ValueError as exc:
        raise FilterException("gateway filter is only valid against CIDR strings.") from exc


def subnet(value: str, prefixlen: int) -> list[str]:
    """Divide a prefix into a list of subnets."""
    try:
        net = ipaddress.ip_network(value)
        if prefixlen < net.prefixlen:
            raise FilterException(f"Cannot subdivide {value} into /{prefixlen} subnets.")
        return [str(subnet) for subnet in net.subnets(new_prefix=prefixlen)]
    except ValueError as exc:
        raise FilterException("subnet filter is only valid against CIDR strings.") from exc


def supernet(value: str, prefixlen: int) -> str:
    """Return the supernet containing this subnet."""
    try:
        net = ipaddress.ip_network(value)
        if prefixlen > net.prefixlen:
            raise FilterException(f"Cannot contain {value} in a /{prefixlen} supernet.")
        return str(net.supernet(new_prefix=prefixlen))
    except ValueError as exc:
        raise FilterException("supernet filter is only valid against CIDR strings.") from exc


def ips(value: str) -> list[str]:
    """Return a list of IPs contained within a CIDR."""
    try:
        net = ipaddress.ip_network(value)
        return [str(ipaddr) for ipaddr in net]
    except ValueError as exc:
        raise FilterException("ips filter is only valid against CIDR strings.") from exc


def netmask_notation(value: str) -> tuple[str, str]:
    """Convert a cidr to netmask notation."""
    try:
        net = ipaddress.ip_network(value)
        return (str(net.network_address), str(net.netmask))
    except ValueError as exc:
        raise FilterException(
            "netmask_notation filter is only valid against CIDR strings."
        ) from exc


def rfc3442_classless_static_route(value: str, next_hop: str) -> str:
    """Create a RFC3442 static route entry."""
    # option rfc3442-classless-static-routes X, X,X,X,X, X,X,X,X;
    try:
        net = ipaddress.ip_network(value)
        # Purely for validation
        _ = ipaddress.ip_address(next_hop)
        octets = str(net.network_address).split(".")
        # Only include significant octets
        significant_octet_count = ceil(net.prefixlen / 8)
        route_str = ",".join(octets[:significant_octet_count])
        next_hop_str = next_hop.replace(".", ",")
        return f"{net.prefixlen}, {route_str}, {next_hop_str}"
    except ValueError as exc:
        raise FilterException("Invalid CIDR or next hop.") from exc


def get_peer_ip(cidr: str) -> str:
    """Given a CIDR notation string, return the other IP address in the subnet."""
    try:
        ip_interface = ipaddress.ip_interface(cidr)
        if ip_interface.network.prefixlen != 31:
            raise FilterException("get_peer_ip filter requires a /31 CIDR.")
        if ip_interface.ip == ip_interface.network.network_address:
            return str(ip_interface.network.broadcast_address)
        return str(ip_interface.network.network_address)
    except ValueError as exc:
        raise FilterException("get_peer_ip filter is only valid against CIDR strings.") from exc


def network_address(value: str) -> str:
    """Given an ip address with a prefix, return the network address."""
    try:
        ip_interface = ipaddress.ip_interface(value)
        network = ip_interface.network
        return f"{network.network_address}/{network.prefixlen}"
    except ValueError as exc:
        raise FilterException("network_address filter is only valid against CIDR strings.") from exc


def host_range(value: str) -> tuple[str, str]:
    """Return the host range for the given subnet."""
    try:
        network = ipaddress.ip_network(value)
        # network +2 to broadcast -1
        return str(network.network_address + 2), str(network.broadcast_address - 1)
    except ValueError as exc:
        raise FilterException("host_range filter is only valid against CIDR strings.") from exc
