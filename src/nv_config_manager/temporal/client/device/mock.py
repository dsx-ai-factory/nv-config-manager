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
"""Mock network connection for local development."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.client.device.models import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
)


class MockNetworkConnection(NetworkConnection):
    """Mock Network Connection for Local Dev."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize a Mock Network Connection."""
        super().__init__(host, port, username, password, site)

    def get_running_configuration(self) -> str:
        """Load the running configuration for a given device."""
        # TODO: set up some more realistic mock data
        return "Mock Network Config"

    def get_mac_table(self) -> DeviceMacTable:
        """Get the device MAC table."""
        return DeviceMacTable()

    def get_arp_table(self) -> DeviceArpTable:
        """Get the device ARP table."""
        return DeviceArpTable()

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get interface connections from LLDP."""
        return DeviceNeighborData()

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the candidate configuration and return the diff."""
        # TODO: set up some more realistic mock data
        return "Mock Network Diff"

    def get_hostname(self) -> str:
        """Get the system hostname."""
        return "MockHostName"

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the candidate configuration and commit."""

    def get_platform(self) -> Any:
        """Get the platform information."""
        return {"mock": "platform data"}

    def get_platform_environment_fan(self) -> Any:
        """Get platform fan information."""
        return {"mock": "fan data"}

    def get_platform_environment_led(self) -> Any:
        """Get platform LED information."""
        return {"mock": "led data"}

    def get_platform_environment_psu(self) -> Any:
        """Get platform PSU information."""
        return {"mock": "psu data"}

    def get_platform_environment_voltage(self) -> Any:
        """Get platform voltage information."""
        return {"mock": "voltage data"}

    def get_platform_inventory(self) -> Any:
        """Get platform inventory information."""
        return {"mock": "inventory data"}

    def diag_get_version(self) -> object:
        return {"mock": True, "command": "show_version"}

    def diag_get_interfaces(self) -> object:
        return {"mock": True, "command": "show_interfaces"}

    def diag_get_bgp_summary(self) -> object:
        return {"mock": True, "command": "show_bgp_summary"}

    def diag_get_lldp_neighbors(self) -> object:
        return {"mock": True, "command": "show_lldp_neighbors"}

    def diag_get_platform(self) -> object:
        return {"mock": True, "command": "show_platform"}

    def diag_get_route_table(self) -> object:
        return {"mock": True, "command": "show_route_table"}

    def diag_get_vlan(self) -> object:
        return {"mock": True, "command": "show_vlan"}

    def diag_get_vrf(self) -> object:
        return {"mock": True, "command": "show_vrf"}

    def diag_get_arp_table(self) -> object:
        return {"mock": True, "command": "show_arp_table"}

    def diag_get_mac_table(self) -> object:
        return {"mock": True, "command": "show_mac_table"}

    def diag_get_mlag(self) -> object:
        return {"mock": True, "command": "show_mlag"}

    def diag_get_spanning_tree(self) -> object:
        return {"mock": True, "command": "show_spanning_tree"}

    def diag_get_port_channels(self) -> object:
        return {"mock": True, "command": "show_port_channels"}

    def diag_get_isis_neighbors(self) -> object:
        return {"mock": True, "command": "show_isis_neighbors"}

    def diag_get_isis_interfaces(self) -> object:
        return {"mock": True, "command": "show_isis_interfaces"}

    def diag_get_isis_database(self) -> object:
        return {"mock": True, "command": "show_isis_database"}

    def diag_get_mpls_interfaces(self) -> object:
        return {"mock": True, "command": "show_mpls_interfaces"}

    def diag_get_mpls_rsvp_neighbors(self) -> object:
        return {"mock": True, "command": "show_mpls_rsvp_neighbors"}

    def diag_get_mac_security(self) -> object:
        return {"mock": True, "command": "show_mac_security"}

    def diag_get_mac_security_counters(self) -> object:
        return {"mock": True, "command": "show_mac_security_counters"}

    def diag_get_vrrp(self) -> object:
        return {"mock": True, "command": "show_vrrp"}

    def diag_get_inventory(self) -> object:
        return {"mock": True, "command": "show_inventory"}

    def diag_get_system_health(self) -> object:
        return {"mock": True, "command": "show_system_health"}

    def diag_get_interface_counters(self) -> object:
        return {"mock": True, "command": "show_interface_counters"}

    def diag_get_interface_mac(self) -> object:
        return {"mock": True, "command": "show_interface_mac"}

    def diag_get_platform_environment(self) -> object:
        return {"mock": True, "command": "show_platform_environment"}

    def diag_get_platform_transceiver(self) -> object:
        return {"mock": True, "command": "show_platform_transceiver"}

    def get_tech_support_bundle(
        self, heartbeat_fn: Callable[[], None] | None = None
    ) -> tuple[bytes, str]:
        """Return a predictable stub bundle for unit testing."""
        return (
            b"[mock tech-support bundle]",
            "Mock cl-support output\nSaved cl_support output to /var/support/mock_bundle.txz.",
        )
