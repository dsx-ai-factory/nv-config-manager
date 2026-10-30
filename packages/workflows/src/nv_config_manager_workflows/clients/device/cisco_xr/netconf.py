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
"""NETCONF-over-SSH session of a Cisco IOS XR router (ncclient).

:class:`IosXrSession` is the small protocol the connection needs from a router session;
:class:`NetconfIosXrSession` implements it with NETCONF RPCs: ``<get-config>`` and ``<get>``
for reads, and ``<lock>``, ``<copy-config>``, the ``get-cli-config-diff`` action, ``<commit>``,
``<discard-changes>`` and ``<unlock>`` for the candidate datastore. The flow that uses them
(diff, approval, commit, verification, confirm) is in :mod:`.connection`.

The namespaces and element names are the constants below, the one place to change if
another release differs.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from lxml import etree  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
from ncclient.operations import RPCError, TimeoutExpiredError  # type: ignore[import-untyped]
from ncclient.transport.errors import TransportError  # type: ignore[import-untyped]
from ncclient.xml_ import to_ele  # type: ignore[import-untyped]
from nv_config_manager_logging import LogCategory, get_logger

from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    NetworkDeviceException,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)

# ncclient raises its own errors for transport and RPC failures; sockets raise OSError.
# AuthenticationError is a TransportError, so it is mapped separately where it matters.
TRANSPORT_ERRORS = (TransportError, RPCError, TimeoutExpiredError, OSError)

NETCONF_PORT = 830  # IANA NETCONF-over-SSH port (RFC 6242)

# --- CLI text over NETCONF (checked on a lab router running IOS XR 26.4.1) ---------
# The router's Cisco-IOS-XR-cli-cfg YANG module is one top-level string leaf, ``cli``
# ("a set of CLI config commands"). Reading it with get-config returns the running
# configuration as CLI text; writing it with copy-config replaces the candidate with CLI
# text (edit-config accepts the leaf only as a merge). There is no exec/show RPC:
# operational commands need their own YANG path.
CLI_CFG_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cli-cfg"
_CLI_LEAF = "cli"
NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
_RUNNING_CONFIG_COMMAND = "show running-config"

# Standard NETCONF error tags that mean "the router does not implement this RPC".
_UNSUPPORTED_TAGS = ("operation-not-supported", "unknown-element", "unknown-namespace")

# --- Actions (RPCs from the router's ``-act`` YANG modules; names and inputs read from a lab router) ---
DIFF_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cli-diff-act"
_ROLLBACK_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-cfgmgr-rollback-act"
ZTP_NS = "http://cisco.com/ns/yang/Cisco-IOS-XR-ztp-act"
_ROLLBACK_LAST_RE = re.compile(r"^last (?P<count>\d+)$")


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
            f'<filter xmlns="{NETCONF_BASE_NS}"><{_CLI_LEAF} xmlns="{CLI_CFG_NS}"/></filter>'
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
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(
                f"Reading the running configuration failed: {error}"
            ) from error
        finally:
            self._session.timeout = previous
        leaf = etree.fromstring(reply.xml.encode()).find(f".//{{{CLI_CFG_NS}}}{_CLI_LEAF}")
        return "" if leaf is None or leaf.text is None else str(leaf.text)

    def show(self, command: str, *, read_timeout: float = 60.0) -> str:
        """Run an operational command; only ``show running-config`` has a NETCONF path today."""
        if command.strip() != _RUNNING_CONFIG_COMMAND:
            raise self._no_netconf_path(command)
        return self._read_running_cli(read_timeout)

    def get(self, subtree: str, *, read_timeout: float = 60.0) -> str:
        """<get> with a subtree filter: the XML reply for one operational container."""
        selector = to_ele(f'<filter xmlns="{NETCONF_BASE_NS}">{subtree}</filter>')
        previous = self._session.timeout
        self._session.timeout = read_timeout
        try:
            reply = self._session.get(filter=selector)
        except RPCError as error:
            raise NetworkDeviceException(
                f"Reading operational data failed: {_rpc_message(error)}"
            ) from error
        except TRANSPORT_ERRORS as error:
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
        source = etree.Element(f"{{{NETCONF_BASE_NS}}}source", nsmap={None: NETCONF_BASE_NS})
        config = etree.SubElement(source, f"{{{NETCONF_BASE_NS}}}config")
        leaf = etree.SubElement(config, f"{{{CLI_CFG_NS}}}{_CLI_LEAF}", nsmap={None: CLI_CFG_NS})
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
        except TRANSPORT_ERRORS as error:
            raise NetworkDeviceException(f"Failed to load configuration: {error}") from error
        finally:
            self._session.timeout = previous

    def _candidate_diff_text(self, timeout: float) -> str:
        """The router's CLI diff of the candidate against running (``get-cli-config-diff``).

        An empty ``<response>`` is the router saying there is no difference. A reply with
        no ``<response>`` element, or that is not XML, is an error: reading it as "no
        difference" would let the post-commit check confirm a change it never verified.
        """
        reply = self.action(DIFF_NS, "get-cli-config-diff", read_timeout=timeout)
        try:
            node = etree.fromstring(reply.encode()).find(f".//{{{DIFF_NS}}}response")
        except etree.XMLSyntaxError as error:
            raise NetworkDeviceException(
                f"The router's get-cli-config-diff reply is not valid XML: {error}",
                non_retryable=True,
            ) from error
        if node is None:
            raise NetworkDeviceException(
                "The router's get-cli-config-diff reply has no <response> element.",
                non_retryable=True,
            )
        return "" if node.text is None else str(node.text)

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
        except TRANSPORT_ERRORS as error:
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
        except TRANSPORT_ERRORS as error:
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
        except TRANSPORT_ERRORS as error:
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
        except TRANSPORT_ERRORS as error:
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
