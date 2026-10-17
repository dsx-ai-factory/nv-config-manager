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

import io
import json
import tarfile
from typing import cast
from unittest.mock import MagicMock, call

import pyeapi.eapilib
import pytest

from nv_config_manager_workflows.clients.device import arista
from nv_config_manager_workflows.clients.device.arista import AristaConnection
from nv_config_manager_workflows.clients.device.base import COMMIT_CONFIRM_ROLLBACK_SECONDS
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
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
