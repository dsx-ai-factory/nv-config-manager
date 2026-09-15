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
"""Base network connection interface."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Self

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.client.device.exceptions import NetworkDeviceException
from nv_config_manager.temporal.client.device.models import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.common.secrets import (
    get_credential,
    get_rotation_passwords,
    resolve_config_section,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)

# Commit-confirm rollback: 5 minutes. Arista uses HH:MM:SS; Cumulus uses seconds.
COMMIT_CONFIRM_ROLLBACK_SECONDS = 5 * 60


class NetworkConnection:
    """Generic Network Connection Class."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize a network connection.

        Args:
            host: Device hostname or IP address
            port: Connection port
            username: Username for authentication (optional, defaults from config)
            password: Password for authentication (optional, defaults from config)
            site: Site name for site-specific password lookup (optional)
        """
        config = load_config()
        self._host = host
        self._port = port
        self._username = username or config["device"]["username"]

        # Build list of passwords to try for authentication
        self._passwords_to_try: list[str] = []
        self._working_password: str | None = None

        # If explicit password provided, use only that
        if password:
            self._passwords_to_try = [password]
            logger.debug("Using explicit password for %s@%s", self._username, self._host)
            return

        # Determine which config and section to use for passwords
        # This checks the secrets config first, then falls back to main config
        password_config, password_section = resolve_config_section(config, "device", site)
        passwords = get_rotation_passwords(password_config, password_section)

        if passwords:
            logger.debug(
                "Loaded %d rotation password(s) for %s@%s",
                len(passwords),
                self._username,
                self._host,
            )
            self._passwords_to_try = passwords
        else:
            # Fallback to "password" key using get_credential (handles site fallback)
            fallback = get_credential(config, "device", "password", site)
            logger.debug(
                "Using fallback password (no rotation keys found) for %s@%s",
                self._username,
                self._host,
            )
            self._passwords_to_try = [fallback] if fallback else []

    def _get_passwords_to_try(self) -> list[str]:
        """Get list of passwords to try, with cached working password first."""
        if self._working_password and self._working_password in self._passwords_to_try:
            # Put working password first, then the rest (deduplicated)
            return [self._working_password] + [
                p for p in self._passwords_to_try if p != self._working_password
            ]
        return list(self._passwords_to_try)

    def _try_passwords_with_callback(
        self,
        connect_callback: Callable[[str], Any],
        error_types: tuple[type[Exception], ...],
    ) -> Any:
        """Try authentication with password rotations using a callback.

        Args:
            connect_callback: Function that takes a password and returns a connection
            error_types: Tuple of exception types to catch and retry

        Returns:
            The result from the successful connect_callback

        Raises:
            NetworkDeviceException: If all password attempts fail
        """
        passwords = self._get_passwords_to_try()
        last_error = None

        for idx, password in enumerate(passwords, 1):
            try:
                logger.debug(
                    "Attempting authentication to %s with %s (attempt %d/%d)",
                    self._host,
                    self._username,
                    idx,
                    len(passwords),
                )
                result = connect_callback(password)
                # Connection successful - cache this password
                self._working_password = password
                logger.debug(
                    "Successfully authenticated to %s with %s (attempt %d)",
                    self._host,
                    self._username,
                    idx,
                )
                return result
            except error_types as exc:
                last_error = exc
                logger.warning(
                    "Authentication failed for %s@%s (attempt %d/%d): %s",
                    self._username,
                    self._host,
                    idx,
                    len(passwords),
                    exc,
                )
                continue

        raise NetworkDeviceException(
            f"All passwords failed for {self._username}@{self._host} after {len(passwords)} attempt(s)"
        ) from last_error

    def get_running_configuration(self) -> str:
        """Load the running configuration for a given device."""
        raise NotImplementedError()

    def get_hostname(self) -> str:
        """Get the system hostname."""
        raise NotImplementedError()

    def get_mac_table(self) -> DeviceMacTable:
        """Get the device MAC table."""
        raise NotImplementedError()

    def get_arp_table(self) -> DeviceArpTable:
        """Get the device ARP table."""
        raise NotImplementedError()

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        """Get the raw LLDP data for a given interface."""
        raise NotImplementedError()

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections for the device."""
        raise NotImplementedError()

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the candidate configuration and return the diff."""
        raise NotImplementedError()

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the candidate configuration and commit."""
        raise NotImplementedError()

    def get_rollback_diff(self, rollback_id: int = 1) -> str:
        """Return the diff between the active config and a numbered rollback."""
        raise NotImplementedError()

    def rollback_configuration(self, rollback_id: int = 1, *, commit_confirm: bool = True) -> None:
        """Roll back to a numbered rollback revision and commit."""
        raise NotImplementedError()

    def save_rescue_configuration(self) -> None:
        """Save the current active config as the rescue checkpoint."""
        raise NotImplementedError()

    def get_rescue_configuration(self) -> str | None:
        """Return the saved rescue configuration, or None if none is set."""
        raise NotImplementedError()

    def delete_rescue_configuration(self) -> None:
        """Delete the saved rescue configuration."""
        raise NotImplementedError()

    def rollback_to_rescue(self, *, commit_confirm: bool = True) -> None:
        """Roll back to the saved rescue configuration and commit."""
        raise NotImplementedError()

    def execute_ztp(self) -> None:
        """Execute ZTP on the device."""
        raise NotImplementedError()

    def get_running_image(self) -> str:
        """Get the running image on the device."""
        raise NotImplementedError()

    def get_ztp_status(self) -> str:
        """Get the ZTP status of the device."""
        raise NotImplementedError()

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        raise NotImplementedError()

    def get_platform(self) -> Any:
        """Get the platform information."""
        raise NotImplementedError()

    def get_platform_environment_fan(self) -> Any:
        """Get platform fan information."""
        raise NotImplementedError()

    def get_platform_environment_led(self) -> Any:
        """Get platform LED information."""
        raise NotImplementedError()

    def get_platform_environment_psu(self) -> Any:
        """Get platform PSU information."""
        raise NotImplementedError()

    def get_platform_environment_voltage(self) -> Any:
        """Get platform voltage information."""
        raise NotImplementedError()

    def get_platform_inventory(self) -> Any:
        """Get platform inventory information."""
        raise NotImplementedError()

    def get_firmware_versions(self) -> dict[str, Any]:
        """Get firmware versions for all components."""
        raise NotImplementedError()

    def reboot(self) -> None:
        """Reboot the device."""
        raise NotImplementedError()

    def diag_get_version(self) -> object:
        raise NotImplementedError()

    def diag_get_interfaces(self) -> object:
        raise NotImplementedError()

    def diag_get_bgp_summary(self) -> object:
        raise NotImplementedError()

    def diag_get_lldp_neighbors(self) -> object:
        raise NotImplementedError()

    def diag_get_platform(self) -> object:
        raise NotImplementedError()

    def diag_get_route_table(self) -> object:
        raise NotImplementedError()

    def diag_get_vlan(self) -> object:
        raise NotImplementedError()

    def diag_get_vrf(self) -> object:
        raise NotImplementedError()

    def diag_get_arp_table(self) -> object:
        raise NotImplementedError()

    def diag_get_mac_table(self) -> object:
        raise NotImplementedError()

    def diag_get_mlag(self) -> object:
        raise NotImplementedError()

    def diag_get_spanning_tree(self) -> object:
        raise NotImplementedError()

    def diag_get_port_channels(self) -> object:
        raise NotImplementedError()

    def diag_get_isis_neighbors(self) -> object:
        raise NotImplementedError()

    def diag_get_isis_interfaces(self) -> object:
        raise NotImplementedError()

    def diag_get_isis_database(self) -> object:
        raise NotImplementedError()

    def diag_get_mpls_interfaces(self) -> object:
        raise NotImplementedError()

    def diag_get_mpls_rsvp_neighbors(self) -> object:
        raise NotImplementedError()

    def diag_get_mac_security(self) -> object:
        raise NotImplementedError()

    def diag_get_mac_security_counters(self) -> object:
        raise NotImplementedError()

    def diag_get_vrrp(self) -> object:
        raise NotImplementedError()

    def diag_get_inventory(self) -> object:
        raise NotImplementedError()

    def diag_get_system_health(self) -> object:
        raise NotImplementedError()

    def diag_get_interface_counters(self) -> object:
        raise NotImplementedError()

    def diag_get_interface_mac(self) -> object:
        raise NotImplementedError()

    def diag_get_platform_environment(self) -> object:
        raise NotImplementedError()

    def diag_get_platform_transceiver(self) -> object:
        raise NotImplementedError()

    def run_diagnostic_command(self, name: str) -> str:
        """Run a named diagnostic command and return JSON-formatted output.

        Dispatches to the appropriate diag_get_* method for the given command
        name and serialises the result as indented JSON. Supported command names
        vary by platform — see the diagnostics catalog for the full list.

        Raises:
            NetworkDeviceException: If the command name is unknown or the
                platform does not implement it.
        """
        dispatch: dict[str, Callable[[], object]] = {
            "show_version": self.diag_get_version,
            "show_interfaces": self.diag_get_interfaces,
            "show_bgp_summary": self.diag_get_bgp_summary,
            "show_lldp_neighbors": self.diag_get_lldp_neighbors,
            "show_platform": self.diag_get_platform,
            "show_route_table": self.diag_get_route_table,
            "show_vlan": self.diag_get_vlan,
            "show_vrf": self.diag_get_vrf,
            "show_arp_table": self.diag_get_arp_table,
            "show_mac_table": self.diag_get_mac_table,
            "show_mlag": self.diag_get_mlag,
            "show_spanning_tree": self.diag_get_spanning_tree,
            "show_port_channels": self.diag_get_port_channels,
            "show_isis_neighbors": self.diag_get_isis_neighbors,
            "show_isis_interfaces": self.diag_get_isis_interfaces,
            "show_isis_database": self.diag_get_isis_database,
            "show_mpls_interfaces": self.diag_get_mpls_interfaces,
            "show_mpls_rsvp_neighbors": self.diag_get_mpls_rsvp_neighbors,
            "show_mac_security": self.diag_get_mac_security,
            "show_mac_security_counters": self.diag_get_mac_security_counters,
            "show_vrrp": self.diag_get_vrrp,
            "show_inventory": self.diag_get_inventory,
            "show_system_health": self.diag_get_system_health,
            "show_interface_counters": self.diag_get_interface_counters,
            "show_interface_mac": self.diag_get_interface_mac,
            "show_platform_environment": self.diag_get_platform_environment,
            "show_platform_transceiver": self.diag_get_platform_transceiver,
        }
        if name not in dispatch:
            raise NetworkDeviceException(
                f"Diagnostic command '{name}' is not supported on this platform. "
                f"Supported: {sorted(dispatch)}"
            )
        try:
            result = dispatch[name]()
        except NotImplementedError as error:
            raise NetworkDeviceException(
                f"Diagnostic command '{name}' is not implemented for {type(self).__name__}."
            ) from error
        return json.dumps(result, indent=2)

    def get_tech_support_bundle(
        self, heartbeat_fn: Callable[[], None] | None = None
    ) -> tuple[bytes, str]:
        """Generate and retrieve a full platform tech-support bundle.

        Returns:
            A tuple of (raw bundle bytes, full cl-support command output text).
        """
        raise NotImplementedError()

    def close(self) -> None:
        """Release any underlying session; subclasses override when needed."""

    def __enter__(self) -> Self:
        """Enter a context that closes the connection on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the connection when leaving the context."""
        self.close()

    @staticmethod
    def from_device_data(device_data: NetworkDeviceData) -> NetworkConnection:
        """Return a NetworkConnection for a given device."""
        from nv_config_manager.temporal.client.device.factory import (
            from_device_data as connection_from_device_data,
        )  # avoid circular import

        return connection_from_device_data(device_data)
