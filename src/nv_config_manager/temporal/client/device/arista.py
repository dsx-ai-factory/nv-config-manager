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
"""Arista EOS device connection (eAPI)."""

from __future__ import annotations

import re
import ssl
import sys
from typing import cast
from uuid import uuid4

import pyeapi
import pyeapi.eapilib
from pyeapi.client import Node
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.client.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager.temporal.client.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
)
from nv_config_manager.temporal.client.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


class AristaConnection(NetworkConnection):
    """Arista Device Connection."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize an Arista Connection."""
        super().__init__(host, port, username, password, site)
        self._node = self._connect()
        self._session_id: str | None = None

    def _connect(self) -> Node:
        # Until we get to a point where we can load and rotate
        # https certs on Aristas, these are self-signed by default.
        # Additionally, out of the box, Arista does not support
        # the set of ciphers required by python3.10+
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT")

        def connect_with_password(password: str) -> Node:
            return pyeapi.connect(
                transport="https",
                host=self._host,
                port=self._port,
                username=self._username,
                password=password,
                enablepwd=password,
                return_node=True,
                context=ctx,
                timeout=300,
            )

        return self._try_passwords_with_callback(
            connect_with_password,
            (pyeapi.eapilib.ConnectionError, pyeapi.eapilib.CommandError),
        )

    def get_running_configuration(self) -> str:
        """Load the running configuration for a given device."""
        response = self._node.enable("show running-config sanitized", encoding="text")
        return cast(str, response[0]["result"]["output"])

    def get_mac_table(self) -> DeviceMacTable:
        """Get the device MAC table."""
        response = self._node.enable("show mac address-table")
        result = DeviceMacTable(by_interface={}, by_mac={})
        try:
            for row in response[0]["result"]["unicastTable"]["tableEntries"]:
                if not row["interface"]:
                    continue
                entry = DeviceMacEntry.from_eapi(row)
                if entry.interface not in result.by_interface:
                    result.by_interface[entry.interface] = []
                if entry.mac not in result.by_interface[entry.interface]:
                    result.by_interface[entry.interface].append(entry.mac)
                if entry.mac in result.by_mac:
                    logger.warning(
                        "Duplicate MAC address %s on device %s: using newest entry",
                        entry.mac,
                        self._host,
                    )
                    if entry.age > result.by_mac[entry.mac].age:
                        continue
                result.by_mac[entry.mac] = entry
        except KeyError as exc:
            raise NetworkDeviceException(
                "Failed to parse JSON output of 'show mac address-table'."
            ) from exc

        return result

    def get_arp_table(self) -> DeviceArpTable:
        """Get the device ARP table."""
        response = self._node.enable("show ip arp")
        return DeviceArpTable.from_eapi(response[0]["result"])

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        response = self._node.enable(
            f"show lldp neighbors {interface_name} detail", encoding="json"
        )
        for interface, neighbor in response[0]["result"]["lldpNeighbors"].items():
            if len(neighbor["lldpNeighborInfo"]) > 1:
                raise NetworkDeviceException(
                    f"Received multiple LLDP neighbors on interface {interface} from {self._host}"
                )
            if neighbor["lldpNeighborInfo"]:
                return InterfaceNeighborData.from_eapi(neighbor)
        return None

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections from LLDP."""
        response = self._node.enable("show lldp neighbors detail", encoding="json")
        neighbors = {}
        for interface, neighbor in response[0]["result"]["lldpNeighbors"].items():
            if len(neighbor["lldpNeighborInfo"]) > 1:
                raise NetworkDeviceException(
                    f"Received multiple LLDP neighbors on interface {interface} from {self._host}"
                )
            if neighbor["lldpNeighborInfo"]:
                neighbors[interface] = InterfaceNeighborData.from_eapi(neighbor)

        link_states = {}
        response = self._node.enable("show interfaces status", encoding="json")
        for interface, data in response[0]["result"]["interfaceStatuses"].items():
            link_states[interface] = data["linkStatus"] == "connected"

        return DeviceNeighborData(neighbors=neighbors, link_states=link_states)

    def _extract_banner_commands(self, new_configuration: str) -> tuple[str, list[tuple[str, str]]]:
        """Extract banners into multiline commands."""
        pattern = re.compile(r"(banner \w+)(.*?)EOF", flags=re.S)
        matches = pattern.findall(new_configuration)
        modified_configuration = pattern.sub("", new_configuration)
        return modified_configuration, matches

    def _load_candidate_config(self, new_configuration: str, partial: bool = False) -> None:
        self._session_id = str(uuid4())

        config_commands: list[str | dict[str, str]] = [
            f"configure session {self._session_id}",
        ]
        if not partial:
            config_commands.append("rollback clean-config")
        configuration, banner_commands = self._extract_banner_commands(new_configuration)
        multiline_commands = [{"cmd": cmd[0], "input": cmd[1]} for cmd in banner_commands]
        config_commands.extend(multiline_commands)
        config_commands.extend(
            [cmd for cmd in configuration.splitlines() if cmd.strip() not in ["!", ""]]
        )
        if (
            not isinstance(config_commands[-1], str) or config_commands[-1].strip() != "end"  # type: ignore[union-attr]
        ):
            config_commands.append("end")
        try:
            self._node.run_commands(config_commands)
        except pyeapi.eapilib.CommandError as exc:
            raise ConfigSyntaxException("Invalid configuration supplied.") from exc
        except Exception as exc:  # pylint disable=broad-except
            raise NetworkDeviceException("Failed to run commands against the device.") from exc

    def _abort(self) -> None:
        # Clean up the config session if present
        if self._session_id is None:
            # Nothing to clean up
            return
        try:
            sessions = self._node.enable("show configuration sessions")
            if self._session_id in sessions[0]["result"]["sessions"]:
                self._node.enable(f"configure session {self._session_id} abort")
        except Exception as exc:  # pylint disable=broad-except
            raise NetworkDeviceException(
                f"Failed to cleanup session {self._session_id}, resolve manually."
            ) from exc

    def _abort_preserving_pending(self) -> None:
        """Abort the config session without masking an in-flight exception."""
        pending = sys.exc_info()[1]
        try:
            self._abort()
        except Exception:  # noqa: BLE001 - cleanup must not mask the original error
            if pending is None:
                raise
            logger.warning(
                "Failed to cleanup session %s, resolve manually.",
                self._session_id,
                exc_info=True,
            )

    def _diff(self) -> str:
        diff = self._node.enable(
            f"show session-config named {self._session_id} diffs", encoding="text"
        )[0]["result"]["output"]
        return cast(str, diff)

    def _diff_eq(self, diff_a: str, diff_b: str) -> bool:
        # Arista session diffs include the session ID in the output
        # strip those lines when comparing
        if diff_b == "":
            # Previous run succeeded to apply but failed on copy run start
            return True
        pattern = re.compile(r"session:/.*?-session-config")
        return pattern.sub("", diff_a) == pattern.sub("", diff_b)

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the candidate configuration and perform a diff."""
        try:
            self._load_candidate_config(new_configuration, partial=partial)
            return self._diff()
        except ConfigSyntaxException as exc:
            raise exc
        except Exception as exc:  # pylint: disable=broad-except
            raise NetworkDeviceException(f"Failed to diff session {self._session_id}") from exc
        finally:
            self._abort_preserving_pending()

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the candidate configuration and commit.

        When commit_confirm is True, uses commit timer so changes roll back if not
        confirmed; we then confirm to make permanent. When False, commits directly
        (use for changes that cause brief interruption, e.g. IP change ahead of
        upstream VLAN change).
        """
        try:
            self._load_candidate_config(new_configuration, partial=partial)
            diff = self._diff()
            if not self._diff_eq(diff, approved_diff):
                raise DiffChangedException("Diff has changed since approval, aborting.")
            if commit_confirm:
                # commit timer HH:MM:SS = roll back unless confirmed; commit applies
                m, s = divmod(COMMIT_CONFIRM_ROLLBACK_SECONDS, 60)
                h, m = divmod(m, 60)
                timer_str = f"{h:02d}:{m:02d}:{s:02d}"
                self._node.run_commands(
                    [
                        f"configure session {self._session_id}",
                        f"commit timer {timer_str}",
                        "commit",
                    ]
                )
                # Confirm (make permanent); from exec: configure session <name> commit
                self._node.enable(f"configure session {self._session_id} commit")
            else:
                self._node.enable(f"configure session {self._session_id} commit")
            self._node.enable("copy running-config startup-config")
        except (ConfigSyntaxException, DiffChangedException):
            raise
        except Exception as exc:  # pylint: disable=broad-except
            raise NetworkDeviceException(f"Failed to commit session {self._session_id}") from exc
        finally:
            self._abort_preserving_pending()

    def get_hostname(self) -> str:
        """Get the system hostname."""
        try:
            return str(self._node.enable("show hostname")[0]["result"]["hostname"])
        except (KeyError, IndexError, ValueError) as error:
            raise ApplicationError(
                f"No hostname configured for {self._host}",
                non_retryable=True,
            ) from error

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        response = self._node.enable("show uptime")
        return int(response[0]["result"]["upTime"])

    # ------------------------------------------------------------------
    # Diagnostic commands — all via eAPI JSON-RPC (HTTPS port 443).
    # Optional features (BGP, ISIS, MPLS, MACsec, MLAG, VRRP) catch
    # CommandError and return a sentinel dict so the workflow never fails
    # on a device where that protocol is not configured.
    # ------------------------------------------------------------------

    def diag_get_version(self) -> object:
        return self._node.enable("show version detail", encoding="json")[0]["result"]

    def diag_get_interfaces(self) -> object:
        return self._node.enable("show interfaces status", encoding="json")[0]["result"]

    def diag_get_bgp_summary(self) -> object:
        try:
            return self._node.enable("show ip bgp summary", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "BGP not configured on this device"}

    def diag_get_lldp_neighbors(self) -> object:
        return self._node.enable("show lldp neighbors detail", encoding="json")[0]["result"]

    def diag_get_route_table(self) -> object:
        return self._node.enable("show ip route summary", encoding="json")[0]["result"]

    def diag_get_vlan(self) -> object:
        return self._node.enable("show vlan", encoding="json")[0]["result"]

    def diag_get_vrf(self) -> object:
        return self._node.enable("show vrf", encoding="json")[0]["result"]

    def diag_get_arp_table(self) -> object:
        return self._node.enable("show ip arp", encoding="json")[0]["result"]

    def diag_get_mac_table(self) -> object:
        return self._node.enable("show mac address-table", encoding="json")[0]["result"]

    def diag_get_inventory(self) -> object:
        return self._node.enable("show inventory", encoding="json")[0]["result"]

    def diag_get_mlag(self) -> object:
        try:
            return self._node.enable("show mlag detail", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "MLAG not configured on this device"}

    def diag_get_spanning_tree(self) -> object:
        return self._node.enable("show spanning-tree", encoding="json")[0]["result"]

    def diag_get_port_channels(self) -> object:
        return self._node.enable("show port-channel dense", encoding="json")[0]["result"]

    def diag_get_isis_neighbors(self) -> object:
        try:
            return self._node.enable("show isis neighbors", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "IS-IS not configured on this device"}

    def diag_get_isis_interfaces(self) -> object:
        try:
            return self._node.enable("show isis interface brief", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "IS-IS not configured on this device"}

    def diag_get_isis_database(self) -> object:
        try:
            return self._node.enable("show isis database", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "IS-IS not configured on this device"}

    def diag_get_mpls_interfaces(self) -> object:
        try:
            return self._node.enable("show mpls interface", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            # JSON not supported on this EOS version; return raw text
            try:
                return {
                    "output": self._node.enable("show mpls interface", encoding="text")[0][
                        "result"
                    ]["output"]
                }
            except pyeapi.eapilib.CommandError:
                return {"error": "MPLS not configured on this device"}

    def diag_get_mpls_rsvp_neighbors(self) -> object:
        try:
            return {
                "output": self._node.enable("show mpls rsvp neighbor summary", encoding="text")[0][
                    "result"
                ]["output"]
            }
        except pyeapi.eapilib.CommandError:
            return {"error": "MPLS RSVP not configured on this device"}

    def diag_get_mac_security(self) -> object:
        try:
            return self._node.enable("show mac security interface", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "MACsec not configured on this device"}

    def diag_get_mac_security_counters(self) -> object:
        try:
            return self._node.enable("show mac security counters", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "MACsec not configured on this device"}

    def diag_get_vrrp(self) -> object:
        try:
            return self._node.enable("show vrrp", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "VRRP not configured on this device"}
