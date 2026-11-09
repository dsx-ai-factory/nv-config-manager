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

import http.client
import io
import json
import tarfile
from typing import cast
from unittest.mock import MagicMock, call, patch

import pyeapi.eapilib
import pytest

from nv_config_manager_workflows.clients.device import arista
from nv_config_manager_workflows.clients.device.arista import AristaConnection
from nv_config_manager_workflows.clients.device.base import COMMIT_CONFIRM_ROLLBACK_SECONDS
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
    ZTPRollbackException,
)


def _arista_connection() -> AristaConnection:
    conn = AristaConnection.__new__(AristaConnection)
    conn._session_id = "sess-1"
    conn._host = "192.0.2.1"
    setattr(conn, "_abort", MagicMock())
    setattr(conn, "_load_candidate_config", MagicMock())
    setattr(conn, "_diff", MagicMock(return_value="new-diff"))
    return conn


def test_commit_preserves_diff_changed_exception() -> None:
    """A mismatched approved diff raises DiffChangedException, not a wrapped failure."""
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=False))
    abort = cast(MagicMock, conn._abort)

    with pytest.raises(DiffChangedException, match="changed since approval"):
        conn.commit_candidate_config("config", "old-diff")

    abort.assert_called_once()


def test_commit_wraps_other_failures_as_network_device_exception() -> None:
    """Unexpected commit errors stay wrapped as NetworkDeviceException."""
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=True))
    conn._node = MagicMock()
    conn._node.enable.side_effect = RuntimeError("eAPI down")

    with pytest.raises(NetworkDeviceException, match="Failed to commit session sess-1"):
        conn.commit_candidate_config("config", "new-diff", commit_confirm=False)


@pytest.mark.parametrize("commit_confirm", [True, False])
def test_commit_confirm_controls_arista_commit_timer(commit_confirm: bool) -> None:
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=True))
    conn._node = MagicMock()

    conn.commit_candidate_config(
        "configuration",
        "new-diff",
        commit_confirm=commit_confirm,
    )

    if commit_confirm:
        minutes, seconds = divmod(COMMIT_CONFIRM_ROLLBACK_SECONDS, 60)
        hours, minutes = divmod(minutes, 60)
        conn._node.run_commands.assert_called_once_with(
            [
                "configure session sess-1",
                f"commit timer {hours:02d}:{minutes:02d}:{seconds:02d}",
            ]
        )
        assert conn._node.enable.call_args_list == [
            call("configure session sess-1 commit"),
            call("copy running-config startup-config"),
        ]
    else:
        conn._node.run_commands.assert_not_called()
        assert conn._node.enable.call_args_list == [
            call("configure session sess-1 commit"),
            call("copy running-config startup-config"),
        ]


def test_commit_preserves_diff_changed_when_abort_fails() -> None:
    """Abort failure after a stale diff must not replace DiffChangedException."""
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=False))
    abort = cast(MagicMock, conn._abort)
    abort.side_effect = NetworkDeviceException("Failed to cleanup session sess-1")

    with pytest.raises(DiffChangedException, match="changed since approval"):
        conn.commit_candidate_config("config", "old-diff")

    abort.assert_called_once()


def test_commit_preserves_config_syntax_when_abort_fails() -> None:
    """Abort failure after invalid config must not replace ConfigSyntaxException."""
    conn = _arista_connection()
    load_candidate = cast(MagicMock, conn._load_candidate_config)
    abort = cast(MagicMock, conn._abort)
    load_candidate.side_effect = ConfigSyntaxException("Invalid configuration supplied.")
    abort.side_effect = NetworkDeviceException("Failed to cleanup session sess-1")

    with pytest.raises(ConfigSyntaxException, match="Invalid configuration supplied"):
        conn.commit_candidate_config("config", "old-diff")

    abort.assert_called_once()


