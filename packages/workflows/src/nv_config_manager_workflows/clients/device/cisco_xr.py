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
"""Cisco IOS XR device connection (NETCONF over SSH, port 830, through ncclient).

The workflow semantics (normalize, diff, access guard, apply, verify, confirm)
live in :class:`CiscoIosXrConnection`. The router is reached only through the
small :class:`IosXrSession` protocol; :class:`NetconfIosXrSession` is its
NETCONF implementation.

How NVCM uses NETCONF
---------------------
Configuration stays CLI text, the form the Config Store keeps and the templates
render. It travels in the single string leaf ``cli`` of the YANG module
``Cisco-IOS-XR-cli-cfg`` (namespace ``http://cisco.com/ns/yang/Cisco-IOS-XR-cli-cfg``).
IOS XR has no "run this show command" RPC, so everything else uses YANG.

Session: an SSH login to the ``netconf`` subsystem on port 830, with the
``iosxr`` ncclient device profile. The router must advertise ``:candidate`` and
``:confirmed-commit``; otherwise the connection is refused. Passwords are tried
in turn.

Backup and drift (read-only):
  ``<get-config><source><running/></source>`` with the filter
  ``<cli xmlns=".../Cisco-IOS-XR-cli-cfg"/>``. The reply's ``cli`` leaf is the whole
  running configuration as CLI text. Nothing is locked or loaded.

Deploy diff: the intended configuration replaces the candidate and the router
computes the diff, as Junos does with ``load update``. In one session:

  1. ``<lock>`` the ``candidate`` datastore and ``<discard-changes>`` so it starts
     equal to the running configuration.
  2. ``<copy-config>`` with the intended configuration as the source, written as the
     text of the ``cli`` leaf inside ``<source><config>``, and ``candidate`` as the
     target. This replaces the whole candidate: anything the intended configuration
     omits is removed. (``<edit-config>`` replace is rejected on the ``cli`` leaf; only
     ``<copy-config>`` accepts CLI text as a whole-datastore replace.)
  3. The ``get-cli-config-diff`` action of ``Cisco-IOS-XR-cli-diff-act`` returns the
     difference between running and the candidate as CLI text, with lines marked
     ``+`` and ``-``. The ``!!`` header lines, the closing ``end`` and any ``#`` lines
     (meaning not documented) are left out; the diff is empty when no line is marked.
  4. ``<discard-changes>`` and ``<unlock>``. The router is unchanged.

Deploy (``commit_candidate_config``) repeats steps 1 to 3, requires the diff to equal
the approved diff and the access guard to pass, and then:

  4. ``<commit>``: with the commit timer, ``<commit><confirmed/>
     <confirm-timeout>N</confirm-timeout></commit>`` (N seconds), which keeps the
     candidate lock; without it, a plain ``<commit/>`` followed by ``<unlock>``. A
     failed commit discards the candidate and leaves the router unchanged.

With the commit timer, NVCM then proves the change is safe before keeping it:
a second NETCONF session logs in (the account and management path survived), the
intended configuration is copied onto the candidate again and its diff against the
now-running configuration must have no changed lines, and only then a confirming
``<commit/>`` is sent from the first session, followed by ``<unlock>``. If any check
fails nothing is confirmed, and the router rolls back on its own when the timer
expires.

Operational data (diagnostics, link state, uptime): ``<get>`` with narrow
subtree filters on the router's operational models: ``Cisco-IOS-XR-install-oper``
(version), ``Cisco-IOS-XR-shellutil-oper`` (uptime), ``Cisco-IOS-XR-ethernet-lldp-oper``
(LLDP neighbors), ``Cisco-IOS-XR-pfi-im-cmd-oper`` (interface link state) and
``Cisco-IOS-XR-ipv4-arp-oper`` (ARP table).

Actions, sent as RPCs from the router's ``-act`` YANG modules:
  ``Cisco-IOS-XR-cfgmgr-rollback-act`` ``roll-back-configuration-last`` (input
  ``count``) and ``roll-back-configuration-to`` (input ``commit-id``), which commit
  at once with no timer; and ``Cisco-IOS-XR-ztp-act`` ``ztp-initiate``.

Checked on a router running IOS XR 26.4.1: reading the ``cli`` leaf, ``<copy-config>``
of a complete configuration and of a one-block change with the diff, the operational
filters, the confirmed commit with its timer (``confirmed-commit:1.1``), and the
confirming commit. Not yet run on a router: the numbered rollback and ``ztp-initiate``
actions, whose names and inputs were read from the router's YANG modules. The
namespaces and element names are the constants below, the one place to change if
another release differs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import netaddr
from lxml import etree  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
from ncclient import manager  # type: ignore[import-untyped]
from ncclient.operations import RPCError, TimeoutExpiredError  # type: ignore[import-untyped]
from ncclient.transport.errors import (  # type: ignore[import-untyped]
    AuthenticationError,
    TransportError,
)
from ncclient.xml_ import to_ele  # type: ignore[import-untyped]
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.device import iosxr_config
from nv_config_manager_workflows.clients.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
)
from nv_config_manager_workflows.clients.device.models import (
    DeviceArpTable,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
    is_mac_address,
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings
from nv_config_manager_workflows.secret_redaction import redact_iosxr_secrets

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("ncclient").setLevel(logging.WARNING)

# ncclient raises its own errors for transport and RPC failures; sockets raise OSError.
# AuthenticationError is a TransportError, so it is mapped separately where it matters.
_TRANSPORT_ERRORS = (TransportError, RPCError, TimeoutExpiredError, OSError)

_NETCONF_PORT = 830  # IANA NETCONF-over-SSH port (RFC 6242)

# --- CLI text over NETCONF (checked on a lab router running IOS XR 26.4.1) ---------
# The router's Cisco-IOS-XR-cli-cfg YANG module is one top-level string leaf, ``cli``
# ("a set of CLI config commands"). Reading it with get-config returns the running
# configuration as CLI text; writing it with copy-config replaces the candidate with CLI
# text (edit-config accepts the leaf only as a merge). There is no exec/show RPC:
# operational commands need their own YANG path.
_CLI_CFG_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cli-cfg"
_CLI_LEAF = "cli"
_NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
_RUNNING_CONFIG_COMMAND = "show running-config"

# Standard NETCONF error tags that mean "the router does not implement this RPC".
_UNSUPPORTED_TAGS = ("operation-not-supported", "unknown-element", "unknown-namespace")

_HOSTNAME_RE = re.compile(r"^hostname\s+(?P<name>\S+)\s*$", re.MULTILINE)

# --- Operational data through YANG (reply shapes read from a lab router running 26.4.1) ---
# IOS XR has no "run this show command" RPC, so every read NVCM needs is a <get> on an
# operational model with a narrow subtree filter (whole containers are megabytes).
_FILTER_VERSION = (
    '<install xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-install-oper"><version/></install>'
)
_FILTER_UPTIME = '<system-time xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-shellutil-oper"><uptime/></system-time>'
_FILTER_LLDP = (
    '<lldp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ethernet-lldp-oper">'
    "<nodes><node><neighbors><devices/></neighbors></node></nodes></lldp>"
)
_FILTER_LINKS = '<interfaces xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-pfi-im-cmd-oper"><interface-briefs/></interfaces>'
_FILTER_ARP = (
    '<arp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ipv4-arp-oper">'
    "<nodes><node><entries/></node></nodes></arp>"
)
_LINE_UP = "im-state-up"
_ARP_UNRESOLVED_STATES = ("state-incomplete", "state-deleted")

# --- Actions (RPCs from the router's ``-act`` YANG modules; names and inputs read from a lab router) ---
_DIFF_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cli-diff-act"
_ROLLBACK_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cfgmgr-rollback-act"
_ZTP_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-ztp-act"
_ROLLBACK_LAST_RE = re.compile(r"^last (?P<count>\d+)$")


def _xml_root(xml: str, what: str) -> Any:
    try:
        return etree.fromstring(xml.encode())
    except etree.XMLSyntaxError as error:
        raise NetworkDeviceException(f"Unreadable {what} reply from the router: {error}") from error


def _parse_version(xml: str) -> str:
    """Running IOS XR version (``26.4.1.03I``) from the install-oper ``version`` container."""
    for package in _xml_root(xml, "version").iterfind(".//{*}version/{*}package"):
        if package.findtext("{*}name") == "IOS-XR" and package.findtext("{*}version"):
            return str(package.findtext("{*}version")).strip()
    raise NetworkDeviceException(
        "No IOS-XR package version in the router's reply.", non_retryable=True
    )


def _parse_uptime(xml: str) -> int:
    """Uptime in seconds from the shellutil-oper ``system-time/uptime`` container."""
    text = _xml_root(xml, "uptime").findtext(".//{*}uptime/{*}uptime")
    if text is None or not text.strip().isdigit():
        raise NetworkDeviceException("No uptime in the router's reply.")
    return int(text.strip())


def _parse_lldp(xml: str) -> dict[str, InterfaceNeighborData]:
    """Neighbors by local (physical) interface from the lldp-oper ``neighbors/devices`` container."""
    neighbors: dict[str, InterfaceNeighborData] = {}
    for device in _xml_root(xml, "LLDP").iterfind(".//{*}neighbors/{*}devices/{*}device"):
        local = device.findtext("{*}interface-name")
        neighbor = device.find("{*}lldp-neighbor")
        if not local or neighbor is None:
            continue
        if local in neighbors:
            raise NetworkDeviceException(f"Received multiple LLDP neighbors on interface {local}")
        port = (neighbor.findtext("{*}port-id-detail") or "").strip() or None
        system = (
            neighbor.findtext("{*}detail/{*}system-name") or neighbor.findtext("{*}device-id") or ""
        ).strip()
        neighbors[local] = InterfaceNeighborData(
            device_name=system or None,
            name=str(netaddr.EUI(port)) if port and is_mac_address(port) else port,
        )
    return neighbors


def _parse_link_states(xml: str) -> dict[str, bool]:
    """Line-protocol state by interface from the pfi-im-cmd-oper ``interface-briefs`` container."""
    states: dict[str, bool] = {}
    for brief in _xml_root(xml, "interface state").iterfind(
        ".//{*}interface-briefs/{*}interface-brief"
    ):
        name = brief.findtext("{*}interface-name")
        if name:
            states[name] = brief.findtext("{*}line-state") == _LINE_UP
    return states


def _parse_arp(xml: str) -> list[tuple[str, str, str]]:
    """(ip, mac, interface) for each resolved entry of the ipv4-arp-oper ``entries`` container."""
    rows: list[tuple[str, str, str]] = []
    for entry in _xml_root(xml, "ARP").iterfind(".//{*}entries/{*}entry"):
        if entry.findtext("{*}state") in _ARP_UNRESOLVED_STATES:
            continue
        ip = entry.findtext("{*}address")
        mac = entry.findtext("{*}hardware-address")
        interface = entry.findtext("{*}interface-name")
        if ip and mac and interface and int(mac.replace(":", ""), 16):
            rows.append((ip, mac, interface))
    return rows


class IosXrSession(Protocol):
    """What the connection needs from a router session (NETCONF today)."""

    def show(self, command: str, *, read_timeout: float = ...) -> str:
        """Return the running configuration for ``show running-config``; other commands are not supported."""

    def get(self, subtree: str, *, read_timeout: float = ...) -> str:
        """Return the XML reply of a <get> with the given subtree filter (operational data)."""

    def candidate_diff(self, text: str, *, timeout: float) -> str:
        """Return the router's diff of the running configuration against ``text``.

        Locks the candidate, replaces it with ``text``, reads the diff, then discards
        and unlocks, so the router is unchanged.
        """

    def load_replacement(self, text: str, *, timeout: float) -> str:
        """Lock the candidate, replace it with ``text`` and return the router's diff.

        The candidate stays locked and loaded for :meth:`commit`. On any failure the
        candidate is discarded and unlocked. Raises :class:`ConfigSyntaxException`
        when the router rejects the text.
        """

    def commit(self, *, confirm_seconds: int | None, timeout: float) -> None:
        """Commit the loaded candidate (a trial commit if ``confirm_seconds``).

        Without a timer the candidate is unlocked afterwards. A failed commit discards
        and unlocks.
        """

    def remaining_diff(self, text: str, *, timeout: float) -> str:
        """With the lock held after a trial commit: the diff of the running configuration against ``text``.

        The candidate is discarded afterwards and the lock is kept for :meth:`confirm`.
        """

    def discard(self) -> None:
        """Discard the loaded candidate and release the lock; never raises."""

    def confirm(self) -> None:
        """Confirm a pending trial commit, cancelling its rollback timer."""

    def rollback(self, selector: str) -> None:
        """Roll back with ``last <n>`` or a commit id (commits at once)."""

    def action(
        self,
        namespace: str,
        name: str,
        params: dict[str, str] | None = ...,
        *,
        read_timeout: float = ...,
    ) -> str:
        """Invoke a YANG action/RPC (``<name xmlns=namespace>`` with simple leaf inputs); return the reply XML."""

    def close(self) -> None:
        """Release the session; never raises."""


def _rpc_message(error: RPCError) -> str:
    """The router's own message for an RPC error, falling back to the tag."""
    return str(getattr(error, "message", None) or getattr(error, "tag", None) or error)


