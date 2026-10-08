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
"""IP filter tests."""

import pytest

from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.filters.ip import (
    gateway,
    get_peer_ip,
    host_range,
    ips,
    netmask_notation,
    network_address,
    rfc3442_classless_static_route,
    subnet,
    supernet,
)


def test_gateway() -> None:
    """Gateway is the first usable address, with point-to-point handling."""
    assert gateway("10.0.0.3/24") == "10.0.0.1"
    assert gateway("::3/64") == "::1"
    assert gateway("10.0.0.3/31") == "10.0.0.2"

    with pytest.raises(FilterException, match="gateway filter is only valid against CIDR strings."):
        gateway("noncidrtext")


def test_subnet() -> None:
    """Prefixes subdivide into requested child subnets."""
    assert subnet("10.0.0.0/24", 25) == ["10.0.0.0/25", "10.0.0.128/25"]

    with pytest.raises(FilterException, match="subnet filter is only valid against CIDR strings."):
        subnet("noncidrtest", 23)

    with pytest.raises(FilterException, match="Cannot subdivide 10.0.0.0/24 into /23 subnets."):
        subnet("10.0.0.0/24", 23)


def test_supernet() -> None:
    """Prefixes resolve to containing supernets."""
    assert supernet("10.0.0.128/25", 24) == "10.0.0.0/24"

    with pytest.raises(
        FilterException, match="supernet filter is only valid against CIDR strings."
    ):
        supernet("noncidrtest", 23)

    with pytest.raises(FilterException, match="Cannot contain 10.0.0.0/24 in a /25 supernet."):
        supernet("10.0.0.0/24", 25)


def test_ips() -> None:
    """All IPs in a prefix are returned."""
    assert ips("10.0.0.0/31") == ["10.0.0.0", "10.0.0.1"]

    with pytest.raises(FilterException, match="ips filter is only valid against CIDR strings."):
        ips("noncidrtest")


def test_netmask_notation() -> None:
    """CIDR notation converts to address/netmask notation."""
    assert netmask_notation("10.0.0.0/31") == ("10.0.0.0", "255.255.255.254")

    with pytest.raises(
        FilterException,
        match="netmask_notation filter is only valid against CIDR strings.",
    ):
        netmask_notation("noncidrtest")


def test_rfc3442_classless_static_route() -> None:
    """RFC3442 route encoding includes significant destination octets and next hop."""
    assert rfc3442_classless_static_route("172.4.0.0/14", "172.4.0.2") == "14, 172,4, 172,4,0,2"

    with pytest.raises(FilterException, match="Invalid CIDR or next hop."):
        rfc3442_classless_static_route("noncidrtest", "172.4.0.2")

    with pytest.raises(FilterException, match="Invalid CIDR or next hop."):
        rfc3442_classless_static_route("172.4.0.0/14", "noniptest")


def test_get_peer_ip() -> None:
    """The opposite address in a /31 is returned."""
    assert get_peer_ip("10.91.160.8/31") == "10.91.160.9"
    assert get_peer_ip("10.0.0.5/31") == "10.0.0.4"

    with pytest.raises(FilterException, match="/31 CIDR"):
        get_peer_ip("10.0.0.2/24")

    with pytest.raises(
        FilterException, match="get_peer_ip filter is only valid against CIDR strings."
    ):
        get_peer_ip("noCidr")


def test_network_address() -> None:
    """CIDR interfaces resolve to network prefixes."""
    assert network_address("10.0.0.3/24") == "10.0.0.0/24"
    assert network_address("::3/64") == "::/64"

    with pytest.raises(
        FilterException, match="network_address filter is only valid against CIDR strings."
    ):
        network_address("noncidrtext")


def test_host_range() -> None:
    """Host ranges skip the gateway and exclude broadcast."""
    assert host_range("10.91.33.16/29") == ("10.91.33.18", "10.91.33.22")
    assert host_range("192.0.2.0/30") == ("192.0.2.2", "192.0.2.2")

    with pytest.raises(
        FilterException, match="host_range filter is only valid against CIDR strings."
    ):
        host_range("noncidrtext")