def test_diff_and_commit_pass_partial_to_load_candidate() -> None:
    """Tenant (partial) deploys must not issue rollback clean-config."""
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=True))
    load_candidate = cast(MagicMock, conn._load_candidate_config)
    conn._node = MagicMock()

    conn.perform_candidate_diff("fragment", partial=True)
    load_candidate.assert_called_with("fragment", partial=True)

    load_candidate.reset_mock()
    conn.commit_candidate_config("fragment", "new-diff", partial=True, commit_confirm=False)
    load_candidate.assert_called_with("fragment", partial=True)


def test_diff_and_commit_default_to_full_candidate_load() -> None:
    """Full deploys keep the default partial=False load."""
    conn = _arista_connection()
    setattr(conn, "_diff_eq", MagicMock(return_value=True))
    load_candidate = cast(MagicMock, conn._load_candidate_config)
    conn._node = MagicMock()

    conn.perform_candidate_diff("full-config")
    load_candidate.assert_called_with("full-config", partial=False)

    load_candidate.reset_mock()
    conn.commit_candidate_config("full-config", "new-diff", commit_confirm=False)
    load_candidate.assert_called_with("full-config", partial=False)


def test_load_candidate_omits_empty_command_on_partial() -> None:
    """Partial loads start the session without a blank rollback placeholder."""
    conn = AristaConnection.__new__(AristaConnection)
    conn._node = MagicMock()
    setattr(
        conn,
        "_extract_banner_commands",
        MagicMock(return_value=("hostname leaf1\nend", [])),
    )

    conn._load_candidate_config("hostname leaf1\nend", partial=True)

    commands = conn._node.run_commands.call_args[0][0]
    assert commands[0].startswith("configure session ")
    assert "" not in commands
    assert "rollback clean-config" not in commands
    assert commands[1:] == ["hostname leaf1", "end"]


def test_load_candidate_includes_rollback_on_full_load() -> None:
    """Full loads still wipe the session with rollback clean-config."""
    conn = AristaConnection.__new__(AristaConnection)
    conn._node = MagicMock()
    setattr(
        conn,
        "_extract_banner_commands",
        MagicMock(return_value=("hostname leaf1\nend", [])),
    )

    conn._load_candidate_config("hostname leaf1\nend", partial=False)

    commands = conn._node.run_commands.call_args[0][0]
    assert commands[0].startswith("configure session ")
    assert commands[1] == "rollback clean-config"
    assert commands[2:] == ["hostname leaf1", "end"]


def test_get_arp_table_parses_eapi_neighbors_and_multiple_interfaces() -> None:
    conn = _arista_connection()
    conn._node = MagicMock()
    conn._node.enable.return_value = [
        {
            "result": {
                "ipV4Neighbors": [
                    {
                        "address": "192.0.2.10",
                        "hwAddress": "00:11:22:33:44:55",
                        "interface": "Vlan100, Ethernet1",
                    }
                ]
            }
        }
    ]

    table = conn.get_arp_table()

    assert table.ip_to_mac == {"192.0.2.10": ["00-11-22-33-44-55"]}
    assert table.interface_to_mac == {
        "Vlan100": ["00-11-22-33-44-55"],
        "Ethernet1": ["00-11-22-33-44-55"],
    }


def test_get_interface_connections_combines_eapi_lldp_and_link_state() -> None:
    conn = _arista_connection()
    conn._node = MagicMock()
    conn._node.enable.side_effect = [
        [
            {
                "result": {
                    "lldpNeighbors": {
                        "Ethernet1": {
                            "lldpNeighborInfo": [
                                {
                                    "systemName": "spine-1",
                                    "neighborInterfaceInfo": {"interfaceId": "Ethernet51"},
                                }
                            ]
                        },
                        "Ethernet2": {"lldpNeighborInfo": []},
                    }
                }
            }
        ],
        [
            {
                "result": {
                    "interfaceStatuses": {
                        "Ethernet1": {"linkStatus": "connected"},
                        "Ethernet2": {"linkStatus": "disabled"},
                    }
                }
            }
        ],
    ]

    output = conn.get_interface_connections()

    assert output.neighbors["Ethernet1"].device_name == "spine-1"
    assert output.neighbors["Ethernet1"].name == "Ethernet51"
    assert output.link_states == {"Ethernet1": True, "Ethernet2": False}