def _is_unsupported(error: RPCError) -> bool:
    return str(getattr(error, "tag", "") or "") in _UNSUPPORTED_TAGS


class NetconfIosXrSession:
    """:class:`IosXrSession` over a NETCONF-over-SSH session (ncclient, ``iosxr`` handler)."""

    def __init__(self, session: Any) -> None:
        """Wrap an open ncclient manager."""
        self._session = session

    # -- reads -------------------------------------------------------------------

    def _unsupported(self, what: str, error: RPCError) -> NetworkDeviceException:
        return NetworkDeviceException(
            f"The router rejected the {what} request ({_rpc_message(error)}). "
            "This release may not accept CLI text through the Cisco-IOS-XR-cli-cfg 'cli' leaf.",
            non_retryable=True,
        )

    @staticmethod
    def _no_netconf_path(command: str) -> NetworkDeviceException:
        return NetworkDeviceException(
            f"'{command}' has no NETCONF path yet: the router has no CLI exec RPC, so this "
            "needs its YANG operational model.",
            non_retryable=True,
        )

    def _read_running_cli(self, timeout: float) -> str:
        """The running configuration as CLI text (get-config on the ``cli`` leaf)."""
        selector = to_ele(
            f'<filter xmlns="{_NETCONF_BASE_NS}"><{_CLI_LEAF} xmlns="{_CLI_CFG_NS}"/></filter>'
        )
        previous = self._session.timeout
        self._session.timeout = timeout
        try:
            reply = self._session.get_config(source="running", filter=selector)
        except RPCError as error:
            if _is_unsupported(error):
                raise self._unsupported("running configuration", error) from error
            raise NetworkDeviceException(
                f"Reading the running configuration failed: {_rpc_message(error)}"
            ) from error
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Reading the running configuration failed: {error}"
            ) from error
        finally:
            self._session.timeout = previous
        leaf = etree.fromstring(reply.xml.encode()).find(f".//{{{_CLI_CFG_NS}}}{_CLI_LEAF}")
        return "" if leaf is None or leaf.text is None else str(leaf.text)

    def show(self, command: str, *, read_timeout: float = 60.0) -> str:
        """Run an operational command; only ``show running-config`` has a NETCONF path today."""
        if command.strip() != _RUNNING_CONFIG_COMMAND:
            raise self._no_netconf_path(command)
        return self._read_running_cli(read_timeout)

    def get(self, subtree: str, *, read_timeout: float = 60.0) -> str:
        """<get> with a subtree filter: the XML reply for one operational container."""
        selector = to_ele(f'<filter xmlns="{_NETCONF_BASE_NS}">{subtree}</filter>')
        previous = self._session.timeout
        self._session.timeout = read_timeout
        try:
            reply = self._session.get(filter=selector)
        except RPCError as error:
            raise NetworkDeviceException(
                f"Reading operational data failed: {_rpc_message(error)}"
            ) from error
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Reading operational data failed: {error}") from error
        finally:
            self._session.timeout = previous
        return str(reply.xml)

    # -- candidate datastore -----------------------------------------------------

    def _source_payload(self, text: str) -> str:
        """The ``<source>`` of a copy-config: the whole configuration as the ``cli`` leaf.

        The XML sent is ``<source><config><cli xmlns=".../Cisco-IOS-XR-cli-cfg">TEXT</cli></config></source>``.
        ncclient requires the ``<source>`` wrapper for an inline source. Building the
        element with lxml (not string concatenation) escapes ``<``, ``>`` and ``&`` in
        the configuration text.
        """
        source = etree.Element(f"{{{_NETCONF_BASE_NS}}}source", nsmap={None: _NETCONF_BASE_NS})
        config = etree.SubElement(source, f"{{{_NETCONF_BASE_NS}}}config")
        leaf = etree.SubElement(config, f"{{{_CLI_CFG_NS}}}{_CLI_LEAF}", nsmap={None: _CLI_CFG_NS})
        leaf.text = text
        return str(etree.tostring(source, encoding="unicode"))

    def _discard_and_unlock(self) -> None:
        for step in (self._session.discard_changes, lambda: self._session.unlock("candidate")):
            try:
                step()
            except Exception:  # noqa: BLE001 - cleanup must not mask the original error
                logger.debug("NETCONF cleanup step failed", exc_info=True)

    def _copy_replacement(self, text: str, timeout: float) -> None:
        """``<copy-config>`` of ``text`` onto the candidate: the whole candidate becomes ``text``.

        This is the replace. RFC 6241 section 7.3 defines ``<copy-config>`` as creating or
        replacing an entire datastore with the content of another, so the router drops
        every line that ``text`` does not contain; NVCM computes no removals itself.
        ``<edit-config>`` cannot do this: the ``cli`` leaf accepts it only as a merge, and
        both replace forms are rejected with ``operation-not-supported``.

        A rejected text (syntax the router does not parse) is a :class:`ConfigSyntaxException`.
        A router that does not implement the request is a non-retryable error. The session
        timeout is raised for the call because a 12,000-line copy takes tens of seconds.
        """
        previous = self._session.timeout
        self._session.timeout = timeout
        try:
            self._session.copy_config(source=self._source_payload(text), target="candidate")
        except RPCError as error:
            if _is_unsupported(error):
                raise self._unsupported("configuration", error) from error
            raise ConfigSyntaxException(_rpc_message(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Failed to load configuration: {error}") from error
        finally:
            self._session.timeout = previous

    def _candidate_diff_text(self, timeout: float) -> str:
        """The router's CLI diff of the candidate against running (``get-cli-config-diff``)."""
        reply = self.action(_DIFF_NS, "get-cli-config-diff", read_timeout=timeout)
        node = etree.fromstring(reply.encode()).find(f".//{{{_DIFF_NS}}}response")
        return "" if node is None or node.text is None else str(node.text)

    def load_replacement(self, text: str, *, timeout: float) -> str:
        """Lock the candidate, replace it with ``text`` and return the router's diff.

        The candidate datastore is shared by every session, so the lock keeps another
        session from editing it between the load and the commit; the diff that was
        approved is then the diff that is committed.
        """
        try:
            self._session.lock("candidate")
        except RPCError as error:
            raise NetworkDeviceException(
                f"Cannot lock the candidate configuration ({_rpc_message(error)}); "
                "another session may be configuring the router."
            ) from error
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Cannot lock the candidate: {error}") from error
        try:
            # Clear edits left by an earlier session so the candidate starts equal to running.
            self._session.discard_changes()
            # The replace: the candidate becomes the intended configuration.
            self._copy_replacement(text, timeout)
            # The router, not NVCM, computes what would change (as Junos cu.diff() does).
            return self._candidate_diff_text(timeout)
        except RPCError as error:
            self._discard_and_unlock()
            raise NetworkDeviceException(
                f"Failed to load configuration: {_rpc_message(error)}"
            ) from error
        except Exception:
            self._discard_and_unlock()
            raise

    def candidate_diff(self, text: str, *, timeout: float) -> str:
        """Router diff of the running configuration against ``text``; nothing is left loaded."""
        diff = self.load_replacement(text, timeout=timeout)
        self._discard_and_unlock()
        return diff

    def remaining_diff(self, text: str, *, timeout: float) -> str:
        """After a trial commit: the diff of the running configuration against ``text``.

        The lock from :meth:`load_replacement` is still held, so the copy-config goes
        straight onto the candidate. If the commit took effect as intended the diff has
        no changed lines; the candidate is discarded either way.
        """
        try:
            self._copy_replacement(text, timeout)
            return self._candidate_diff_text(timeout)
        finally:
            try:
                self._session.discard_changes()
            except Exception:  # noqa: BLE001 - the lock is kept; cleanup must not mask the result
                logger.debug("NETCONF discard after the verification diff failed", exc_info=True)

    def commit(self, *, confirm_seconds: int | None, timeout: float) -> None:
        """Commit the loaded candidate; with ``confirm_seconds`` the lock is kept for :meth:`confirm`."""
        previous = self._session.timeout
        self._session.timeout = timeout
        try:
            if confirm_seconds:
                self._session.commit(confirmed=True, timeout=str(confirm_seconds))
            else:
                self._session.commit()
        except _TRANSPORT_ERRORS as error:
            self._discard_and_unlock()
            raise NetworkDeviceException(
                f"Commit failed: {_rpc_message(error) if isinstance(error, RPCError) else error}"
            ) from error
        finally:
            self._session.timeout = previous
        if not confirm_seconds:
            self._discard_and_unlock()

    def discard(self) -> None:
        """Discard the loaded candidate and release the lock; never raises."""
        self._discard_and_unlock()

    def confirm(self) -> None:
        """Confirm the trial commit with a confirming ``<commit/>`` from the same session."""
        try:
            self._session.commit()
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Failed to confirm commit: {error}") from error
        self._discard_and_unlock()

    def action(
        self,
        namespace: str,
        name: str,
        params: dict[str, str] | None = None,
        *,
        read_timeout: float = 60.0,
    ) -> str:
        """Invoke a YANG RPC from an ``-act`` module and return the reply XML."""
        rpc = etree.Element(f"{{{namespace}}}{name}", nsmap={None: namespace})
        for leaf, value in (params or {}).items():
            etree.SubElement(rpc, f"{{{namespace}}}{leaf}").text = value
        previous = self._session.timeout
        self._session.timeout = read_timeout
        try:
            reply = self._session.dispatch(rpc)
        except RPCError as error:
            # The router answered and refused: retrying cannot help.
            raise NetworkDeviceException(
                f"{name} failed: {_rpc_message(error)}", non_retryable=True
            ) from error
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"{name} failed: {error}") from error
        finally:
            self._session.timeout = previous
        return str(reply.xml)

    def rollback(self, selector: str) -> None:
        """Roll back through ``Cisco-IOS-XR-cfgmgr-rollback-act`` (``last <n>`` or a commit id); commits at once."""
        last = _ROLLBACK_LAST_RE.match(selector.strip())
        if last:
            self.action(
                _ROLLBACK_NS,
                "roll-back-configuration-last",
                {"count": last["count"]},
                read_timeout=120,
            )
        else:
            self.action(
                _ROLLBACK_NS,
                "roll-back-configuration-to",
                {"commit-id": selector.strip()},
                read_timeout=120,
            )

    def close(self) -> None:
        """Close the NETCONF session (releases any lock); never raises."""
        try:
            self._session.close_session()
        except Exception:  # noqa: BLE001 - cleanup must not raise
            logger.debug("Error closing NETCONF session", exc_info=True)


class CiscoIosXrConnection(NetworkConnection):
    """Cisco IOS XR device connection."""

    # ``commit confirmed`` is given in seconds (checked on 26.4.1: a 30 s timer with
    # no confirming commit rolled back by itself); this is the one constant to change.
    _COMMIT_CONFIRM_SECONDS = COMMIT_CONFIRM_ROLLBACK_SECONDS

    # The diff activity runs under one minute (as for Junos, which uses 45 s) and the apply
    # activity under five. A 12,000-line copy-config took 36 s on a lab router and, once, over 120 s.
    _DIFF_TIMEOUT_SECONDS = 45.0
    _APPLY_TIMEOUT_SECONDS = 120.0
    _SHOW_READ_TIMEOUT_SECONDS = 60.0
    _CONN_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        host: str,
        port: int = _NETCONF_PORT,
        *,
        settings: DeviceConnectionSettings,
    ) -> None:
        """Initialize a connection; the NETCONF session opens on first use."""
        super().__init__(host, port, settings=settings)
        self._session: IosXrSession | None = None

    # ------------------------------------------------------------------
    # Session management.
    # ------------------------------------------------------------------

    def _netconf_connect(self, **kwargs: Any) -> Any:
        """Construct the NETCONF transport for host adapters to override."""
        return manager.connect(**kwargs)

    def _open_session(self) -> IosXrSession:
        """Open a new NETCONF session, rotating passwords on authentication failure."""

        def connect_with_password(password: str) -> IosXrSession:
            session = self._netconf_connect(
                host=self._host,
                port=self._port,
                username=self._username,
                password=password,
                hostkey_verify=False,
                allow_agent=False,
                look_for_keys=False,
                timeout=self._CONN_TIMEOUT_SECONDS,
                device_params={"name": "iosxr"},
            )
            missing = [
                capability
                for capability in (":candidate", ":confirmed-commit")
                if capability not in session.server_capabilities
            ]
            if missing:
                try:
                    session.close_session()
                except Exception:  # noqa: BLE001 - the capability error below is what matters
                    logger.debug("close after missing capabilities failed", exc_info=True)
                raise NetworkDeviceException(
                    f"{self._host} does not advertise NETCONF capabilities {missing}. "
                    "Check 'netconf-yang agent ssh' and the release (see the Cisco IOS XR user guide).",
                    non_retryable=True,
                )
            return NetconfIosXrSession(session)

        try:
            session: IosXrSession = self._try_passwords_with_callback(
                connect_with_password, (AuthenticationError,)
            )
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Cannot open a NETCONF session to {self._host}:{self._port}: {error}. "
                "Is NETCONF enabled on the router (see the Cisco IOS XR user guide)?"
            ) from error
        return session

    def _get_session(self) -> IosXrSession:
        if self._session is None:
            self._session = self._open_session()
        return self._session

    def close(self) -> None:
        """Close the NETCONF session if it is open."""
        session = getattr(self, "_session", None)
        if session is not None:
            session.close()
            self._session = None

    def __del__(self) -> None:
        """Best-effort cleanup of the NETCONF session on garbage collection."""
        self.close()

    def _show(self, command: str, *, read_timeout: float | None = None) -> str:
        """Run an operational command, mapping transport errors."""
        try:
            return self._get_session().show(
                command, read_timeout=read_timeout or self._SHOW_READ_TIMEOUT_SECONDS
            )
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Command '{command}' failed on {self._host}: {error}"
            ) from error

    def _get(self, subtree: str, *, read_timeout: float | None = None) -> str:
        """Read one operational container (XML), mapping transport errors."""
        try:
            return self._get_session().get(
                subtree, read_timeout=read_timeout or self._SHOW_READ_TIMEOUT_SECONDS
            )
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Reading operational data failed on {self._host}: {error}"
            ) from error

    # ------------------------------------------------------------------
    # Read operations.
    # ------------------------------------------------------------------

    def _raw_running_config(self, *, read_timeout: float | None = None) -> str:
        """Normalized running configuration with secrets intact (diff and guard only)."""
        text = iosxr_config.normalize_config(
            self._show("show running-config", read_timeout=read_timeout)
        )
        if not text.strip():
            raise NetworkDeviceException(
                f"Empty running configuration returned by {self._host}.", non_retryable=True
            )
        return text

    def get_running_configuration(self) -> str:
        """Return the running configuration with secret values redacted.

        Backups exist for attribution and drift display, not restoration, so as
        for Junos the secrets are redacted before the text leaves the session.
        """
        return redact_iosxr_secrets(self._raw_running_config())

    def get_hostname(self) -> str:
        """Get the system hostname."""
        match = _HOSTNAME_RE.search(self._raw_running_config())
        if not match:
            raise NetworkDeviceException(
                f"No hostname in the running configuration of {self._host}", non_retryable=True
            )
        return match.group("name")

    def get_running_image(self) -> str:
        """Get the running IOS XR version."""
        return _parse_version(self._get(_FILTER_VERSION))

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        return _parse_uptime(self._get(_FILTER_UPTIME))

    def get_ztp_status(self) -> str:
        """Return success when the device is reachable."""
        self.get_running_image()
        return "success"

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        """Get the LLDP neighbor of one interface."""
        return _parse_lldp(self._get(_FILTER_LLDP)).get(interface_name)

    def _link_states(self) -> dict[str, bool]:
        """Interface line-protocol state from the interface briefs."""
        return _parse_link_states(self._get(_FILTER_LINKS))

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections from LLDP."""
        return DeviceNeighborData(
            neighbors=_parse_lldp(self._get(_FILTER_LLDP)),
            link_states=self._link_states(),
        )

    def get_mac_table(self) -> DeviceMacTable:
        """A routed backbone device has no MAC table the callers use."""
        return DeviceMacTable()

    def get_arp_table(self) -> DeviceArpTable:
        """Get the ARP table (the entries of the default VRF's ARP database)."""
        table = DeviceArpTable()
        for ip, mac, interface in _parse_arp(self._get(_FILTER_ARP)):
            try:
                table.add_entry(ip, str(netaddr.EUI(mac)), interface)
            except (ValueError, netaddr.core.AddrFormatError):
                logger.warning("Invalid ARP entry from %s, skipping: %s %s", self._host, ip, mac)
        return table

    # ------------------------------------------------------------------
    # Configuration operations.
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_partial(partial: bool) -> None:
        """Reject partial applies; XR supports full desired state only."""
        if partial:
            raise NetworkDeviceException(
                "Partial configuration is not supported for CiscoIosXrConnection; "
                "supply the full desired-state configuration.",
                non_retryable=True,
            )

    def _parse_intended(self, new_configuration: str) -> list[iosxr_config.Node]:
        try:
            return iosxr_config.parse_config(new_configuration)
        except iosxr_config.IosXrConfigError as error:
            raise ConfigSyntaxException(
                f"Invalid configuration for {self._host}: {error}"
            ) from error

    def _parse_running(self) -> list[iosxr_config.Node]:
        try:
            return iosxr_config.parse_config(self._raw_running_config())
        except iosxr_config.IosXrConfigError as error:
            raise NetworkDeviceException(
                f"Cannot parse the running configuration of {self._host}: {error}"
            ) from error

    def _router_diff(self, raw: str) -> str:
        """The router's diff without its headers; empty when nothing would change."""
        diff, ignored = iosxr_config.parse_router_diff(raw)
        if ignored:
            logger.info(
                "%s listed %d '#' lines in its configuration diff; they are left out of the diff",
                self._host,
                ignored,
            )
        return diff

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Return the router's diff between the running configuration and the intended one.

        The intended configuration is copied onto the candidate (``<copy-config>``), the
        router computes the diff, and the candidate is discarded: the router is left
        unchanged. The candidate lock is held for the duration, as it is for Junos.
        """
        self._reject_partial(partial)
        self._parse_intended(new_configuration)
        try:
            raw = self._get_session().candidate_diff(
                iosxr_config.replacement_text(new_configuration),
                timeout=self._DIFF_TIMEOUT_SECONDS,
            )
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Failed to perform candidate diff on {self._host}: {error}"
            ) from error
        return self._router_diff(raw)

    def _check_access_preserved(self, intended: list[iosxr_config.Node]) -> None:
        problems = iosxr_config.check_access_preserved(
            self._parse_running(), intended, username=self._username, host=self._host
        )
        if problems:
            raise NetworkDeviceException(
                f"Refusing to apply to {self._host}: " + "; ".join(problems),
                non_retryable=True,
            )

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Replace the router's configuration with the intended one in one atomic commit.

        Order: (1) copy-config the intended text onto the locked candidate and read the
        router's diff; (2) abort unless it equals ``approved_diff`` and the lockout guard
        passes; (3) commit (a confirmed commit when ``commit_confirm``); (4) with the
        timer, verify the router and only then confirm.
        """
        self._reject_partial(partial)
        intended = self._parse_intended(new_configuration)
        text = iosxr_config.replacement_text(new_configuration)
        session = self._get_session()
        # (1) Load: lock, discard, copy-config, get-cli-config-diff. The lock stays held.
        try:
            raw = session.load_replacement(text, timeout=self._APPLY_TIMEOUT_SECONDS)
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Failed to load the candidate configuration on {self._host}: {error}"
            ) from error

        try:
            diff = self._router_diff(raw)
            if not diff:
                # Router already matches the intended configuration: nothing to commit.
                session.discard()
                return
            # (2) The router must still propose exactly what a person approved.
            if diff != approved_diff:
                raise DiffChangedException("Diff has changed since approval, aborting.")
            self._check_access_preserved(intended)
        except Exception:
            session.discard()
            raise

        # (3) Commit the loaded candidate. With a timer the router rolls back by itself
        # unless step (4) confirms, so a commit that cuts NVCM off heals on its own.
        confirm_seconds = self._COMMIT_CONFIRM_SECONDS if commit_confirm else None
        try:
            session.commit(confirm_seconds=confirm_seconds, timeout=self._APPLY_TIMEOUT_SECONDS)
        except (NetworkDeviceException, *_TRANSPORT_ERRORS) as error:
            if not commit_confirm:
                if isinstance(error, NetworkDeviceException):
                    raise
                raise NetworkDeviceException(
                    f"Failed to apply candidate configuration on {self._host}: {error}"
                ) from error
            # A failed confirmed commit may still have reached the router: a retry could
            # find an empty diff and report success while the rollback timer is running.
            raise NetworkDeviceException(
                f"Confirmed commit on {self._host} failed or its result is unknown ({error}); "
                "the router rolls back when the commit timer expires.",
                non_retryable=True,
            ) from error

        # (4) Second login, second copy-config diff, then the confirming commit.
        if commit_confirm:
            self._verify_then_confirm(text)

    def _verify_then_confirm(self, text: str) -> None:
        """Cancel the rollback timer only if the router is reachable and converged.

        Every failure here is non-retryable: a retried apply would find the
        trial configuration already running, compute an empty diff and return
        success while the timer is still counting down to a rollback.

        A fresh NETCONF login proves the account and the management path survived
        the commit; a second router diff of the intended configuration with no changed
        lines proves the router holds it. If either check fails the timer is left
        running, so the router rolls itself back.
        """
        try:
            self._check_fresh_login()
            remaining = self._router_diff(
                self._get_session().remaining_diff(text, timeout=self._APPLY_TIMEOUT_SECONDS)
            )
        except (NetworkDeviceException, *_TRANSPORT_ERRORS) as error:
            raise NetworkDeviceException(
                f"Commit on {self._host} was not confirmed ({error}); "
                "the router will roll back when the commit timer expires.",
                non_retryable=True,
            ) from error
        if remaining:
            raise NetworkDeviceException(
                f"Commit on {self._host} was not confirmed: the running configuration still "
                "differs from the intended configuration; the router will roll back when "
                "the commit timer expires.",
                non_retryable=True,
            )
        try:
            self._get_session().confirm()
        except (NetworkDeviceException, *_TRANSPORT_ERRORS) as error:
            raise NetworkDeviceException(
                f"Commit on {self._host} was not confirmed ({error}); "
                "the router will roll back when the commit timer expires.",
                non_retryable=True,
            ) from error

    def _check_fresh_login(self) -> None:
        """Open a second NETCONF session: a successful login proves the account and path survived."""
        self._open_session().close()

    # ------------------------------------------------------------------
    # Rollback to a numbered commit (``rollback configuration last <n>``).
    # ------------------------------------------------------------------

    def rollback_configuration(self, rollback_id: int = 1, *, commit_confirm: bool = True) -> None:
        """Roll back the last ``rollback_id`` commits.

        XR commits a rollback immediately and has no trial timer for it, so
        ``commit_confirm`` cannot add one; it is accepted for interface parity.
        """
        if commit_confirm:
            logger.info("IOS XR rollback has no confirm timer; rolling back %s now", self._host)
        try:
            self._get_session().rollback(f"last {int(rollback_id)}")
        except _TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Failed to roll back on {self._host}: {error}") from error

    # ------------------------------------------------------------------
    # ZTP (``Cisco-IOS-XR-ztp-act`` ``ztp-initiate``). Not yet run on a router.
    # ------------------------------------------------------------------

    def execute_ztp(self) -> None:
        """Start IOS XR ZTP so the router re-fetches its configuration.

        The NETCONF session usually ends while the router acts on it; that is success,
        as it is for the Junos zeroize.
        """
        session = self._get_session()  # a failure to connect or log in is not ZTP success
        try:
            session.action(_ZTP_NS, "ztp-initiate", read_timeout=60)
        except NetworkDeviceException as error:
            if error.non_retryable:
                raise  # the router refused the action
            logger.info(
                "NETCONF session to %s ended after ztp-initiate (%s); treating as success",
                self._host,
                error,
            )
        finally:
            self.close()

    # ------------------------------------------------------------------
    # Not supported over NETCONF (the base class raises NotImplementedError): rescue
    # configuration. It needs a file on the router to be read back and loaded, and there is
    # a copy and a delete action but no read or load action.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Diagnostics: the operational XML wrapped as JSON-serializable data.
    # ------------------------------------------------------------------

    def _diag(self, name: str, subtree: str) -> object:
        return {"data": name, "xml": self._get(subtree)}

    def diag_get_version(self) -> object:
        return self._diag("install/version", _FILTER_VERSION)

    def diag_get_interfaces(self) -> object:
        return self._diag("interface-briefs", _FILTER_LINKS)

    def diag_get_lldp_neighbors(self) -> object:
        return self._diag("lldp neighbors", _FILTER_LLDP)

    def diag_get_arp_table(self) -> object:
        return self._diag("arp entries", _FILTER_ARP)
