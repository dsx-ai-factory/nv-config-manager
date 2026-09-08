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
"""Juniper Junos device connection (PyEZ / NETCONF)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import netaddr
from jnpr.junos import Device
from jnpr.junos.exception import (
    CommitError,
    ConfigLoadError,
    ConnectAuthError,
    ConnectError,
    LockError,
    ProbeError,
    RpcError,
    UnlockError,
)
from jnpr.junos.utils.config import Config
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
    is_mac_address,
)
from nv_config_manager.temporal.common.secret_redaction import redact_junos_secrets

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


def _junos_string(container: dict[str, Any], key: str) -> str | None:
    """Return a string value (or None) from a Junos JSON response."""
    value = container.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("data")
    return str(value) if value is not None else None


def _junos_list(container: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return a repeatable Junos JSON element as a list, however it was wrapped."""
    value = container.get(key, [])
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return cast(list[dict[str, Any]], list(value))
    return []


def _mac_entry_from_junos(data: dict[str, Any]) -> DeviceMacEntry | None:
    """Return a MAC address from a Junos switching-table entry."""
    mac = _junos_string(data, "mac-address")
    interface = _junos_string(data, "mac-interfaces-list") or _junos_string(data, "mac-interface")
    if not (mac and interface):
        return None
    vlan = _junos_string(data, "mac-vlan")
    try:
        mac_std = str(netaddr.EUI(mac))
    except netaddr.core.AddrFormatError:
        logger.warning("Invalid MAC %r in Junos MAC table entry, skipping: %s", mac, data)
        return None
    # get-ethernet-switching-table-information has no per-entry last-seen time,
    # unlike Arista/Cumulus. sys.maxsize as a shared age means a duplicate MAC
    # always loses the tie-break in get_mac_table, so the later entry in
    # response order wins rather than either side being picked at random.
    return DeviceMacEntry(
        mac=mac_std,
        interface=interface.split(".")[0],
        vlan=int(vlan) if vlan and vlan.isdigit() else None,
        age=sys.maxsize,
    )


def _arp_table_from_junos(entries: list[dict[str, Any]]) -> DeviceArpTable:
    """ARP table from a Junos get-arp-table-information reply."""
    result = DeviceArpTable()
    for entry in entries:
        ip = _junos_string(entry, "ip-address")
        mac = _junos_string(entry, "mac-address")
        interface = _junos_string(entry, "interface-name")
        if not (ip and mac and interface):
            logger.warning("ARP entry missing data, skipping: %s", entry)
            continue
        try:
            result.add_entry(ip, mac, interface.split(".")[0])
        except (ValueError, netaddr.core.AddrFormatError):
            logger.warning("Invalid IP/MAC in Junos ARP entry, skipping: %s", entry)
            continue
    return result


def _neighbor_from_junos(entry: dict[str, Any]) -> InterfaceNeighborData:
    """Produce InterfaceNeighborData from a Junos LLDP neighbor entry."""
    name = _junos_string(entry, "lldp-remote-port-id")
    return InterfaceNeighborData(
        device_name=_junos_string(entry, "lldp-remote-system-name"),
        name=str(netaddr.EUI(name)) if name and is_mac_address(name) else name,
    )