def _eapi_node(*results: object) -> MagicMock:
    """Mock an eAPI node whose successive enable() calls return each result."""
    node = MagicMock()
    node.enable.side_effect = [
        result if isinstance(result, BaseException) else [{"result": result}] for result in results
    ]
    return node


def test_get_running_image_returns_eos_version() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({"version": "4.34.3M", "modelName": "CCS-720XP-48ZC2-F"})

    assert conn.get_running_image() == "4.34.3M"
    conn._node.enable.assert_called_once_with("show version")


def test_get_running_image_strips_engineering_build() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({"version": "4.34.3M-44102239.4343M (engineering build)"})

    assert conn.get_running_image() == "4.34.3M"


def test_get_running_image_raises_when_version_missing() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({"modelName": "cEOSLab"})

    with pytest.raises(
        NetworkDeviceException, match="No EOS version reported by 192.0.2.1"
    ) as error:
        conn.get_running_image()
    assert error.value.non_retryable


def test_get_tech_support_bundle_packs_report_as_gzipped_tarball() -> None:
    conn = _arista_connection()
    conn._node = MagicMock()
    conn._node.enable.return_value = [{"result": {"output": "------ show version ------\n"}}]
    heartbeat = MagicMock()

    content, summary = conn.get_tech_support_bundle(heartbeat)

    conn._node.enable.assert_called_once_with("show tech-support", encoding="text")
    heartbeat.assert_called()
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        assert tar.getnames() == ["show-tech-support.txt"]
        report = tar.extractfile("show-tech-support.txt")
        assert report is not None
        assert report.read() == b"------ show version ------\n"
    assert summary.startswith("show tech-support collected from 192.0.2.1: 27 bytes")


@pytest.mark.parametrize(
    "failure",
    [
        pyeapi.eapilib.CommandError(1002, "invalid command"),
        pyeapi.eapilib.ConnectionError("https", "timed out"),
    ],
    ids=["command-error", "connection-error"],
)
def test_get_tech_support_bundle_wraps_eapi_errors(failure: Exception) -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(failure)

    with pytest.raises(NetworkDeviceException, match="show tech-support failed on 192.0.2.1"):
        conn.get_tech_support_bundle()


def test_get_tech_support_bundle_extends_the_eapi_timeout_for_the_report() -> None:
    conn = _arista_connection()
    conn._node = MagicMock()
    conn._node.connection.transport.timeout = 300
    timeouts_seen = []

    def enable(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        timeouts_seen.append(conn._node.connection.transport.timeout)
        return [{"result": {"output": "report\n"}}]

    conn._node.enable.side_effect = enable

    conn.get_tech_support_bundle()

    assert timeouts_seen == [arista._TECH_SUPPORT_TIMEOUT_SECONDS]
    assert conn._node.connection.transport.timeout == 300


def test_get_tech_support_bundle_reports_a_timeout() -> None:
    conn = _arista_connection()
    timed_out = pyeapi.eapilib.ConnectionError("https", "Socket error during eAPI connection")
    timed_out.__context__ = TimeoutError("timed out")
    conn._node = _eapi_node(timed_out)

    with pytest.raises(NetworkDeviceException, match="show tech-support timed out on 192.0.2.1"):
        conn.get_tech_support_bundle()


def test_get_tech_support_bundle_rejects_a_response_without_output() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({"messages": []})

    with pytest.raises(
        NetworkDeviceException, match="show tech-support returned no output on 192.0.2.1"
    ):
        conn.get_tech_support_bundle()


def test_diag_platform_environment_reports_each_missing_sensor_group() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {"fanTraySlots": []},
        pyeapi.eapilib.CommandError(1002, "not supported on this hardware platform"),
        {"powerSupplies": {}},
    )

    environment = conn.diag_get_platform_environment()

    assert environment == {
        "cooling": {"fanTraySlots": []},
        "temperature": {"error": "No temperature data available on this device"},
        "power": {"powerSupplies": {}},
    }
    assert [c.args[0] for c in conn._node.enable.call_args_list] == [
        "show system environment cooling",
        "show system environment temperature",
        "show system environment power",
    ]


