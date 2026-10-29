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
"""Tests for the Cisco IOS XR connection using a fake session (no router)."""

from __future__ import annotations

import difflib
from collections import deque
from typing import Any
from unittest.mock import MagicMock

import pytest
from ncclient.transport.errors import AuthenticationError, SSHError  # type: ignore[import-untyped]

from nv_config_manager_workflows.clients.device import cisco_xr, iosxr_config
from nv_config_manager_workflows.clients.device.cisco_xr import (
    _FILTER_ARP,
    _FILTER_LINKS,
    _FILTER_LLDP,
    _FILTER_UPTIME,
    _FILTER_VERSION,
    CiscoIosXrConnection,
    NetconfIosXrSession,
)
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings

from .iosxr_fixtures import HOST, INTENDED, RUNNING, USERNAME

_REPLY = '<rpc-reply xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><data>{}</data></rpc-reply>'

# Shapes read from a lab router's operational models, trimmed to what the parsers use.
VERSION_XML = _REPLY.format(
    '<install xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-install-oper"><version>'
    "<package><name>IOS-XR</name><version>26.4.1.03I</version><built-by>swtools</built-by></package>"
    "<label>26.4.1.03I-JUNE_EFT</label><uptime>6 days, 2 hours, 17 minutes</uptime>"
    "<chassis-pid>8223-64EF-M</chassis-pid><xr-host-name>xr-router-1</xr-host-name>"
    "</version></install>"
)
UPTIME_XML = _REPLY.format(
    '<system-time xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-shellutil-oper"><uptime>'
    "<host-name>xr-router-1</host-name><uptime>526267</uptime></uptime></system-time>"
)


def _lldp_device(local: str, port: str, system: str, *, parent: str = "Bundle-Ether1204") -> str:
    return (
        f"<device><device-id>{system.upper()}</device-id><interface-name>{local}</interface-name>"
        f"<lldp-neighbor><receiving-interface-name>{local}</receiving-interface-name>"
        f"<receiving-parent-interface-name>{parent}</receiving-parent-interface-name>"
        f"<device-id>{system.upper()}</device-id><chassis-id>062c.db20.05d6</chassis-id>"
        f"<port-id-detail>{port}</port-id-detail><detail><system-name>{system}</system-name>"
        "</detail></lldp-neighbor></device>"
    )


def _lldp_xml(*devices: str) -> str:
    return _REPLY.format(
        '<lldp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ethernet-lldp-oper"><nodes><node>'
        f"<node-name>0/RP0/CPU0</node-name><neighbors><devices>{''.join(devices)}</devices>"
        "</neighbors></node></nodes></lldp>"
    )


LLDP_XML = _lldp_xml(
    _lldp_device("HundredGigE0/0/0/0", "Hu0/0/0/1", "xr-router-2"),
    _lldp_device("HundredGigE0/0/0/1", "00:11:22:33:44:77", "xr-router-3"),
)
LINKS_XML = _REPLY.format(
    '<interfaces xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-pfi-im-cmd-oper"><interface-briefs>'
    "<interface-brief><interface-name>HundredGigE0/0/0/0</interface-name><state>im-state-up</state>"
    "<line-state>im-state-up</line-state></interface-brief>"
    "<interface-brief><interface-name>HundredGigE0/0/0/1</interface-name><state>im-state-up</state>"
    "<line-state>im-state-down</line-state></interface-brief>"
    "</interface-briefs></interfaces>"
)


def _arp_entry(ip: str, mac: str, interface: str, state: str = "state-dynamic") -> str:
    return (
        f"<entry><address>{ip}</address><interface-name>{interface}</interface-name>"
        f"<media-type>media-arpa</media-type><state>{state}</state>"
        f"<hardware-address>{mac}</hardware-address></entry>"
    )


ARP_XML = _REPLY.format(
    '<arp xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-ipv4-arp-oper"><nodes><node>'
    "<node-name>0/RP0/CPU0</node-name><entries>"
    + _arp_entry("10.1.1.1", "00:11:22:33:44:55", "HundredGigE0/0/0/0")
    + _arp_entry("10.1.1.2", "00:00:00:00:00:00", "HundredGigE0/0/0/0", "state-incomplete")
    + _arp_entry("10.1.1.3", "00:11:22:33:44:66", "HundredGigE0/0/0/1", "state-deleted")
    + "</entries></node></nodes></arp>"
)


