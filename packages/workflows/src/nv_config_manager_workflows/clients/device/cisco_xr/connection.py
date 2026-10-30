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
small :class:`~.netconf.IosXrSession` protocol; :class:`~.netconf.NetconfIosXrSession`
is its NETCONF implementation. The text handling is in :mod:`.config` and the
operational filters and parsers are in :mod:`.operational`.

How NVCM uses NETCONF
---------------------
Configuration stays CLI text, the form the Config Store keeps and the templates
render. It travels in the single string leaf ``cli`` of the YANG module
``Cisco-IOS-XR-cli-cfg`` (namespace ``http://cisco.com/ns/yang/Cisco-IOS-XR-cli-cfg``).
IOS XR has no "run this show command" RPC, so everything else uses YANG.

Session: an SSH login to the ``netconf`` subsystem on port 830, with the
``iosxr`` ncclient device profile. When the session opens, each side sends a NETCONF
``<hello>`` listing its capabilities. The router must list two NETCONF capabilities in
its ``<hello>`` to NVCM: ``:candidate`` (a candidate datastore to load and diff
without touching running) and ``:confirmed-commit`` (a commit that rolls back on its
own unless confirmed). If either is missing the connection is refused. Passwords are
tried in turn.

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
     ``+`` and ``-``; a line changed in place is listed as a pair marked ``#`` (the old
     line, then the new one). The ``!!`` header lines and the closing ``end`` are left
     out; the diff is empty when no line is marked ``+``, ``-`` or ``#``.
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
import time
from typing import Any