def test_diag_platform_transceiver_returns_eos_transceiver_data() -> None:
    conn = _arista_connection()
    transceivers = {"interfaces": {"Ethernet1": {"temperature": 31.2, "voltage": 3.29}}}
    conn._node = _eapi_node(transceivers)

    assert conn.diag_get_platform_transceiver() == transceivers
    conn._node.enable.assert_called_once_with("show interfaces transceiver", encoding="json")


def test_diag_platform_transceiver_reports_unavailable_data() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(pyeapi.eapilib.CommandError(1002, "not supported"))

    assert conn.diag_get_platform_transceiver() == {
        "error": "Transceiver data not available on this device"
    }


def test_diag_interface_counters_combines_traffic_and_error_counters() -> None:
    conn = _arista_connection()
    conn._node = MagicMock()
    conn._node.enable.return_value = [
        {"result": {"interfaces": {"Ethernet1": {"inOctets": 10}}}},
        {"result": {"interfaceErrorCounters": {"Ethernet1": {"fcsErrors": 0}}}},
    ]

    counters = conn.diag_get_interface_counters()

    conn._node.enable.assert_called_once_with(
        ["show interfaces counters", "show interfaces counters errors"], encoding="json"
    )
    assert counters == {
        "counters": {"interfaces": {"Ethernet1": {"inOctets": 10}}},
        "errors": {"interfaceErrorCounters": {"Ethernet1": {"fcsErrors": 0}}},
    }


def test_diag_interface_mac_keeps_only_interfaces_with_addresses() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {
            "interfaces": {
                "Ethernet1": {
                    "physicalAddress": "00:1c:73:00:00:01",
                    "burnedInAddress": "00:1c:73:00:00:01",
                    "mtu": 9214,
                },
                "Management1": {"physicalAddress": "00:1c:73:00:00:99"},
                "Loopback0": {"mtu": 65535},
            }
        }
    )

    assert conn.diag_get_interface_mac() == {
        "Ethernet1": {
            "physicalAddress": "00:1c:73:00:00:01",
            "burnedInAddress": "00:1c:73:00:00:01",
        },
        "Management1": {"physicalAddress": "00:1c:73:00:00:99"},
    }


def test_run_diagnostic_command_serializes_new_arista_command() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(pyeapi.eapilib.CommandError(1002, "not supported"))

    output = conn.run_diagnostic_command("show_platform_transceiver")

    assert json.loads(output) == {"error": "Transceiver data not available on this device"}


# ---------------------------------------------------------------------------
# Password fallback: pyeapi authenticates per request, so a rotated password
# first shows up as a 401 on the first command.
# ---------------------------------------------------------------------------


class _FakeEapiConnection:
    """An eAPI transport that accepts exactly one password."""

    def __init__(self, accepted: str, first: str) -> None:
        self.accepted = accepted
        self.password = first
        self.authentications: list[tuple[str, str]] = []
        self.executed: list[list[object]] = []

    def authentication(self, username: str, password: str) -> None:
        self.authentications.append((username, password))
        self.password = password

    def execute(
        self, commands: list[object], encoding: str, **_kwargs: object
    ) -> dict[str, object]:
        self.executed.append(commands)
        if self.password != self.accepted:
            raise pyeapi.eapilib.ConnectionError("eapi", "Unauthorized. Unauthorized")
        return {"result": [{}, {"hostname": "leaf1"}]}