class JuniperConnection(NetworkConnection):
    """Juniper Junos device connection over NETCONF (PyEZ).

    Uses junos-eznc (PyEZ) over SSH/NETCONF (default port 830). PyEZ provides
    native candidate / diff / commit-confirmed / rollback semantics, so the
    config workflow maps directly onto perform_candidate_diff and
    commit_candidate_config without hand-rolling RPC batches.

    Config is applied as the full desired state via Junos ``load update``.
    """

    # Junos commit-confirmed timeout is expressed in minutes, not seconds. Round
    # up so the rollback window is never shorter than the requested seconds.
    _COMMIT_CONFIRM_ROLLBACK_MINUTES = max(1, (COMMIT_CONFIRM_ROLLBACK_SECONDS + 59) // 60)

    # Ceiling for a single RPC / commit so a stuck device surfaces an error
    # instead of hanging the activity indefinitely.
    _RPC_TIMEOUT_SECONDS = 300

    # Exclusive *diff* ops run under activities with a 60s start_to_close.
    # Keep the PyEZ RPC deadline under that so a wedged load/diff returns and
    # closes the NETCONF session instead of outliving Temporal's timeout.
    _CONFIG_OP_TIMEOUT_SECONDS = 45

    # TCP/NETCONF open deadline separate from per-RPC timeout.
    _CONN_OPEN_TIMEOUT_SECONDS = 30

    # Substring of the RpcError Junos raises for get-ethernet-switching-table-information
    # on a device with no bridging (e.g. a pure backbone router). Any other RPC failure
    # (auth, connection, timeout) must propagate rather than read as an empty MAC table.
    _MAC_TABLE_UNSUPPORTED_MESSAGE = "l2-learning subsystem is not running"

    def __init__(
        self,
        host: str,
        port: int = 830,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize a Juniper Connection; the NETCONF session opens lazily."""
        super().__init__(host, port, username, password, site)
        self._device: Device | None = None

    # ------------------------------------------------------------------
    # Connection management — NETCONF with password rotation.
    # ------------------------------------------------------------------

    def _connect(self) -> Device:
        """Open a NETCONF session, rotating passwords on authentication failure.

        Only genuine authentication failures trigger password rotation. A probe
        failure (the port is not reachable) or any other connection error is
        raised immediately with a clear message, since retrying with a different
        password would not help.
        """

        def connect_with_password(password: str) -> Device:
            device = Device(
                host=self._host,
                user=self._username,
                passwd=password,
                port=self._port,
                gather_facts=False,
                auto_probe=5,
                conn_open_timeout=self._CONN_OPEN_TIMEOUT_SECONDS,
            )
            device.open()
            device.timeout = self._RPC_TIMEOUT_SECONDS
            return device

        try:
            return cast(
                Device,
                self._try_passwords_with_callback(connect_with_password, (ConnectAuthError,)),
            )
        except ProbeError as error:
            raise NetworkDeviceException(
                f"Cannot reach NETCONF on {self._host}:{self._port}."
            ) from error
        except ConnectError as error:
            raise NetworkDeviceException(
                f"Failed to connect to NETCONF on {self._host}:{self._port}: {error}"
            ) from error

    def _get_device(self) -> Device:
        """Return the open PyEZ device, connecting on first use."""
        if self._device is None:
            self._device = self._connect()
        return self._device

    @contextmanager
    def _device_timeout(self, seconds: int) -> Iterator[Device]:
        """Run exclusive-config work under a tighter RPC deadline, then restore."""
        device = self._get_device()
        previous = device.timeout
        device.timeout = seconds
        try:
            yield device
        finally:
            try:
                device.timeout = previous
            except Exception:  # noqa: BLE001 - cleanup must not raise
                logger.debug("Unable to restore RPC timeout on %s", self._host, exc_info=True)

    def close(self) -> None:
        """Close the NETCONF session if it is open."""
        device = getattr(self, "_device", None)
        if device is not None:
            try:
                device.close()
            except Exception:  # noqa: BLE001 - cleanup must not raise
                logger.debug("Error closing NETCONF session to %s", self._host, exc_info=True)
            self._device = None

    def __enter__(self) -> JuniperConnection:
        """Enter a context that closes the NETCONF session on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the NETCONF session when leaving the context."""
        self.close()

    def __del__(self) -> None:
        """Best-effort cleanup of the NETCONF session on garbage collection."""
        self.close()

    def _rpc(self, rpc_name: str, params: dict[str, Any] | None = None) -> Any:
        """Run an operational RPC and return its JSON (dict) representation.

        rpc_name accepts either Junos style (get-software-information) or Python
        style (get_software_information). Empty or truthy flag parameters (for
        example ``terse``) are sent as boolean flags.
        """
        device = self._get_device()
        try:
            rpc_method = getattr(device.rpc, rpc_name.replace("-", "_"))
        except AttributeError as error:
            raise NetworkDeviceException(f"Unknown RPC '{rpc_name}' for {self._host}") from error
        kwargs: dict[str, Any] = {}
        for key, value in (params or {}).items():
            flag = value in ("", "true", "True", True)
            kwargs[key.replace("-", "_")] = True if flag else value
        try:
            return rpc_method({"format": "json"}, **kwargs)
        except RpcError as error:
            raise NetworkDeviceException(
                f"RPC {rpc_name} failed on {self._host}: {error}"
            ) from error

    def _get_fact(self, name: str) -> Any:
        """Return a PyEZ fact, or None when the device does not report one.

        The PyEZ fact cache swallows RPC and transport errors and caches None in
        their place for the life of the session. An empty fact is therefore
        re-checked against a live RPC: a failure there stays retryable, while a
        device that answers gets the fact re-gathered before it is called absent.
        """
        device = self._get_device()
        value = device.facts.get(name)
        if value:
            return value
        try:
            device.rpc.get_software_information()
            device.facts_refresh(keys=name)
        except (RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to read the {name} fact from {self._host}: {error}"
            ) from error
        return device.facts.get(name)

    def _extract_configuration_output(self, element: Any) -> str | None:
        """Return text-format config from a PyEZ reply root element."""
        if element.tag == "configuration-output":
            direct: str | None = element.text
            if direct:
                return direct
        else:
            nested: str | None = element.findtext(".//configuration-output")
            if nested:
                return nested
        fallback = "".join(element.itertext()).strip()
        if not fallback:
            return None
        logger.warning(
            "Unexpected get-configuration reply shape from %s (root tag <%s>); "
            "fell back to flattened text content.",
            self._host,
            element.tag,
        )
        return fallback

    def _get_config(self, fmt: str) -> str:
        """Return the committed configuration in the requested format."""
        device = self._get_device()
        try:
            result = device.rpc.get_config(options={"format": fmt, "database": "committed"})
        except RpcError as error:
            raise NetworkDeviceException(
                f"Failed to read configuration from {self._host}: {error}"
            ) from error
        if isinstance(result, str):
            return result
        text = self._extract_configuration_output(result)
        return text if text is not None else ""

    def _load_full_config(self, cu: Config, new_configuration: str) -> None:
        """Load the complete desired-state config via Junos ``load update``."""
        try:
            cu.load(new_configuration, format="text", update=True)
        except ConfigLoadError as error:
            raise ConfigSyntaxException(
                f"Invalid configuration for {self._host}: {error}"
            ) from error

    @staticmethod
    def _discard_candidate(cu: Config, host: str) -> None:
        """Best-effort rollback."""
        try:
            cu.rollback()
        except Exception:  # noqa: BLE001 - cleanup must not mask the original error
            logger.debug("Failed to discard candidate config on %s", host, exc_info=True)

    @staticmethod
    def _reject_partial(partial: bool) -> None:
        """Reject partial applies; JuniperConnection supports full desired state only."""
        if partial:
            raise NetworkDeviceException(
                "Partial configuration is not supported for JuniperConnection; "
                "supply the full desired-state configuration.",
                non_retryable=True,
            )

    def _commit(self, cu: Config, commit_confirm: bool) -> None:
        """Commit the candidate, optionally with a rollback timer."""
        if commit_confirm:
            cu.commit(
                confirm=self._COMMIT_CONFIRM_ROLLBACK_MINUTES,
                timeout=self._RPC_TIMEOUT_SECONDS,
            )
        else:
            cu.commit(timeout=self._RPC_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Read operations.
    # ------------------------------------------------------------------

    def get_running_configuration(self) -> str:
        """Return the running configuration in hierarchical (curly-brace) text.

        Backups exist for attribution and drift detection, not restoration --
        intended state is rolled forward through Nautobot and re-applied, never
        loaded back from a stored backup. So, as with Arista's ``sanitized``
        running-config and Cumulus's applied-config read, secret values are
        redacted before this leaves the device session.
        """
        return redact_junos_secrets(self._get_config("text").strip() + "\n")

    def get_configuration_text(self) -> str:
        """Return the running configuration in hierarchical (curly-brace) text."""
        return redact_junos_secrets(self._get_config("text"))

    def get_hostname(self) -> str:
        """Get the system hostname."""
        hostname = self._get_fact("hostname")
        if not hostname:
            raise ApplicationError(
                f"No hostname returned for {self._host}",
                non_retryable=True,
            )
        return str(hostname)

    def get_running_image(self) -> str:
        """Get the running Junos version on the device."""
        version = self._get_fact("version")
        if not version:
            raise NetworkDeviceException(
                f"Unable to determine running image on {self._host}.", non_retryable=True
            )
        return str(version)

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        data = self._rpc("get-system-uptime-information")
        try:
            uptime = data["system-uptime-information"][0]["uptime-information"][0]["up-time"][0]
            return int(uptime["attributes"]["junos:seconds"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise NetworkDeviceException(f"Unable to determine uptime on {self._host}.") from error

    def execute_ztp(self) -> None:
        """Factory-reset the device so it re-runs Junos DHCP/HTTP ZTP."""
        device = self._get_device()
        try:
            device.rpc.request_system_zeroize()
        except ConnectError:
            # Zeroize tears down NETCONF as the device reboots; that is success.
            logger.info(
                "NETCONF session ended after zeroize on %s; treating as success",
                self._host,
            )
        finally:
            self.close()

    def get_ztp_status(self) -> str:
        """Return success when the device is reachable after ZTP."""
        self.get_running_image()
        return "success"

    def _get_lldp_neighbor_entries(self) -> list[dict[str, Any]]:
        """Return raw lldp-neighbor-information entries from the device."""
        data = self._rpc("get-lldp-neighbors-information")
        root = _junos_list(data, "lldp-neighbors-information")
        return _junos_list(root[0], "lldp-neighbor-information") if root else []

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        """Get the raw LLDP data for a given interface."""
        entries = [
            entry
            for entry in self._get_lldp_neighbor_entries()
            if _junos_string(entry, "lldp-local-port-id") == interface_name
        ]
        if len(entries) > 1:
            raise NetworkDeviceException(
                f"Received multiple LLDP neighbors on interface {interface_name} from {self._host}"
            )
        return _neighbor_from_junos(entries[0]) if entries else None

    def _get_interface_link_states(self) -> dict[str, bool]:
        """Return physical interface oper-status keyed by interface name."""
        data = self._rpc("get-interface-information", {"terse": True})
        root = _junos_list(data, "interface-information")
        if not root:
            return {}
        link_states = {}
        for physical in _junos_list(root[0], "physical-interface"):
            name = _junos_string(physical, "name")
            oper_status = _junos_string(physical, "oper-status")
            if name and oper_status:
                link_states[name] = oper_status == "up"
        return link_states

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections from LLDP."""
        neighbors: dict[str, InterfaceNeighborData] = {}
        for entry in self._get_lldp_neighbor_entries():
            interface = _junos_string(entry, "lldp-local-port-id")
            if not interface:
                continue
            if interface in neighbors:
                raise NetworkDeviceException(
                    f"Received multiple LLDP neighbors on interface {interface} from {self._host}"
                )
            neighbors[interface] = _neighbor_from_junos(entry)

        return DeviceNeighborData(
            neighbors=neighbors, link_states=self._get_interface_link_states()
        )

    def get_mac_table(self) -> DeviceMacTable:
        """Get the device MAC table."""
        try:
            data = self._rpc("get-ethernet-switching-table-information")
        except NetworkDeviceException as error:
            cause_message = getattr(error.__cause__, "message", None) or str(error)
            if self._MAC_TABLE_UNSUPPORTED_MESSAGE not in cause_message:
                raise
            logger.debug(
                "No Ethernet switching table on %s; treating the MAC table as empty.",
                self._host,
            )
            return DeviceMacTable()

        result = DeviceMacTable()
        root = _junos_list(data, "ethernet-switching-table-information")
        table = _junos_list(root[0], "ethernet-switching-table") if root else []
        entries = _junos_list(table[0], "mac-table-entry") if table else []
        for raw_entry in entries:
            mac_entry = _mac_entry_from_junos(raw_entry)
            if mac_entry is None:
                continue
            if mac_entry.mac in result.by_mac:
                logger.warning(
                    "Duplicate MAC address %s on device %s: using newest entry",
                    mac_entry.mac,
                    self._host,
                )
                if mac_entry.age > result.by_mac[mac_entry.mac].age:
                    continue
            result.by_mac[mac_entry.mac] = mac_entry
            result.by_interface.setdefault(mac_entry.interface, [])
            if mac_entry.mac not in result.by_interface[mac_entry.interface]:
                result.by_interface[mac_entry.interface].append(mac_entry.mac)
        return result

    def get_arp_table(self) -> DeviceArpTable:
        """Get the device ARP table."""
        data = self._rpc("get-arp-table-information")
        root = _junos_list(data, "arp-table-information")
        entries = _junos_list(root[0], "arp-table-entry") if root else []
        return _arp_table_from_junos(entries)

    # ------------------------------------------------------------------
    # Configuration operations.
    # ------------------------------------------------------------------

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the full desired-state candidate and return the diff versus active."""
        self._reject_partial(partial)
        try:
            with (
                self._device_timeout(self._CONFIG_OP_TIMEOUT_SECONDS) as device,
                Config(device, mode="exclusive") as cu,
            ):
                try:
                    self._load_full_config(cu, new_configuration)
                    diff = cu.diff()
                    cu.rollback()
                except Exception:
                    # Discard a partial load so the next exclusive session is clean.
                    self._discard_candidate(cu, self._host)
                    raise
        except ConfigSyntaxException:
            raise
        except (LockError, UnlockError, RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to perform candidate diff on {self._host}: {error}"
            ) from error
        return diff or ""

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the full desired-state candidate and commit."""
        self._reject_partial(partial)
        device = self._get_device()
        try:
            with Config(device, mode="exclusive") as cu:
                self._load_full_config(cu, new_configuration)
                diff = cu.diff()
                if diff and diff != approved_diff:
                    cu.rollback()
                    raise DiffChangedException("Diff has changed since approval, aborting.")
                if diff:
                    self._commit(cu, commit_confirm)
                else:
                    cu.rollback()
        except (ConfigSyntaxException, DiffChangedException):
            raise
        except (CommitError, LockError, UnlockError, RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to apply candidate configuration on {self._host}: {error}"
            ) from error

        if commit_confirm:
            # Confirm the pending commit to cancel the rollback timer.
            self._confirm_commit()

    def _confirm_commit(self) -> None:
        """Confirm a pending commit-confirmed, cancelling its rollback timer."""
        device = self._get_device()
        try:
            with Config(device, mode="exclusive") as cu:
                cu.commit(timeout=self._RPC_TIMEOUT_SECONDS)
        except (CommitError, LockError, UnlockError, RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to confirm commit on {self._host}: {error}"
            ) from error

    # ------------------------------------------------------------------
    # Rollback to a numbered revision (Junos keeps rollback 0-49).
    # ------------------------------------------------------------------

    def get_rollback_diff(self, rollback_id: int = 1) -> str:
        """Return the diff between the active config and a numbered rollback."""
        try:
            with (
                self._device_timeout(self._CONFIG_OP_TIMEOUT_SECONDS) as device,
                Config(device, mode="exclusive") as cu,
            ):
                try:
                    diff = cu.diff(rb_id=rollback_id)
                    cu.rollback()
                except Exception:
                    self._discard_candidate(cu, self._host)
                    raise
        except (LockError, UnlockError, RpcError, ConnectError, ValueError) as error:
            raise NetworkDeviceException(
                f"Failed to read rollback {rollback_id} diff on {self._host}: {error}"
            ) from error
        return diff or ""

    def rollback_configuration(self, rollback_id: int = 1, *, commit_confirm: bool = True) -> None:
        """Roll back to a numbered rollback revision and commit (instant rollback)."""
        device = self._get_device()
        try:
            with Config(device, mode="exclusive") as cu:
                cu.rollback(rb_id=rollback_id)
                if cu.diff():
                    self._commit(cu, commit_confirm)
                else:
                    cu.rollback()
        except (CommitError, LockError, UnlockError, RpcError, ConnectError, ValueError) as error:
            raise NetworkDeviceException(
                f"Failed to roll back to revision {rollback_id} on {self._host}: {error}"
            ) from error
        if commit_confirm:
            self._confirm_commit()

    # ------------------------------------------------------------------
    # Rescue configuration (a named checkpoint of the active config).
    # ------------------------------------------------------------------

    def save_rescue_configuration(self) -> None:
        """Save the current active config as the rescue checkpoint."""
        device = self._get_device()
        try:
            Config(device).rescue(action="save")
        except (RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to save rescue configuration on {self._host}: {error}"
            ) from error

    def get_rescue_configuration(self) -> str | None:
        """Return the saved rescue configuration, or None if none is set."""
        device = self._get_device()
        try:
            got = device.rpc.get_rescue_information(format="text")
        except RpcError as error:
            if self._is_rescue_absent(error):
                return None
            raise NetworkDeviceException(
                f"Failed to read rescue configuration on {self._host}: {error}"
            ) from error
        except ConnectError as error:
            raise NetworkDeviceException(
                f"Failed to read rescue configuration on {self._host}: {error}"
            ) from error
        text = self._extract_configuration_output(got)
        return text if text else None

    @staticmethod
    def _is_rescue_absent(error: RpcError) -> bool:
        """True when Junos reports that no rescue configuration is defined."""
        message = str(error).lower()
        return "rescue" in message and (
            "does not exist" in message
            or "not found" in message
            or "no rescue" in message
            or "rescue configuration is not set" in message
        )

    def delete_rescue_configuration(self) -> None:
        """Delete the saved rescue configuration."""
        device = self._get_device()
        try:
            Config(device).rescue(action="delete")
        except (RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to delete rescue configuration on {self._host}: {error}"
            ) from error

    def rollback_to_rescue(self, *, commit_confirm: bool = True) -> None:
        """Roll back to the saved rescue configuration and commit."""
        device = self._get_device()
        try:
            with Config(device, mode="exclusive") as cu:
                try:
                    loaded = cu.rescue(action="reload")
                    if not loaded:
                        raise NetworkDeviceException(
                            f"No rescue configuration on {self._host}.",
                            non_retryable=True,
                        )
                    if cu.diff():
                        self._commit(cu, commit_confirm)
                    else:
                        cu.rollback()
                except Exception:
                    self._discard_candidate(cu, self._host)
                    raise
        except NetworkDeviceException:
            raise
        except (CommitError, LockError, UnlockError, RpcError, ConnectError) as error:
            raise NetworkDeviceException(
                f"Failed to roll back to rescue configuration on {self._host}: {error}"
            ) from error
        if commit_confirm:
            self._confirm_commit()

    # ------------------------------------------------------------------
    # Diagnostic commands — operational RPCs returning JSON.
    # ------------------------------------------------------------------

    def diag_get_version(self) -> object:
        return self._rpc("get-software-information")

    def diag_get_interfaces(self) -> object:
        return self._rpc("get-interface-information", params={"terse": True})

    def diag_get_lldp_neighbors(self) -> object:
        return self._rpc("get-lldp-neighbors-information")

    def diag_get_route_table(self) -> object:
        return self._rpc("get-route-summary-information")

    def diag_get_arp_table(self) -> object:
        return self._rpc("get-arp-table-information")
