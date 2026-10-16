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

import json
from typing import Any, cast
from unittest.mock import MagicMock, patch

import paramiko
import pytest
import requests
import responses
from pytest_mock import MockerFixture

from nv_config_manager_workflows.clients.device.base import COMMIT_CONFIRM_ROLLBACK_SECONDS
from nv_config_manager_workflows.clients.device.cumulus import CumulusConnection
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigApplyFailureException,
    InvalidConfigException,
)
from nv_config_manager_workflows.clients.device.models import InterfaceNeighborData
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings

_TEST_HOST = "192.0.2.1"

_CUMULUS_DIFF = {
    "added": {
        "interface": {
            "lo": {"description": None},
            "swp1": {"description": "test description"},
        },
        "service": {"syslog": {"mgmt": None}},
    },
    "removed": {
        "interface": {"lo": {"description": "test123"}, "swp1": {"description": None}},
        "service": {
            "syslog": {"mgmt": {"server": {"1.1.1.1": {"port": 32365, "protocol": "udp"}}}}
        },
    },
}

_CUMULUS_DHCP_DIFF = {
    "added": {
        "service": {
            "dhcp-relay": {
                "default": {
                    "interface": {
                        "swp49": {},
                        "swp50": {},
                        "vlan112": {},
                        "vlan12": {},
                    },
                    "server": {"10.91.208.128": {}},
                }
            }
        }
    },
    "removed": {"service": {"dhcp-relay": None}},
}


def _cumulus_connection() -> CumulusConnection:
    settings: DeviceConnectionSettings = {
        "username": "admin",
        "passwords": ["password"],
        "mock": False,
    }
    return CumulusConnection(_TEST_HOST, settings=settings)


def _add_commit_responses(revision: str, *, apply_state: str) -> None:
    base = f"https://{_TEST_HOST}:8765/nvue_v1"
    responses.add(responses.POST, f"{base}/revision", json={revision: {}})
    responses.add(responses.DELETE, f"{base}/", json={})
    responses.add(responses.PATCH, f"{base}/", json={})
    responses.add(
        responses.GET,
        f"{base}/?rev=applied&diff={revision}&filled=false",
        json={},
    )
    responses.add(
        responses.GET,
        f"{base}/?rev={revision}&diff=applied&filled=false",
        json={"interface": {"eth0": {"description": "test"}}},
    )
    responses.add(responses.PATCH, f"{base}/revision/{revision}", json={})
    responses.add(
        responses.GET,
        f"{base}/revision/{revision}",
        json={"state": apply_state},
    )
    responses.add(responses.PATCH, f"{base}/revision/applied", json={})


def _revision_patch_bodies() -> list[dict[str, Any]]:
    revision_url = f"https://{_TEST_HOST}:8765/nvue_v1/revision/"
    bodies: list[dict[str, Any]] = []
    for response_call in responses.calls:
        request = response_call.request
        request_url = request.url or ""
        if (
            request.method != "PATCH"
            or not request_url.startswith(revision_url)
            or "applied" in request_url
            or request.body is None
        ):
            continue
        bodies.append(cast(dict[str, Any], json.loads(request.body)))
    return bodies


@patch("nv_config_manager_workflows.clients.device.cumulus.paramiko.SSHClient")
def test_sftp_download_closes_client_when_connect_fails(mock_ssh_client: MagicMock) -> None:
    """SFTP closes the SSH client when connection setup fails."""
    ssh = mock_ssh_client.return_value
    ssh.connect.side_effect = paramiko.SSHException("connection failed")
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    conn._username = "admin"

    with pytest.raises(paramiko.SSHException, match="connection failed"):
        conn._sftp_download("password", "/tmp/support.tar", None)

    ssh.close.assert_called_once_with()


def test_close_closes_nvue_session() -> None:
    """closing() must release the pooled requests session."""
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    session = MagicMock()
    conn._session = session

    conn.close()

    session.close.assert_called_once_with()
    assert conn._session is None
    conn.close()


@responses.activate
@pytest.mark.parametrize(
    ("diff_response", "expected_diff"),
    [
        (
            _CUMULUS_DIFF,
            "\n".join(
                (
                    "nv unset interface lo description test123",
                    "nv unset service syslog mgmt server 1.1.1.1 port 32365",
                    "nv unset service syslog mgmt server 1.1.1.1 protocol udp",
                    "nv set interface swp1 description test description",
                )
            ),
        ),
        (
            _CUMULUS_DHCP_DIFF,
            "\n".join(
                (
                    "nv set service dhcp-relay default interface swp49",
                    "nv set service dhcp-relay default interface swp50",
                    "nv set service dhcp-relay default interface vlan112",
                    "nv set service dhcp-relay default interface vlan12",
                    "nv set service dhcp-relay default server 10.91.208.128",
                )
            ),
        ),
    ],
)
def test_get_diff_flattens_nvue_responses(
    diff_response: dict[str, Any],
    expected_diff: str,
) -> None:
    conn = _cumulus_connection()
    responses.add(
        responses.GET,
        f"https://{_TEST_HOST}:8765/nvue_v1/?rev=applied&diff=2&filled=false",
        json=diff_response["removed"],
    )
    responses.add(
        responses.GET,
        f"https://{_TEST_HOST}:8765/nvue_v1/?rev=2&diff=applied&filled=false",
        json=diff_response["added"],
    )

    assert conn._get_diff("2") == expected_diff