def _router_diff(running: str, text: str) -> str:
    """What the router's get-cli-config-diff prints: headers, marked changed lines, ``end``."""
    # the router ignores indentation, so the fake compares stripped lines
    old = [line.strip() for line in iosxr_config.normalize_config(running).splitlines()]
    new = [line.strip() for line in iosxr_config.normalize_config(text).splitlines()]
    lines = ["!! Building configuration...", "!! IOS XR Configuration 26.4.1"]
    for entry in difflib.ndiff(old, new):
        if entry.startswith(("- ", "+ ")):
            lines.append(f"{entry[0]}  {entry[2:]}")
    return "\n".join([*lines, "end"]) + "\n"


class FakeSession:
    """A router that holds a running configuration; records calls; answers ``show`` and ``get``."""

    def __init__(
        self, shows: dict[str, str] | None = None, gets: dict[str, str] | None = None
    ) -> None:
        self.shows: dict[str, str] = {"show running-config": RUNNING, **(shows or {})}
        self.gets: dict[str, str] = gets or {}
        self.get_calls: list[str] = []
        self.show_calls: list[str] = []
        self.diff_texts: list[str] = []
        self.loaded: list[str] = []
        self.remaining_texts: list[str] = []
        self.committed: list[int | None] = []
        self.discarded = 0
        self.confirmed = 0
        self.converges = True
        self.rollbacks: list[str] = []
        self.actions: list[tuple[str, str, dict[str, str] | None]] = []
        self.action_error: Exception | None = None
        self.closed = False
        self.load_error: Exception | None = None
        self.commit_error: Exception | None = None
        self.confirm_error: Exception | None = None
        self.extra_diff_lines = ""

    def _diff(self, text: str) -> str:
        diff = _router_diff(self.shows["show running-config"], text)
        return diff.replace("end\n", self.extra_diff_lines + "end\n")

    def show(self, command: str, *, read_timeout: float = 0) -> str:
        self.show_calls.append(command)
        if command not in self.shows:
            raise AssertionError(f"unexpected command {command!r}")
        return self.shows[command]

    def get(self, subtree: str, *, read_timeout: float = 0) -> str:
        self.get_calls.append(subtree)
        if subtree not in self.gets:
            raise AssertionError(f"unexpected operational read {subtree!r}")
        return self.gets[subtree]

    def candidate_diff(self, text: str, *, timeout: float) -> str:
        if self.load_error:
            raise self.load_error
        self.diff_texts.append(text)
        return self._diff(text)

    def load_replacement(self, text: str, *, timeout: float) -> str:
        if self.load_error:
            raise self.load_error
        self.loaded.append(text)
        return self._diff(text)

    def commit(self, *, confirm_seconds: int | None, timeout: float) -> None:
        if self.commit_error:
            raise self.commit_error
        self.committed.append(confirm_seconds)
        if self.converges:  # the router now holds the loaded configuration
            self.shows["show running-config"] = self.loaded[-1]

    def remaining_diff(self, text: str, *, timeout: float) -> str:
        self.remaining_texts.append(text)
        return self._diff(text)

    def discard(self) -> None:
        self.discarded += 1

    def confirm(self) -> None:
        if self.confirm_error:
            raise self.confirm_error
        self.confirmed += 1

    def rollback(self, selector: str) -> None:
        self.rollbacks.append(selector)

    def action(
        self,
        namespace: str,
        name: str,
        params: dict[str, str] | None = None,
        *,
        read_timeout: float = 0,
    ) -> str:
        self.actions.append((namespace, name, params))
        if self.action_error:
            raise self.action_error
        return "<rpc-reply/>"

    def close(self) -> None:
        self.closed = True