def _fallback_node(
    connection: _FakeEapiConnection, *others: str
) -> tuple[arista._PasswordFallbackNode, list[str]]:
    """Return the node and the list of passwords it reports as working."""
    remembered: list[str] = []
    node = arista._PasswordFallbackNode(
        connection,
        host="192.0.2.1",
        username="svc",
        passwords=lambda: list(others),
        on_password=remembered.append,
        enablepwd=connection.password,
    )
    return node, remembered


def test_fallback_node_retries_a_rejected_request_with_the_other_passwords() -> None:
    connection = _FakeEapiConnection(accepted="r1", first="r2")
    node, remembered = _fallback_node(connection, "r1")

    result = node.enable("show hostname")

    assert result[0]["result"] == {"hostname": "leaf1"}
    assert connection.authentications == [("svc", "r1")]
    assert remembered == ["r1"]
    # The enable password follows the login password.
    assert connection.executed[-1][0] == {"cmd": "enable", "input": "r1"}


def test_fallback_node_restores_the_original_password_when_every_password_is_rejected() -> None:
    connection = _FakeEapiConnection(accepted="r9", first="r2")
    node, remembered = _fallback_node(connection, "r1", "r0")

    with pytest.raises(NetworkDeviceException, match="rejected every known password for svc"):
        node.enable("show hostname")
    # Each candidate once, then the original password is put back.
    assert [password for _, password in connection.authentications] == ["r1", "r0", "r2"]
    assert connection.password == "r2"
    assert remembered == []


def test_fallback_node_tries_the_other_passwords_once_per_session() -> None:
    """Each failed login counts towards a lockout, so a failed pass is not repeated."""
    connection = _FakeEapiConnection(accepted="r9", first="r2")
    node, _ = _fallback_node(connection, "r1", "r0")
    with pytest.raises(NetworkDeviceException):
        node.enable("show hostname")
    authentications, requests = len(connection.authentications), len(connection.executed)

    with pytest.raises(NetworkDeviceException, match="other known passwords already failed"):
        node.enable("show hostname")
    assert len(connection.authentications) == authentications
    assert len(connection.executed) == requests + 1


def test_fallback_node_warns_when_another_password_works() -> None:
    connection = _FakeEapiConnection(accepted="r1", first="r2")
    node, _ = _fallback_node(connection, "r1")

    with patch.object(arista, "logger") as logger:
        node.enable("show hostname")
    logger.warning.assert_called_once()
    assert "192.0.2.1" in logger.warning.call_args.args


def test_fallback_node_leaves_other_connection_errors_alone() -> None:
    connection = _FakeEapiConnection(accepted="r2", first="r2")
    node, _ = _fallback_node(connection, "r1")

    with (
        patch.object(
            connection,
            "execute",
            side_effect=pyeapi.eapilib.ConnectionError("eapi", "unable to connect to eAPI"),
        ),
        pytest.raises(pyeapi.eapilib.ConnectionError, match="unable to connect"),
    ):
        node.enable("show hostname")
    assert connection.authentications == []


def test_fallback_node_puts_the_original_password_back_when_a_retry_fails_otherwise() -> None:
    connection = _FakeEapiConnection(accepted="r1", first="r2")
    node, remembered = _fallback_node(connection, "r1")
    errors = [
        pyeapi.eapilib.ConnectionError("eapi", "Unauthorized. Unauthorized"),
        pyeapi.eapilib.ConnectionError("eapi", "timed out"),
    ]

    with (
        patch.object(connection, "execute", side_effect=errors),
        pytest.raises(pyeapi.eapilib.ConnectionError, match="timed out"),
    ):
        node.enable("show hostname")
    assert connection.password == "r2"
    assert remembered == []

    # The fallback is not spent, so the next 401 still tries the other password.
    node.enable("show hostname")
    assert remembered == ["r1"]