def test_commit_rejects_invalid_configuration(mocker: MockerFixture) -> None:
    conn = _cumulus_connection()
    mocker.patch.object(conn, "_load_candidate", return_value="2")
    mocker.patch.object(conn, "_get_diff", return_value="approved diff")
    mocker.patch.object(conn, "_apply_config")
    mocker.patch.object(
        conn,
        "_get_revision_state",
        return_value=("invalid", {"progress": "Invalid config"}),
    )

    with pytest.raises(InvalidConfigException) as exc_info:
        conn.commit_candidate_config("configuration", "approved diff")

    assert exc_info.value.non_retryable is True
    assert "Invalid config" in str(exc_info.value)


@pytest.mark.parametrize(
    ("transition", "expected_message"),
    [
        (
            {
                "progress": "Failure during apply. Ignore?",
                "issue": {
                    "00000": {
                        "code": "systemctl",
                        "message": (
                            "Unable to reload-or-restart services (frr): "
                            "Job for frr.service failed."
                        ),
                        "severity": "error",
                    }
                },
            },
            "Failure during apply. Ignore?",
        ),
        (None, "Config apply failed with ignore_fail state"),
    ],
)
def test_commit_reports_ignore_fail_for_manual_recovery(
    mocker: MockerFixture,
    transition: dict[str, Any] | None,
    expected_message: str,
) -> None:
    conn = _cumulus_connection()
    mocker.patch.object(conn, "_load_candidate", return_value="2")
    mocker.patch.object(conn, "_get_diff", return_value="approved diff")
    mocker.patch.object(conn, "_apply_config")
    mocker.patch.object(
        conn,
        "_get_revision_state",
        return_value=("ignore_fail", transition),
    )

    with pytest.raises(ConfigApplyFailureException) as exc_info:
        conn.commit_candidate_config("configuration", "approved diff")

    assert exc_info.value.non_retryable is True
    assert expected_message in str(exc_info.value)
    assert "MANUAL INTERVENTION REQUIRED" in str(exc_info.value)
    assert "nv config apply 2" in str(exc_info.value)


@responses.activate
@pytest.mark.parametrize(
    ("commit_confirm", "apply_state"),
    [(True, "ays"), (False, "applied")],
)
def test_commit_confirm_controls_nvue_apply_and_confirmation(
    mocker: MockerFixture,
    commit_confirm: bool,
    apply_state: str,
) -> None:
    revision = "rev-1"
    _add_commit_responses(revision, apply_state=apply_state)
    mocker.patch("nv_config_manager_workflows.clients.device.cumulus.time.sleep")
    conn = _cumulus_connection()

    conn.commit_candidate_config(
        "- set:\n  interface:\n    eth0:\n      description: test",
        "nv set interface eth0 description test",
        commit_confirm=commit_confirm,
    )

    bodies = _revision_patch_bodies()
    apply_body = bodies[0]
    assert apply_body["state"] == "apply"
    assert apply_body["auto-prompt"]["ays"] == "ays_yes"
    if commit_confirm:
        assert apply_body["auto-prompt"]["confirm"] == "confirm_yes"
        assert apply_body["state-controls"]["confirm"] == COMMIT_CONFIRM_ROLLBACK_SECONDS
        assert bodies[1] == {"state": "apply", "auto-prompt": {"ays": "ays_yes"}}
    else:
        assert "confirm" not in apply_body["auto-prompt"]
        assert "state-controls" not in apply_body
        assert len(bodies) == 1


def test_get_diff_raises_when_added_direction_response_fails() -> None:
    """A failed added-direction GET must not be flattened into nv set lines."""

    conn = CumulusConnection.__new__(CumulusConnection)
    conn._base_url = f"https://{_TEST_HOST}:8765/nvue_v1/"
    removed = MagicMock()
    removed.raise_for_status = MagicMock()
    removed.json.return_value = {}
    added = MagicMock()
    added.raise_for_status.side_effect = requests.HTTPError("500")
    added.json.return_value = {"interface": {"swp1": {"description": "should-not-apply"}}}
    setattr(conn, "get", MagicMock(side_effect=[removed, added]))

    with pytest.raises(requests.HTTPError):
        conn._get_diff("rev-1")

    added.json.assert_not_called()


def test_get_mac_table_parses_domains_and_keeps_the_newest_duplicate() -> None:
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    conn._base_url = f"https://{_TEST_HOST}:8765/nvue_v1/"
    setattr(conn, "get_bridge_domains", MagicMock(return_value={"br_default": {}}))
    response = MagicMock()
    response.json.return_value = {
        "old": {
            "mac": "00:11:22:33:44:55",
            "interface": "swp1",
            "vlan": 100,
            "last-update": 20,
        },
        "new": {
            "mac": "00:11:22:33:44:55",
            "interface": "swp2",
            "vlan": 100,
            "last-update": 10,
        },
        "local": {
            "mac": "00:11:22:33:44:66",
            "interface": "swp3",
            "last-update": 1,
        },
    }
    setattr(conn, "get", MagicMock(return_value=response))

    table = conn.get_mac_table()

    assert table.by_mac["00-11-22-33-44-55"].interface == "swp2"
    assert table.by_interface == {
        "swp1": ["00-11-22-33-44-55"],
        "swp2": ["00-11-22-33-44-55"],
    }


def test_get_interface_connections_combines_link_state_and_lldp() -> None:
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    neighbor = InterfaceNeighborData(device_name="spine-1", name="Ethernet1")
    setattr(conn, "_get_all_lldp_data", MagicMock(return_value={"swp1": neighbor}))
    setattr(
        conn,
        "get_interfaces",
        MagicMock(
            return_value={
                "swp1": {"type": "swp", "link": {"oper-status": "up"}},
                "swp2": {"type": "swp", "link": {"state": {"down": {}}}},
                "lo": {"type": "loopback", "link": {"oper-status": "up"}},
            }
        ),
    )

    output = conn.get_interface_connections()

    assert output.neighbors == {"swp1": neighbor}
    assert output.link_states == {"swp1": True, "swp2": False}