def _connection(*sessions: Any) -> tuple[CiscoIosXrConnection, deque[Any]]:
    settings: DeviceConnectionSettings = {"username": USERNAME, "passwords": ["pw"], "mock": False}
    connection = CiscoIosXrConnection(HOST, settings=settings)
    queue: deque[Any] = deque(sessions)
    connection._open_session = lambda: queue.popleft()  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    return connection, queue


CHANGED = INTENDED.replace("metric 100", "metric 200")


# --- read operations ---------------------------------------------------------


def test_running_configuration_is_normalized_and_secrets_are_redacted() -> None:
    connection, _ = _connection(FakeSession())

    text = connection.get_running_configuration()

    assert "Building configuration" not in text
    assert "placeholderplaceholder" not in text
    assert "secret 10 <redacted>" in text
    assert "hostname xr-router-1" in text


def test_empty_running_configuration_is_an_error() -> None:
    connection, _ = _connection(
        FakeSession({"show running-config": "Mon Oct 11 10:00:00.123 UTC\n"})
    )

    with pytest.raises(NetworkDeviceException, match="Empty running configuration"):
        connection.get_running_configuration()


def test_hostname_image_and_uptime() -> None:
    session = FakeSession(gets={_FILTER_VERSION: VERSION_XML, _FILTER_UPTIME: UPTIME_XML})
    connection, _ = _connection(session)

    assert connection.get_hostname() == "xr-router-1"
    assert connection.get_running_image() == "26.4.1.03I"
    assert connection.get_uptime() == 526267
    assert connection.get_ztp_status() == "success"


def test_unparseable_version_and_uptime_replies_are_errors() -> None:
    empty = _REPLY.format("")
    connection, _ = _connection(FakeSession(gets={_FILTER_VERSION: empty, _FILTER_UPTIME: empty}))

    with pytest.raises(NetworkDeviceException, match="IOS-XR package version"):
        connection.get_running_image()
    with pytest.raises(NetworkDeviceException, match="uptime"):
        connection.get_uptime()
    connection2, _ = _connection(FakeSession(gets={_FILTER_VERSION: "not xml"}))
    with pytest.raises(NetworkDeviceException, match="Unreadable"):
        connection2.get_running_image()


def test_lldp_and_link_states() -> None:
    connection, _ = _connection(
        FakeSession(gets={_FILTER_LLDP: LLDP_XML, _FILTER_LINKS: LINKS_XML})
    )

    data = connection.get_interface_connections()

    assert data.neighbors["HundredGigE0/0/0/0"].device_name == "xr-router-2"
    assert data.neighbors["HundredGigE0/0/0/0"].name == "Hu0/0/0/1"
    assert data.neighbors["HundredGigE0/0/0/1"].name == "00-11-22-33-44-77"
    assert data.link_states == {"HundredGigE0/0/0/0": True, "HundredGigE0/0/0/1": False}
    assert connection.get_lldp_data("HundredGigE0/0/0/9") is None


def test_duplicate_lldp_neighbors_on_one_interface_are_an_error() -> None:
    doubled = _lldp_xml(
        _lldp_device("HundredGigE0/0/0/0", "a", "one"),
        _lldp_device("HundredGigE0/0/0/0", "b", "two"),
    )
    connection, _ = _connection(FakeSession(gets={_FILTER_LLDP: doubled}))

    with pytest.raises(NetworkDeviceException, match="multiple LLDP neighbors"):
        connection.get_lldp_data("HundredGigE0/0/0/0")


def test_arp_table_skips_unresolved_entries_and_the_mac_table_is_empty() -> None:
    connection, _ = _connection(FakeSession(gets={_FILTER_ARP: ARP_XML}))

    arp = connection.get_arp_table()

    assert arp.ip_to_mac == {"10.1.1.1": ["00-11-22-33-44-55"]}
    assert arp.interface_to_mac == {"HundredGigE0/0/0/0": ["00-11-22-33-44-55"]}
    assert connection.get_mac_table().by_mac == {}


def test_operational_read_errors_are_network_device_errors() -> None:
    session = FakeSession()
    session.get = MagicMock(side_effect=SSHError("reset"))  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    connection, _ = _connection(session)

    with pytest.raises(NetworkDeviceException, match="reset"):
        connection.get_uptime()