def test_fallback_node_keeps_a_password_that_eapi_accepted_for_a_failed_command() -> None:
    connection = _FakeEapiConnection(accepted="r1", first="r2")
    node, remembered = _fallback_node(connection, "r1")
    errors = [
        pyeapi.eapilib.ConnectionError("eapi", "Unauthorized. Unauthorized"),
        pyeapi.eapilib.CommandError(1002, "CLI command 2 of 2 'show hostnme' failed"),
    ]

    with (
        patch.object(connection, "execute", side_effect=errors),
        pytest.raises(pyeapi.eapilib.CommandError),
    ):
        node.enable("show hostnme")
    assert connection.password == "r1"
    assert remembered == ["r1"]


def test_connect_starts_with_the_newest_password_and_keeps_the_rest_for_fallback() -> None:
    conn = AristaConnection.__new__(AristaConnection)
    conn._host, conn._port, conn._username = "192.0.2.1", 443, "svc"
    conn._passwords_to_try, conn._working_password = ["r2", "r1"], None
    connection = _FakeEapiConnection(accepted="r1", first="r2")
    with patch.object(arista.pyeapi, "connect", return_value=connection) as connect:
        node = conn._connect()

    assert connect.call_args.kwargs["password"] == "r2"
    assert isinstance(node, arista._PasswordFallbackNode)
    assert conn._working_password == "r2"
    assert conn._other_passwords() == ["r1"]
    node.enable("show hostname")  # falls back to r1 ...
    assert conn._working_password == "r1"  # ... which is remembered for later connections


def _connection_error(cause: BaseException | None) -> pyeapi.eapilib.ConnectionError:
    """A pyeapi ConnectionError as pyeapi raises it, with the socket error as context."""
    error = pyeapi.eapilib.ConnectionError(
        "192.0.2.1", f"Socket error during eAPI connection: {cause}"
    )
    error.__context__ = cause
    return error


_MISSING_FILE = "Error renaming flash:/{} (No such file or directory)"
_MOVE_ASIDE = [
    "rename flash:startup-config flash:startup-config.nvcm-backup",
    "rename flash:zerotouch-config flash:zerotouch-config.nvcm-backup",
]


def _restore(name: str) -> list[str]:
    """The commands that check for a moved file and rename it back."""
    return [
        f"show file information flash:{name}",
        f"show file information flash:{name}.nvcm-backup",
        f"rename flash:{name}.nvcm-backup flash:{name}",
    ]


_RESTORE = [*_restore("startup-config"), *_restore("zerotouch-config")]


def _no_such_file(name: str) -> pyeapi.eapilib.CommandError:
    """What "show file information" raises for a file that is not on flash."""
    return pyeapi.eapilib.CommandError(
        1000,
        "could not run command",
        command_error=f"Error getting information about flash:{name} (No such file or directory)",
    )


def _restore_answers(name: str) -> list[object]:
    """eAPI answers when the original is gone and the backup is there."""
    return [_no_such_file(name), {}, {}]


_RESTORE_ANSWERS = [*_restore_answers("startup-config"), *_restore_answers("zerotouch-config")]


_RELOAD = "reload in 00:05 force"
_CANCEL = "reload cancel"


def _commands(node: MagicMock) -> list[str]:
    return [c.args[0] for c in node.enable.call_args_list]


def test_execute_ztp_moves_aside_what_blocks_zerotouch_then_schedules_the_reload() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({}, {}, {"output": ""})
    transport = conn._node.connection.transport
    transport.timeout = 300

    conn.execute_ztp()

    assert _commands(conn._node) == [*_MOVE_ASIDE, _RELOAD]
    # Sent once, as text: never replayed in another encoding.
    assert conn._node.enable.call_args.kwargs == {"encoding": "text", "strict": True}
    assert transport.timeout == 300


