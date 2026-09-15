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
"""Mellanox MLNX-OS device connection (netmiko)."""

from __future__ import annotations

from netmiko import ConnectHandler  # type: ignore[import-untyped]
from netmiko.base_connection import BaseConnection  # type: ignore[import-untyped]
from netmiko.exceptions import NetmikoAuthenticationException  # type: ignore[import-untyped]

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.client.device.exceptions import (
    DiffChangedException,
    NetworkDeviceException,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


class MellanoxConnection(NetworkConnection):
    """Mellanox device connection."""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize connection."""
        super().__init__(host, port, username, password, site)
        self.client: BaseConnection | None = None

    def _connect(self) -> BaseConnection:
        """Connect to the device with password rotation support."""

        def connect_with_password(password: str) -> BaseConnection:
            device = {
                "device_type": "mellanox_mlnxos",
                "host": self._host,
                "port": self._port,
                "username": self._username,
                "password": password,
            }
            return ConnectHandler(**device)

        return self._try_passwords_with_callback(
            connect_with_password,
            (NetmikoAuthenticationException,),
        )

    def execute_command(self, command: str) -> str:
        """Execute a command on the device."""
        if not self.client:
            self.client = self._connect()
        output = self.client.send_command(command)
        return str(output)

    def execute_enable_command(self, command: str, timeout: int = 10) -> str:
        """Execute a command in enable mode."""
        if not self.client:
            self.client = self._connect()
        self.client.enable()
        output = self.client.send_command(command, expect_string=r"#", read_timeout=timeout)
        return str(output)

    def execute_configure_command(self, command: str, timeout: int = 10) -> str:
        """Execute a command in configure mode."""
        if not self.client:
            self.client = self._connect()
        self.client.enable()
        self.client.config_mode()
        output = self.client.send_command(command, expect_string=r"#", read_timeout=timeout)
        return str(output)

    def close(self) -> None:
        """Disconnect the netmiko session if it is open."""
        client = getattr(self, "client", None)
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - cleanup must not raise
            logger.debug("Error closing SSH session to %s", self._host, exc_info=True)
            return
        self.client = None

    def __del__(self) -> None:
        """Best-effort cleanup of the SSH session on garbage collection."""
        self.close()

    def _get_diff(self, current_config: str, new_configuration: str) -> str:
        """Get the diff between the current and new configuration in git style."""
        current_config_lines = [
            line.strip()
            for line in current_config.splitlines()
            if not line.startswith("#") and line.strip()
        ]
        new_config_lines = [
            line.strip()
            for line in new_configuration.splitlines()
            if not line.startswith("#") and line.strip()
        ]
        diff = []
        current_set = set(current_config_lines)
        new_set = set(new_config_lines)

        for line in current_config_lines:
            if line not in new_set:
                diff.append(f"- {line}")

        for line in new_config_lines:
            if line not in current_set:
                diff.append(f"+ {line}")

        return "\n".join(diff)

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the candidate configuration and return the diff."""
        try:
            current_config = self.execute_enable_command("show running-config")
            diff = self._get_diff(current_config, new_configuration)
            return diff
        except Exception as e:
            raise NetworkDeviceException("Failed to perform candidate diff.") from e

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the candidate configuration and commit."""
        try:
            diff = self.perform_candidate_diff(new_configuration, partial)
            if diff != approved_diff:
                raise DiffChangedException("Diff has changed since approval, aborting.")
            for line in diff.splitlines():
                if line.startswith("-"):
                    line = "no " + line.strip("- ")
                elif line.startswith("+"):
                    line = line.strip("+ ")
                self.execute_configure_command(line)
            self.execute_configure_command("write memory")
        except DiffChangedException:
            raise
        except Exception as e:
            raise NetworkDeviceException("Failed to commit candidate configuration.") from e

    def get_running_configuration(self) -> str:
        """Load the running configuration for a given device."""
        response = self.execute_enable_command("show running-config")
        return response