# --- diff --------------------------------------------------------------------


def test_diff_is_empty_for_a_converged_router_and_never_commits() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    assert connection.perform_candidate_diff(INTENDED) == ""
    assert session.committed == []


def test_diff_sends_the_intended_text_with_its_separators_and_a_closing_end() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    connection.perform_candidate_diff(INTENDED)

    sent = session.diff_texts[0]
    assert sent == iosxr_config.replacement_text(INTENDED)
    assert "\n!\n" in sent
    assert sent.endswith("\nend\n")
    assert "Building configuration" not in sent


def test_diff_lists_the_routers_marked_lines_without_its_headers() -> None:
    connection, _ = _connection(FakeSession())

    diff = connection.perform_candidate_diff(CHANGED)

    lines = diff.splitlines()
    assert any(line.startswith("-") and "metric 100" in line for line in lines)
    assert any(line.startswith("+") and "metric 200" in line for line in lines)
    assert not any(line.startswith("!!") or line == "end" for line in lines)


def test_hash_lines_in_the_routers_diff_are_a_change() -> None:
    session = FakeSession()
    session.extra_diff_lines = "#  hostname xr-router-1\n#  hostname xr-router-1-x\n"
    connection, _ = _connection(session)

    diff = connection.perform_candidate_diff(INTENDED)

    assert diff == "#  hostname xr-router-1\n#  hostname xr-router-1-x\n"
    assert "#  hostname xr-router-1-x" in connection.perform_candidate_diff(CHANGED)


def test_partial_diff_is_rejected() -> None:
    connection, _ = _connection(FakeSession())

    with pytest.raises(NetworkDeviceException, match="Partial configuration"):
        connection.perform_candidate_diff(INTENDED, partial=True)


def test_unparseable_intended_config_is_a_syntax_error_and_nothing_is_sent() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    with pytest.raises(ConfigSyntaxException):
        connection.perform_candidate_diff("prefix-set A\n  10.0.0.0/8\n")

    assert session.diff_texts == []


def test_a_router_error_while_loading_the_diff_propagates() -> None:
    session = FakeSession()
    session.load_error = ConfigSyntaxException("% Invalid input detected")
    connection, _ = _connection(session)

    with pytest.raises(ConfigSyntaxException):
        connection.perform_candidate_diff(CHANGED)


# --- apply -------------------------------------------------------------------


def test_apply_with_empty_diff_commits_nothing_and_releases_the_candidate() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    connection.commit_candidate_config(INTENDED, "")

    assert session.committed == []
    assert session.discarded == 1
    assert session.confirmed == 0


def test_apply_rejects_a_diff_that_changed_since_approval_and_discards() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    with pytest.raises(DiffChangedException):
        connection.commit_candidate_config(CHANGED, "an older diff")

    assert session.committed == []
    assert session.discarded == 1


def test_apply_with_commit_confirm_verifies_and_then_confirms() -> None:
    session, probe = FakeSession(), FakeSession({"show clock": "ok"})
    connection, _ = _connection(session, probe)
    approved = connection.perform_candidate_diff(CHANGED)

    connection.commit_candidate_config(CHANGED, approved)

    assert session.loaded == [iosxr_config.replacement_text(CHANGED)]
    assert session.committed == [CiscoIosXrConnection._COMMIT_CONFIRM_SECONDS]
    # the verification is a second router diff of the same text, taken after the commit
    assert session.remaining_texts == session.loaded
    assert probe.closed
    assert session.confirmed == 1


def test_apply_without_commit_confirm_sends_no_timer_and_no_confirm() -> None:
    session = FakeSession()
    connection, queue = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)

    connection.commit_candidate_config(CHANGED, approved, commit_confirm=False)

    assert session.committed == [None]
    assert session.remaining_texts == []
    assert session.confirmed == 0