def test_execute_ztp_sends_every_request_with_the_short_timeout() -> None:
    """Requests that hang must leave time for the rollback inside the activity deadline."""
    conn = _arista_connection()
    conn._node = _eapi_node(
        {}, {}, _connection_error(TimeoutError("timed out")), *_RESTORE_ANSWERS, {}
    )
    transport = conn._node.connection.transport
    transport.timeout = 300
    seen: list[int] = []
    answers = iter(conn._node.enable.side_effect)

    def answer(*_args: object, **_kwargs: object) -> object:
        seen.append(transport.timeout)
        result = next(answers)
        if isinstance(result, BaseException):
            raise result
        return result

    conn._node.enable.side_effect = answer

    with pytest.raises(NetworkDeviceException):
        conn.execute_ztp()
    assert len(seen) == 10
    assert set(seen) == {20}
    assert transport.timeout == 300


def test_execute_ztp_skips_files_that_are_not_there() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {},
        pyeapi.eapilib.CommandError(
            1000, "could not run command", command_error=_MISSING_FILE.format("zerotouch-config")
        ),
        {},
    )

    conn.execute_ztp()

    assert _commands(conn._node) == [*_MOVE_ASIDE, _RELOAD]


def test_execute_ztp_undoes_the_reset_when_the_reload_is_refused() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {}, {}, pyeapi.eapilib.CommandError(1002, "Unavailable command"), *_RESTORE_ANSWERS, {}
    )

    with pytest.raises(NetworkDeviceException, match="did not confirm the reload") as exc:
        conn.execute_ztp()
    assert _commands(conn._node) == [*_MOVE_ASIDE, _RELOAD, *_RESTORE, _CANCEL]
    assert "may still be at" not in str(exc.value)
    assert not isinstance(exc.value, ZTPRollbackException)
    assert not exc.value.non_retryable


@pytest.mark.parametrize(
    "cause",
    [
        TimeoutError("timed out"),
        ConnectionResetError(104, "Connection reset by peer"),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        ConnectionRefusedError(111, "Connection refused"),
        None,  # eAPI answered with an error status, such as a 401
    ],
)
def test_execute_ztp_undoes_the_reset_when_the_reload_is_not_confirmed(
    cause: BaseException | None,
) -> None:
    """Without an answer the reload may or may not be scheduled, so both are undone."""
    conn = _arista_connection()
    conn._node = _eapi_node({}, {}, _connection_error(cause), *_RESTORE_ANSWERS, {})

    with pytest.raises(NetworkDeviceException, match="did not confirm the reload"):
        conn.execute_ztp()
    assert _commands(conn._node) == [*_MOVE_ASIDE, _RELOAD, *_RESTORE, _CANCEL]


def test_execute_ztp_warns_when_the_reload_cannot_be_cancelled() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {},
        {},
        _connection_error(TimeoutError("timed out")),
        *_RESTORE_ANSWERS,
        _connection_error(TimeoutError("timed out")),
    )

    with patch.object(arista, "logger") as logger, pytest.raises(NetworkDeviceException):
        conn.execute_ztp()
    logger.warning.assert_called_once()


def test_execute_ztp_renames_back_what_it_moved_when_a_rename_fails() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {},
        pyeapi.eapilib.CommandError(1000, "could not run command"),
        *_restore_answers("startup-config"),
        {},  # zerotouch-config is still in place
        {},
    )

    with pytest.raises(NetworkDeviceException, match="could not move flash:zerotouch-config"):
        conn.execute_ztp()
    assert _commands(conn._node) == [
        *_MOVE_ASIDE,
        *_restore("startup-config"),
        "show file information flash:zerotouch-config",
        _CANCEL,
    ]


def test_execute_ztp_renames_back_a_file_whose_rename_ran_before_the_connection_dropped() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        _connection_error(TimeoutError("timed out")),
        *_restore_answers("startup-config"),
        {},  # zerotouch-config is in place
        {},
    )

    with pytest.raises(NetworkDeviceException, match="could not move flash:startup-config"):
        conn.execute_ztp()
    assert _commands(conn._node) == [
        _MOVE_ASIDE[0],
        *_restore("startup-config"),
        "show file information flash:zerotouch-config",
        _CANCEL,
    ]


