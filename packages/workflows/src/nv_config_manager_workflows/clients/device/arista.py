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

import gzip
import io
import re
import ssl
import sys
import tarfile
import time
from collections.abc import Callable
from typing import NoReturn, cast
from uuid import uuid4

import pyeapi
import pyeapi.eapilib
from nv_config_manager_logging import LogCategory, get_logger
from pyeapi.client import Node
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
    ZTPRollbackException,
)
from nv_config_manager_workflows.clients.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)

# Lab and engineering builds append the build to the release:
# "4.34.3M-44102239.4343M (engineering build)" is release 4.34.3M.
_ENGINEERING_BUILD = re.compile(r"-\d+\.\w+\s+\(engineering build\)$")
_BANNER_COMMAND = re.compile(r"banner \w+")

# Below the 600 s tech-support activity timeout.
_TECH_SUPPORT_TIMEOUT_SECONDS = 540
# Either file keeps EOS out of ZeroTouch at boot: a startup-config, or the
# zerotouch-config that "zerotouch cancel" or "zerotouch disable" leaves behind.
_ZTP_BLOCKING_FILES = ("startup-config", "zerotouch-config")
# execute_ztp renames each file aside to this name and renames it back if the reload
# does not happen. The EOS boot script removes the renamed files after ZeroTouch.
_ZTP_BACKUP_SUFFIX = ".nvcm-backup"
# execute_ztp schedules the reload so EOS answers before it goes down. When the answer
# is lost, the files are renamed back and the reload cancelled before it fires.
_ZTP_RELOAD_DELAY = "00:05"
# Timeout for every request execute_ztp sends. At most ten requests (two renames, the
# reload, two files at three each to restore, the cancel) take 200 s, inside both the
# reload delay and the five-minute activity deadline.
_ZTP_REQUEST_TIMEOUT_SECONDS = 20


def _strip_session_paths(diff: str) -> str:
    """Remove session paths in linear time, keeping matches on a single line."""
    suffix = "-session-config"
    parts = diff.split(suffix)
    for index in range(len(parts) - 1):
        part = parts[index]
        # Only a prefix after the last newline can pair with this suffix.
        start = part.find("session:/", part.rfind("\n") + 1)
        parts[index] = part[:start] if start >= 0 else part + suffix
    return "".join(parts)


def _unrestored_note(names: list[str]) -> str:
    """Name each blocking file that may still be moved aside, for an error message."""
    return "".join(
        f"; flash:{name} may still be at flash:{name}{_ZTP_BACKUP_SUFFIX}" for name in names
    )


class _PasswordFallbackNode(Node):
    """A pyeapi node that retries a rejected request with the other known passwords.

    pyeapi sends nothing when it connects, so a stale password only shows up as
    a 401 on the first command. A request that eAPI rejects as unauthorized is
    retried with the remaining candidate passwords, newest first; the one that
    works becomes the node's password for the rest of the session. A pass in
    which every password fails restores the original one and is not repeated
    for the rest of the session, since each failed login counts towards the
    account's lockout.
    """

    def __init__(
        self,
        connection: object,
        *,
        host: str,
        username: str,
        passwords: Callable[[], list[str]],
        on_password: Callable[[str], None],
        **kwargs: object,
    ) -> None:
        super().__init__(connection, **kwargs)
        self._fallback_host = host
        self._fallback_username = username
        self._candidate_passwords = passwords
        self._on_password = on_password
        self._fallback_spent = False

    def run_commands(self, commands: object, *args: object, **kwargs: object) -> object:
        try:
            return super().run_commands(commands, *args, **kwargs)
        except pyeapi.eapilib.ConnectionError as exc:
            if "unauthorized" not in str(exc).lower():
                raise
            unauthorized = exc
        if self._fallback_spent:
            raise NetworkDeviceException(
                f"eAPI on {self._fallback_host} rejected the password for "
                f"{self._fallback_username}; the other known passwords already failed"
            ) from unauthorized
        original = self._enablepwd
        for password in self._candidate_passwords():
            self._use_password(password)
            try:
                result = super().run_commands(commands, *args, **kwargs)
            except pyeapi.eapilib.CommandError:
                # eAPI accepted the password; a command in the request failed.
                self._password_worked(password)
                raise
            except pyeapi.eapilib.ConnectionError as exc:
                if "unauthorized" in str(exc).lower():
                    continue
                # Whether this password works is unknown, so go back to the one in use.
                self._use_password(original)
                raise
            self._password_worked(password)
            return result
        self._fallback_spent = True
        self._use_password(original)
        raise NetworkDeviceException(
            f"eAPI on {self._fallback_host} rejected every known password for "
            f"{self._fallback_username}"
        ) from unauthorized

    def _use_password(self, password: str | None) -> None:
        self.connection.authentication(self._fallback_username, password)
        self.enable_authentication(password)

    def _password_worked(self, password: str) -> None:
        logger.warning(
            "eAPI on %s rejected the password in use for %s; another known password worked",
            self._fallback_host,
            self._fallback_username,
        )
        self._on_password(password)