def test_apply_does_not_confirm_when_a_fresh_login_fails() -> None:
    session = FakeSession()
    connection, _ = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)
    connection._open_session = MagicMock(  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
        side_effect=NetworkDeviceException("All passwords failed")
    )

    with pytest.raises(NetworkDeviceException, match="not confirmed") as raised:
        connection.commit_candidate_config(CHANGED, approved)

    assert session.confirmed == 0
    assert "roll back" in str(raised.value)
    assert raised.value.non_retryable


def test_apply_does_not_confirm_when_the_router_does_not_match_the_intended_config() -> None:
    session, probe = FakeSession(), FakeSession({"show clock": "ok"})
    session.converges = False  # the commit "succeeds" but the running config stays as it was
    connection, _ = _connection(session, probe)
    approved = connection.perform_candidate_diff(CHANGED)

    with pytest.raises(NetworkDeviceException, match="still differs") as raised:
        connection.commit_candidate_config(CHANGED, approved)

    assert session.confirmed == 0
    assert raised.value.non_retryable


def test_failure_to_confirm_is_reported_and_not_retryable() -> None:
    session, probe = FakeSession(), FakeSession({"show clock": "ok"})
    session.confirm_error = NetworkDeviceException("session dropped")
    connection, _ = _connection(session, probe)
    approved = connection.perform_candidate_diff(CHANGED)

    with pytest.raises(NetworkDeviceException, match="not confirmed") as raised:
        connection.commit_candidate_config(CHANGED, approved)

    assert raised.value.non_retryable


def test_apply_does_not_confirm_when_the_checks_used_up_the_commit_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, probe = FakeSession(), FakeSession({"show clock": "ok"})
    connection, _ = _connection(session, probe)
    approved = connection.perform_candidate_diff(CHANGED)
    clock = iter([1000.0, 1000.0 + connection._COMMIT_CONFIRM_SECONDS])  # commit, then pre-confirm
    monkeypatch.setattr(cisco_xr.time, "monotonic", lambda: next(clock))

    with pytest.raises(NetworkDeviceException, match="used up the commit timer") as raised:
        connection.commit_candidate_config(CHANGED, approved)

    assert session.confirmed == 0
    assert raised.value.non_retryable


def test_apply_counts_the_commit_reply_delay_against_the_commit_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, probe = FakeSession(), FakeSession({"show clock": "ok"})
    connection, _ = _connection(session, probe)
    approved = connection.perform_candidate_diff(CHANGED)
    now = [1000.0]
    monkeypatch.setattr(cisco_xr.time, "monotonic", lambda: now[0])
    fast_commit = session.commit

    def slow_commit(*, confirm_seconds: int | None, timeout: float) -> None:
        fast_commit(confirm_seconds=confirm_seconds, timeout=timeout)
        now[0] += connection._COMMIT_CONFIRM_SECONDS  # the reply took the whole timer

    monkeypatch.setattr(session, "commit", slow_commit)

    with pytest.raises(NetworkDeviceException, match="used up the commit timer"):
        connection.commit_candidate_config(CHANGED, approved)

    assert session.confirmed == 0


def test_syntax_error_from_the_router_propagates_and_nothing_is_committed() -> None:
    session = FakeSession()
    connection, _ = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)
    session.load_error = ConfigSyntaxException("% Invalid input detected")

    with pytest.raises(ConfigSyntaxException):
        connection.commit_candidate_config(CHANGED, approved)

    assert session.committed == []
    assert session.confirmed == 0


def test_commit_failure_propagates() -> None:
    session = FakeSession()
    session.commit_error = NetworkDeviceException("Commit failed")
    connection, _ = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)

    with pytest.raises(NetworkDeviceException, match="Commit failed"):
        connection.commit_candidate_config(CHANGED, approved)

    assert session.confirmed == 0


@pytest.mark.parametrize(
    "error",
    [NetworkDeviceException("Commit failed"), SSHError("connection lost")],
    ids=["device-exception", "transport-error"],
)
def test_a_failed_confirmed_commit_is_not_retryable(error: Exception) -> None:
    """The confirmed commit may have reached the router, so a retry could report a false success."""
    session = FakeSession()
    session.commit_error = error
    connection, _ = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)

    with pytest.raises(NetworkDeviceException) as raised:
        connection.commit_candidate_config(CHANGED, approved, commit_confirm=True)

    assert raised.value.non_retryable
    assert session.confirmed == 0