import netaddr
from ncclient import manager  # type: ignore[import-untyped]
from ncclient.transport.errors import AuthenticationError  # type: ignore[import-untyped]
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager_workflows.clients.device.cisco_xr import config
from nv_config_manager_workflows.clients.device.cisco_xr.netconf import (
    NETCONF_PORT,
    TRANSPORT_ERRORS,
    ZTP_NS,
    IosXrSession,
    NetconfIosXrSession,
)
from nv_config_manager_workflows.clients.device.cisco_xr.operational import (
    FILTER_ARP,
    FILTER_LINKS,
    FILTER_LLDP,
    FILTER_UPTIME,
    FILTER_VERSION,
    parse_arp,
    parse_link_states,
    parse_lldp,
    parse_uptime,
    parse_version,
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
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings
from nv_config_manager_workflows.secret_redaction import redact_iosxr_secrets

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("ncclient").setLevel(logging.WARNING)

_HOSTNAME_RE = re.compile(r"^hostname\s+(?P<name>\S+)\s*$", re.MULTILINE)


class CiscoIosXrConnection(NetworkConnection):
    """Cisco IOS XR device connection."""

    # ``commit confirmed`` is given in seconds (checked on 26.4.1: a 30 s timer with
    # no confirming commit rolled back by itself); this is the one constant to change.
    _COMMIT_CONFIRM_SECONDS = COMMIT_CONFIRM_ROLLBACK_SECONDS
    # Do not send the confirming commit with less than this left on the rollback timer.
    _CONFIRM_MARGIN_SECONDS = 15.0

    # The diff activity runs under one minute (as for Junos, which uses 45 s) and the apply
    # activity under five. A 12,000-line copy-config took 36 s on a lab router and, once, over 120 s.
    _DIFF_TIMEOUT_SECONDS = 45.0
    _APPLY_TIMEOUT_SECONDS = 120.0
    _SHOW_READ_TIMEOUT_SECONDS = 60.0
    _CONN_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        host: str,
        port: int = NETCONF_PORT,
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
        except TRANSPORT_ERRORS as error:
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
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Command '{command}' failed on {self._host}: {error}"
            ) from error

    def _get(self, subtree: str, *, read_timeout: float | None = None) -> str:
        """Read one operational container (XML), mapping transport errors."""
        try:
            return self._get_session().get(
                subtree, read_timeout=read_timeout or self._SHOW_READ_TIMEOUT_SECONDS
            )
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Reading operational data failed on {self._host}: {error}"
            ) from error

    # ------------------------------------------------------------------
    # Read operations.
    # ------------------------------------------------------------------

    def _raw_running_config(self, *, read_timeout: float | None = None) -> str:
        """Normalized running configuration with secrets intact (diff and guard only)."""
        text = config.normalize_config(self._show("show running-config", read_timeout=read_timeout))
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
        return parse_version(self._get(FILTER_VERSION))

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        return parse_uptime(self._get(FILTER_UPTIME))

    def get_ztp_status(self) -> str:
        """Return success when the device is reachable."""
        self.get_running_image()
        return "success"

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        """Get the LLDP neighbor of one interface."""
        return parse_lldp(self._get(FILTER_LLDP)).get(interface_name)

    def _link_states(self) -> dict[str, bool]:
        """Interface line-protocol state from the interface briefs."""
        return parse_link_states(self._get(FILTER_LINKS))

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections from LLDP."""
        return DeviceNeighborData(
            neighbors=parse_lldp(self._get(FILTER_LLDP)),
            link_states=self._link_states(),
        )

    def get_mac_table(self) -> DeviceMacTable:
        """A routed backbone device has no MAC table the callers use."""
        return DeviceMacTable()

    def get_arp_table(self) -> DeviceArpTable:
        """Get the ARP table (the entries of the default VRF's ARP database)."""
        table = DeviceArpTable()
        for ip, mac, interface in parse_arp(self._get(FILTER_ARP)):
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

    def _parse_intended(self, new_configuration: str) -> list[config.Node]:
        try:
            return config.parse_config(new_configuration)
        except config.IosXrConfigError as error:
            raise ConfigSyntaxException(
                f"Invalid configuration for {self._host}: {error}"
            ) from error

    def _parse_running(self) -> list[config.Node]:
        try:
            return config.parse_config(self._raw_running_config())
        except config.IosXrConfigError as error:
            raise NetworkDeviceException(
                f"Cannot parse the running configuration of {self._host}: {error}"
            ) from error

    def _router_diff(self, raw: str) -> str:
        """The router's diff without its headers; empty when nothing would change."""
        return config.parse_router_diff(raw)

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
                config.replacement_text(new_configuration),
                timeout=self._DIFF_TIMEOUT_SECONDS,
            )
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Failed to perform candidate diff on {self._host}: {error}"
            ) from error
        return self._router_diff(raw)

    def _check_access_preserved(self, intended: list[config.Node]) -> None:
        problems = config.check_access_preserved(
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
        text = config.replacement_text(new_configuration)
        session = self._get_session()
        # (1) Load: lock, discard, copy-config, get-cli-config-diff. The lock stays held.
        try:
            raw = session.load_replacement(text, timeout=self._APPLY_TIMEOUT_SECONDS)
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Failed to load the candidate configuration on {self._host}: {error}"
            ) from error

        try:
            diff = self._router_diff(raw)
            if not diff:
                session.discard()
                if approved_diff and commit_confirm:
                    # A diff was approved, yet the router already matches: an earlier attempt
                    # of this apply may have sent the confirmed commit and died before
                    # confirming it, and the router rolls back when its timer expires.
                    # Reporting success here would hide that, and nothing in this session
                    # can tell a pending trial commit from a confirmed one.
                    raise NetworkDeviceException(
                        f"{self._host} already matches the intended configuration, but this "
                        "attempt did not make the change and an earlier confirmed commit may "
                        "still be waiting for its confirmation, after which the router rolls "
                        "back. Wait for the commit timer to expire, then run Config Diff: an "
                        "empty diff means the change is permanent; otherwise run Deploy again.",
                        non_retryable=True,
                    )
                # Nothing was approved, or a plain commit (no timer): nothing to commit.
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
        # Taken before the RPC: the router starts its timer when it applies the commit, which
        # can be before the reply reaches us, so this deadline is never later than its expiry.
        deadline = time.monotonic() + self._COMMIT_CONFIRM_SECONDS
        try:
            session.commit(confirm_seconds=confirm_seconds, timeout=self._APPLY_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 - any failure of a confirmed commit is wrapped
            if not commit_confirm:
                if isinstance(error, NetworkDeviceException):
                    raise
                if isinstance(error, TRANSPORT_ERRORS):
                    raise NetworkDeviceException(
                        f"Failed to apply candidate configuration on {self._host}: {error}"
                    ) from error
                raise
            # A failed confirmed commit may still have reached the router: a retry could
            # find an empty diff and report success while the rollback timer is running.
            # That holds for an unexpected error too, so every exception is wrapped.
            raise NetworkDeviceException(
                f"Confirmed commit on {self._host} failed or its result is unknown ({error}); "
                "the router rolls back when the commit timer expires.",
                non_retryable=True,
            ) from error

        # (4) Second login, second copy-config diff, then the confirming commit. The router's
        # rollback timer started when the commit was applied, so the checks race against it.
        if commit_confirm:
            self._verify_then_confirm(text, deadline)

    def _verify_then_confirm(self, text: str, deadline: float) -> None:
        """Cancel the rollback timer only if the router is reachable and converged.

        Every failure here is non-retryable, including an unexpected exception (the
        original is kept as the cause): a retried apply would find the trial
        configuration already running, compute an empty diff and return success while
        the timer is still counting down to a rollback.

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
        except Exception as error:  # noqa: BLE001 - nothing after the commit may be retried
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
        # The checks above can use up the rollback window (password retries, two 120 s
        # operations). If the timer has expired or is about to, the router has reverted or
        # is reverting, and a confirming commit would succeed as a no-op on the old config.
        if deadline - time.monotonic() < self._CONFIRM_MARGIN_SECONDS:
            raise NetworkDeviceException(
                f"Commit on {self._host} was not confirmed: the checks used up the commit "
                "timer; the router rolls back when it expires.",
                non_retryable=True,
            )
        try:
            self._get_session().confirm()
        except Exception as error:  # noqa: BLE001 - nothing after the commit may be retried
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
        except TRANSPORT_ERRORS as error:
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
            session.action(ZTP_NS, "ztp-initiate", read_timeout=60)
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
        return self._diag("install/version", FILTER_VERSION)

    def diag_get_interfaces(self) -> object:
        return self._diag("interface-briefs", FILTER_LINKS)

    def diag_get_lldp_neighbors(self) -> object:
        return self._diag("lldp neighbors", FILTER_LLDP)

    def diag_get_arp_table(self) -> object:
        return self._diag("arp entries", FILTER_ARP)