class AristaConnection(NetworkConnection):
    """Arista Device Connection."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        *,
        settings: DeviceConnectionSettings,
    ) -> None:
        """Initialize an Arista Connection."""
        super().__init__(host, port, settings=settings)
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
            connection = pyeapi.connect(
                transport="https",
                host=self._host,
                port=self._port,
                username=self._username,
                password=password,
                context=ctx,
                timeout=300,
            )
            self._working_password = password
            return _PasswordFallbackNode(
                connection,
                host=self._host,
                username=self._username,
                passwords=self._other_passwords,
                on_password=self._remember_password,
                enablepwd=password,
            )

        # Connecting sends nothing, so this only picks the first candidate; the
        # node falls back to the others on the first unauthorized request.
        return self._try_passwords_with_callback(
            connect_with_password,
            (pyeapi.eapilib.ConnectionError, pyeapi.eapilib.CommandError),
        )

    def _other_passwords(self) -> list[str]:
        """Candidate passwords other than the one in use, newest first."""
        return [p for p in self._passwords_to_try if p != self._working_password]

    def _remember_password(self, password: str) -> None:
        self._working_password = password

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
        matches: list[tuple[str, str]] = []
        remaining: list[str] = []
        cursor = 0
        while command := _BANNER_COMMAND.search(new_configuration, cursor):
            end = new_configuration.find("EOF", command.end())
            command_end = command.end()
            if end < 0:
                # A type can contain EOF. Use its last EOF only if no terminator
                # follows the complete word, keeping at least one type character.
                end = new_configuration.rfind(
                    "EOF", command.start() + len("banner ") + 1, command.end()
                )
                if end < 0:
                    break
                command_end = end
            remaining.append(new_configuration[cursor : command.start()])
            matches.append(
                (
                    new_configuration[command.start() : command_end],
                    new_configuration[command_end:end],
                )
            )
            cursor = end + len("EOF")
            if command_end != command.end():
                break  # No EOF remains after this command.
        remaining.append(new_configuration[cursor:])
        return "".join(remaining), matches

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
        return _strip_session_paths(diff_a) == _strip_session_paths(diff_b)

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
                # commit timer HH:MM:SS = roll back unless confirmed
                m, s = divmod(COMMIT_CONFIRM_ROLLBACK_SECONDS, 60)
                h, m = divmod(m, 60)
                timer_str = f"{h:02d}:{m:02d}:{s:02d}"
                self._node.run_commands(
                    [
                        f"configure session {self._session_id}",
                        f"commit timer {timer_str}",
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

    def get_running_image(self) -> str:
        """Get the running EOS release, for example ``4.34.3M``.

        Lab and engineering builds report the build after the release; only the
        release is returned, so it compares equal to the image store's version.
        """
        try:
            version = str(self._node.enable("show version")[0]["result"]["version"])
        except (KeyError, IndexError) as error:
            raise NetworkDeviceException(
                f"No EOS version reported by {self._host}", non_retryable=True
            ) from error
        return _ENGINEERING_BUILD.sub("", version)

    def get_tech_support_bundle(
        self, heartbeat_fn: Callable[[], None] | None = None
    ) -> tuple[bytes, str]:
        """Collect ``show tech-support`` and return it as a gzipped tarball.

        EOS produces the report as command output, so it is read over eAPI and
        packed in memory; nothing is written to the device. The tarball keeps the
        format the tech-support download and ticket attachment expect.

        heartbeat_fn, if provided, is called every ~15 s while EOS generates the
        report, keeping the Temporal heartbeat window satisfied.

        Returns:
            (gzipped tarball bytes, one-line summary of the collection)
        """
        start = time.monotonic()
        transport = self._node.connection.transport
        default_timeout = transport.timeout
        stop_hb, hb = self._start_heartbeat_thread(heartbeat_fn)
        if heartbeat_fn:
            try:
                heartbeat_fn()
            except Exception:
                pass
        transport.timeout = _TECH_SUPPORT_TIMEOUT_SECONDS
        try:
            response = self._node.enable("show tech-support", encoding="text")
        except pyeapi.eapilib.ConnectionError as exc:
            if isinstance(exc.__context__, TimeoutError):
                elapsed = time.monotonic() - start
                logger.warning("show tech-support on %s timed out after %.0fs", self._host, elapsed)
                raise NetworkDeviceException(
                    f"show tech-support timed out on {self._host} after {elapsed:.0f}s"
                ) from exc
            raise NetworkDeviceException(f"show tech-support failed on {self._host}") from exc
        except pyeapi.eapilib.CommandError as exc:
            raise NetworkDeviceException(f"show tech-support failed on {self._host}") from exc
        finally:
            transport.timeout = default_timeout
            stop_hb.set()
            hb.join(timeout=2)

        try:
            report = cast(str, response[0]["result"]["output"]).encode()
        except (KeyError, IndexError, TypeError) as exc:
            raise NetworkDeviceException(
                f"show tech-support returned no output on {self._host}"
            ) from exc
        member = tarfile.TarInfo("show-tech-support.txt")
        member.size = len(report)
        member.mtime = int(time.time())
        bundle = io.BytesIO()
        # The constructors, not tarfile.open(): the package boundary test rejects any call
        # named open, and nothing here touches a file.
        with (
            gzip.GzipFile(fileobj=bundle, mode="wb") as compressed,
            tarfile.TarFile(fileobj=compressed, mode="w") as tar,
        ):
            tar.addfile(member, io.BytesIO(report))

        summary = (
            f"show tech-support collected from {self._host}: {len(report)} bytes "
            f"in {time.monotonic() - start:.0f}s"
        )
        return bundle.getvalue(), summary

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

    def diag_get_platform_environment(self) -> object:
        # Each sensor group is queried on its own so a platform without one
        # (virtual EOS has no sensors) reports that group instead of failing.
        sections = {
            "cooling": "show system environment cooling",
            "temperature": "show system environment temperature",
            "power": "show system environment power",
        }
        environment: dict[str, object] = {}
        for section, command in sections.items():
            try:
                environment[section] = self._node.enable(command, encoding="json")[0]["result"]
            except pyeapi.eapilib.CommandError:
                environment[section] = {"error": f"No {section} data available on this device"}
        return environment

    def diag_get_platform_transceiver(self) -> object:
        try:
            return self._node.enable("show interfaces transceiver", encoding="json")[0]["result"]
        except pyeapi.eapilib.CommandError:
            return {"error": "Transceiver data not available on this device"}

    def diag_get_interface_counters(self) -> object:
        counters, errors = self._node.enable(
            ["show interfaces counters", "show interfaces counters errors"], encoding="json"
        )
        return {"counters": counters["result"], "errors": errors["result"]}

    def diag_get_interface_mac(self) -> object:
        interfaces = self._node.enable("show interfaces", encoding="json")[0]["result"][
            "interfaces"
        ]
        return {
            name: {key: data[key] for key in ("physicalAddress", "burnedInAddress") if key in data}
            for name, data in interfaces.items()
            if "physicalAddress" in data or "burnedInAddress" in data
        }

    # ------------------------------------------------------------------
    # Zero-touch provisioning. EOS runs ZeroTouch when it boots without a
    # startup-config: DHCP option 67 names the script it downloads and runs.
    # ------------------------------------------------------------------

    def execute_ztp(self) -> None:
        """Factory-reset the switch so it re-runs ZeroTouch on its next boot.

        Moving the startup-config and any zerotouch-config aside, then
        reloading, is what makes EOS enter ZeroTouch; the running configuration
        is deliberately not saved. The files are renamed rather than deleted,
        and renamed back if the reset does not go through, so a later unrelated
        reboot does not put the switch into ZeroTouch. The reload is scheduled
        a short time ahead so EOS confirms it before going down. Without that
        confirmation the reload may or may not be scheduled, so the files are
        renamed back and the reload is cancelled. The rollback checks both files,
        so it also restores a file that an earlier attempt of the activity moved.
        If a file cannot be put back, the error is not retryable.
        """
        transport = self._node.connection.transport
        default_timeout = transport.timeout
        transport.timeout = _ZTP_REQUEST_TIMEOUT_SECONDS
        try:
            for name in _ZTP_BLOCKING_FILES:
                try:
                    self._node.enable(f"rename flash:{name} flash:{name}{_ZTP_BACKUP_SUFFIX}")
                except pyeapi.eapilib.CommandError as exc:
                    if "No such file" in str(exc.command_error):
                        continue  # not there, or moved aside by an earlier attempt
                    self._undo_ztp_reset(f"could not move flash:{name} aside", exc)
                except (pyeapi.eapilib.ConnectionError, NetworkDeviceException) as exc:
                    # The rename may have run before the connection dropped. A
                    # NetworkDeviceException is eAPI rejecting every known password.
                    self._undo_ztp_reset(f"could not move flash:{name} aside", exc)

            try:
                # strict: never resend this command with another encoding.
                self._node.enable(
                    f"reload in {_ZTP_RELOAD_DELAY} force", encoding="text", strict=True
                )
            except (
                pyeapi.eapilib.CommandError,
                pyeapi.eapilib.ConnectionError,
                NetworkDeviceException,
            ) as exc:
                self._undo_ztp_reset("did not confirm the reload", exc)
        finally:
            transport.timeout = default_timeout
        logger.info("%s reloads into ZeroTouch in %s", self._host, _ZTP_RELOAD_DELAY)

    def _undo_ztp_reset(self, failure: str, exc: Exception) -> NoReturn:
        """Rename the files back and cancel any scheduled reload, then raise.

        The cancel also covers a reload that an earlier attempt scheduled without
        getting the answer. A file that cannot be put back makes the error
        ZTPRollbackException, which is not retryable.
        """
        unrestored = self._restore_ztp_files()
        self._cancel_reload()
        message = f"{self._host} {failure}: {exc}{_unrestored_note(unrestored)}"
        if unrestored:
            raise ZTPRollbackException(message) from exc
        raise NetworkDeviceException(message) from exc

    def _cancel_reload(self) -> None:
        try:
            self._node.enable("reload cancel", encoding="text", strict=True)
        except (
            pyeapi.eapilib.CommandError,
            pyeapi.eapilib.ConnectionError,
            NetworkDeviceException,
        ):
            logger.warning("Could not cancel a possibly scheduled reload on %s", self._host)

    def _restore_ztp_files(self) -> list[str]:
        """Rename back the files execute_ztp moved aside, after a reset that did not happen.

        A file is renamed back only when the original is gone and the backup is
        there, which is the state a move leaves, whether this attempt or an
        earlier one made it, and whether or not its response arrived. A backup
        never replaces a file that is still in place. Returns the files that
        could not be checked or renamed back, so the caller can name them.
        """
        unrestored: list[str] = []
        for name in _ZTP_BLOCKING_FILES:
            backup = f"{name}{_ZTP_BACKUP_SUFFIX}"
            try:
                if self._flash_file_exists(name) or not self._flash_file_exists(backup):
                    continue
                self._node.enable(f"rename flash:{backup} flash:{name}")
            except (
                pyeapi.eapilib.CommandError,
                pyeapi.eapilib.ConnectionError,
                NetworkDeviceException,
            ):
                logger.error(
                    "Could not restore flash:%s on %s; it may still be at flash:%s",
                    name,
                    self._host,
                    backup,
                )
                unrestored.append(name)
        return unrestored

    def _flash_file_exists(self, name: str) -> bool:
        try:
            self._node.enable(f"show file information flash:{name}")
        except pyeapi.eapilib.CommandError as exc:
            if "No such file" in str(exc.command_error):
                return False
            raise
        return True

    def get_ztp_status(self) -> str:
        """Report ``success`` once the switch runs its provisioned configuration.

        While ZeroTouch runs there is no eAPI to answer, so the caller sees
        connection errors. Once eAPI answers, ``show zerotouch`` reports
        ``disabled`` for a switch booted from a startup-config.
        """
        mode = str(self._node.enable("show zerotouch")[0]["result"].get("mode", ""))
        return "success" if mode == "disabled" else mode or "unknown"