@pytest.mark.parametrize(
    "error",
    [NetworkDeviceException("Commit failed"), SSHError("connection lost")],
    ids=["device-exception", "transport-error"],
)
def test_a_failed_plain_commit_stays_retryable(error: Exception) -> None:
    """Without the commit timer nothing is pending on the router, so a retry is safe."""
    session = FakeSession()
    session.commit_error = error
    connection, _ = _connection(session)
    approved = connection.perform_candidate_diff(CHANGED)

    with pytest.raises(NetworkDeviceException) as raised:
        connection.commit_candidate_config(CHANGED, approved, commit_confirm=False)

    assert not raised.value.non_retryable


def test_apply_sends_nothing_when_the_intended_config_would_remove_access() -> None:
    session = FakeSession()
    connection, _ = _connection(session)
    without_user = INTENDED.replace(
        "username admin\n  group root-lr\n  group cisco-support\n"
        "  secret 10 $6$H0QFj2rx$placeholderplaceholder\n",
        "",
    )
    approved = connection.perform_candidate_diff(without_user)

    with pytest.raises(NetworkDeviceException, match="account in use") as raised:
        connection.commit_candidate_config(without_user, approved)

    assert session.committed == []
    assert session.discarded == 1
    assert raised.value.non_retryable


@pytest.mark.parametrize(
    "removed",
    [
        "ssh server vrf MGMT\n",
        "aaa authentication login default local\n",
        "  transport input ssh\n",
        "      0.0.0.0/0 192.0.2.1\n",
        "  ipv4 address 192.0.2.7 255.255.255.0\n",
    ],
)
def test_apply_never_commits_when_it_would_drop_management_items(removed: str) -> None:
    session = FakeSession()
    connection, _ = _connection(session)
    candidate = INTENDED.replace(removed, "", 1)
    assert candidate != INTENDED
    approved = connection.perform_candidate_diff(candidate)

    with pytest.raises(NetworkDeviceException, match="Refusing to apply"):
        connection.commit_candidate_config(candidate, approved)

    assert session.committed == []
    assert session.discarded == 1


def test_partial_apply_is_rejected_before_any_read() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    with pytest.raises(NetworkDeviceException, match="Partial configuration"):
        connection.commit_candidate_config(INTENDED, "", partial=True)

    assert session.show_calls == []
    assert session.loaded == []


# --- rollback and unsupported operations ------------------------------------------


def test_rollback_configuration_rolls_back_the_last_commits() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    connection.rollback_configuration(2)
    assert session.rollbacks == ["last 2"]


def test_rollback_diff_is_not_supported_over_netconf() -> None:
    connection, _ = _connection(FakeSession())

    with pytest.raises(NotImplementedError):
        connection.get_rollback_diff(2)


def test_rescue_is_not_supported_over_netconf() -> None:
    connection, _ = _connection(FakeSession())

    for call in (
        connection.save_rescue_configuration,
        connection.get_rescue_configuration,
        connection.delete_rescue_configuration,
        connection.rollback_to_rescue,
    ):
        with pytest.raises(NotImplementedError):
            call()


def test_execute_ztp_invokes_the_ztp_action_and_closes_the_session() -> None:
    session = FakeSession()
    connection, _ = _connection(session)

    connection.execute_ztp()

    assert session.actions == [
        ("http://cisco.com/ns/yang/Cisco-IOS-XR-ztp-act", "ztp-initiate", None)
    ]
    assert session.closed


def test_execute_ztp_treats_a_dropped_session_as_success() -> None:
    session = FakeSession()
    session.action_error = NetworkDeviceException("connection reset")
    connection, _ = _connection(session)

    connection.execute_ztp()

    assert session.closed


def test_execute_ztp_fails_when_the_session_cannot_be_opened() -> None:
    connection, _ = _connection(FakeSession())
    connection._open_session = MagicMock(  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
        side_effect=NetworkDeviceException("All passwords failed")
    )

    with pytest.raises(NetworkDeviceException, match="All passwords failed"):
        connection.execute_ztp()