def test_execute_ztp_restores_a_file_an_earlier_attempt_moved() -> None:
    """A retried activity finds startup-config already renamed; the rollback must still restore it."""
    conn = _arista_connection()
    conn._node = _eapi_node(
        pyeapi.eapilib.CommandError(
            1000, "could not run command", command_error=_MISSING_FILE.format("startup-config")
        ),
        pyeapi.eapilib.CommandError(
            1000, "could not run command", command_error=_MISSING_FILE.format("zerotouch-config")
        ),
        _connection_error(TimeoutError("timed out")),
        *_restore_answers("startup-config"),
        _no_such_file("zerotouch-config"),
        _no_such_file("zerotouch-config.nvcm-backup"),
        {},
    )

    with pytest.raises(NetworkDeviceException, match="did not confirm the reload"):
        conn.execute_ztp()
    assert _commands(conn._node) == [
        *_MOVE_ASIDE,
        _RELOAD,
        *_restore("startup-config"),
        "show file information flash:zerotouch-config",
        "show file information flash:zerotouch-config.nvcm-backup",
        _CANCEL,
    ]


def test_execute_ztp_keeps_the_original_over_an_older_backup() -> None:
    """The rename did not run, so the backup left by an earlier reset must not replace the file."""
    conn = _arista_connection()
    conn._node = _eapi_node(_connection_error(ConnectionRefusedError(111, "refused")), {}, {}, {})

    with pytest.raises(NetworkDeviceException, match="could not move flash:startup-config"):
        conn.execute_ztp()
    assert _commands(conn._node) == [
        _MOVE_ASIDE[0],
        "show file information flash:startup-config",
        "show file information flash:zerotouch-config",
        _CANCEL,
    ]


def test_execute_ztp_reports_where_a_file_is_when_renaming_it_back_fails() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node(
        {},
        {},
        pyeapi.eapilib.CommandError(1002, "Unavailable command"),
        _no_such_file("startup-config"),
        {},
        pyeapi.eapilib.CommandError(1000, "could not run command"),
        *_restore_answers("zerotouch-config"),
        {},
    )

    with patch.object(arista, "logger") as logger, pytest.raises(ZTPRollbackException) as exc:
        conn.execute_ztp()
    logger.error.assert_called_once()
    assert "startup-config" in logger.error.call_args.args
    assert str(exc.value).endswith(
        "; flash:startup-config may still be at flash:startup-config.nvcm-backup"
    )
    assert exc.value.non_retryable
    assert _commands(conn._node)[-1] == _CANCEL


def test_execute_ztp_undoes_the_reset_when_every_password_is_rejected() -> None:
    """eAPI stops accepting the credentials after the first rename: still roll back, and report it."""
    rejected = NetworkDeviceException("eAPI on leaf1 rejected every known password for admin")
    conn = _arista_connection()
    conn._node = _eapi_node({}, rejected, rejected, rejected, rejected, rejected)

    with pytest.raises(ZTPRollbackException, match="could not move flash:zerotouch-config") as exc:
        conn.execute_ztp()
    assert str(exc.value).endswith(
        "; flash:startup-config may still be at flash:startup-config.nvcm-backup"
        "; flash:zerotouch-config may still be at flash:zerotouch-config.nvcm-backup"
    )
    assert _commands(conn._node) == [
        *_MOVE_ASIDE,
        "show file information flash:startup-config",
        "show file information flash:zerotouch-config",
        _CANCEL,
    ]


def test_get_ztp_status_is_success_once_zerotouch_is_disabled() -> None:
    conn = _arista_connection()
    conn._node = _eapi_node({"mode": "disabled", "scriptTimeout": 900})
    assert conn.get_ztp_status() == "success"

    conn._node = _eapi_node({"mode": "enabled", "scriptTimeout": 900})
    assert conn.get_ztp_status() == "enabled"