def test_execute_ztp_reports_a_refused_action() -> None:
    session = FakeSession()
    session.action_error = NetworkDeviceException(
        "ztp-initiate failed: not allowed", non_retryable=True
    )
    connection, _ = _connection(session)

    with pytest.raises(NetworkDeviceException, match="not allowed"):
        connection.execute_ztp()


def test_diagnostics_dispatch() -> None:
    connection, _ = _connection(FakeSession(gets={_FILTER_VERSION: VERSION_XML}))

    output = connection.run_diagnostic_command("show_version")
    assert "26.4.1.03I" in output
    assert "install/version" in output
    with pytest.raises(NetworkDeviceException, match="not implemented"):
        connection.run_diagnostic_command("show_bgp_summary")
    with pytest.raises(NetworkDeviceException, match="not implemented"):
        connection.run_diagnostic_command("show_route_table")


# --- connection management ----------------------------------------------------


def test_close_releases_the_session() -> None:
    session = FakeSession()
    connection, _ = _connection(session)
    connection.get_hostname()

    connection.close()

    assert session.closed
    connection.close()


def _fake_manager() -> MagicMock:
    manager = MagicMock()
    manager.server_capabilities = {":candidate", ":confirmed-commit"}
    return manager


def test_open_session_rotates_passwords_and_passes_the_netconf_port() -> None:
    settings: DeviceConnectionSettings = {
        "username": USERNAME,
        "passwords": ["bad", "good"],
        "mock": False,
    }
    connection = CiscoIosXrConnection(HOST, 2222, settings=settings)
    seen: list[dict[str, Any]] = []

    def fake_connect(**kwargs: Any) -> MagicMock:
        seen.append(kwargs)
        if kwargs["password"] == "bad":
            raise AuthenticationError("denied")
        return _fake_manager()

    connection._netconf_connect = fake_connect  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    session = connection._open_session()

    assert isinstance(session, NetconfIosXrSession)
    assert [call["password"] for call in seen] == ["bad", "good"]
    assert seen[0]["device_params"] == {"name": "iosxr"}
    assert seen[0]["port"] == 2222


def test_default_port_is_the_netconf_port() -> None:
    settings: DeviceConnectionSettings = {"username": USERNAME, "passwords": ["a"], "mock": False}
    seen: list[dict[str, Any]] = []

    def fake_connect(**kwargs: Any) -> MagicMock:
        seen.append(kwargs)
        return _fake_manager()

    connection = CiscoIosXrConnection(HOST, settings=settings)
    connection._netconf_connect = fake_connect  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    connection._open_session()

    assert seen[0]["port"] == 830


def test_open_session_reports_all_passwords_failed_and_unreachable_hosts() -> None:
    settings: DeviceConnectionSettings = {"username": USERNAME, "passwords": ["a"], "mock": False}
    connection = CiscoIosXrConnection(HOST, settings=settings)
    connection._netconf_connect = MagicMock(side_effect=AuthenticationError("denied"))  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    with pytest.raises(NetworkDeviceException, match="All passwords failed"):
        connection._open_session()

    connection._netconf_connect = MagicMock(side_effect=SSHError("connection refused"))  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    with pytest.raises(NetworkDeviceException, match="Cannot open a NETCONF session") as excinfo:
        connection._open_session()
    assert "user guide" in str(excinfo.value)


@pytest.mark.parametrize("missing", [":candidate", ":confirmed-commit"])
def test_open_session_refuses_a_router_without_candidate_or_confirmed_commit(missing: str) -> None:
    settings: DeviceConnectionSettings = {"username": USERNAME, "passwords": ["a"], "mock": False}
    connection = CiscoIosXrConnection(HOST, settings=settings)
    manager = _fake_manager()
    manager.server_capabilities = {":candidate", ":confirmed-commit"} - {missing}
    connection._netconf_connect = MagicMock(return_value=manager)  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    with pytest.raises(NetworkDeviceException, match=missing) as excinfo:
        connection._open_session()

    assert excinfo.value.non_retryable
    manager.close_session.assert_called_once()
