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

from unittest.mock import MagicMock, patch

import paramiko
import pytest
import requests
from nv_config_manager_dcim import CertificateKind

from nv_config_manager.temporal.client.device import CumulusConnection

_TEST_HOST = "192.0.2.1"


@patch("nv_config_manager.temporal.client.device.cumulus.paramiko.SSHClient")
def test_sftp_download_closes_client_when_connect_fails(mock_ssh_client):
    """SFTP closes the SSH client when connection setup fails."""
    ssh = mock_ssh_client.return_value
    ssh.connect.side_effect = paramiko.SSHException("connection failed")
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    conn._username = "admin"

    with pytest.raises(paramiko.SSHException, match="connection failed"):
        conn._sftp_download("password", "/tmp/support.tar", None)

    ssh.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("kind", "resource", "parameter"),
    [
        (CertificateKind.CA, "ca-certificate", "uri"),
        (CertificateKind.IDENTITY, "certificate", "uri-bundle"),
    ],
)
@patch("nv_config_manager.temporal.client.device.cumulus.time.sleep")
def test_import_certificate_uses_nvue_action_and_waits(_mock_sleep, kind, resource, parameter):
    """Rotation uses the Cumulus 5.16 action payload and waits for completion."""
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._base_url = "https://192.0.2.100:8765/nvue_v1/"
    action_created = MagicMock()
    action_created.json.return_value = 42
    conn.post = MagicMock(return_value=action_created)
    running = MagicMock()
    running.json.return_value = {"state": "running"}
    succeeded = MagicMock()
    succeeded.json.return_value = {"state": "action_success"}
    conn.get = MagicMock(side_effect=[running, succeeded])
    uri = "http://192.0.2.10/v1/device/device-id/certificates/otel-client"

    conn.import_certificate("otel-client", kind, uri)

    conn.post.assert_called_once_with(
        f"{conn._base_url}system/security/{resource}/otel-client",
        json={"@import": {"state": "start", "parameters": {parameter: uri}}},
        timeout=120,
    )
    action_created.raise_for_status.assert_called_once_with()
    assert conn.get.call_count == 2


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        (
            "fetch_file",
            {
                "@fetch": {
                    "state": "start",
                    "parameters": {
                        "path": "/tmp/nvcm-certificate.p12",
                        "uri": "sftp://ztp:ztp@192.0.2.10/file",
                        "file-permissions": 600,
                        "vrf": "default",
                    },
                }
            },
        ),
        (
            "delete_file",
            {
                "@delete": {
                    "state": "start",
                    "parameters": {"path": "/tmp/nvcm-certificate.p12"},
                }
            },
        ),
    ],
)
@patch("nv_config_manager.temporal.client.device.cumulus.time.sleep")
def test_file_actions_use_nvue_system_file_path(_mock_sleep, method, payload):
    """Staged certificate files are managed entirely through NVUE REST actions."""
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._base_url = "https://192.0.2.100:8765/nvue_v1/"
    action_created = MagicMock()
    action_created.json.return_value = 42
    conn.post = MagicMock(return_value=action_created)
    succeeded = MagicMock()
    succeeded.json.return_value = {"state": "action_success"}
    conn.get = MagicMock(return_value=succeeded)

    if method == "fetch_file":
        conn.fetch_file(
            "/tmp/nvcm-certificate.p12",
            "sftp://ztp:ztp@192.0.2.10/file",
            "default",
        )
    else:
        conn.delete_file("/tmp/nvcm-certificate.p12")

    conn.post.assert_called_once_with(
        f"{conn._base_url}system/file-path",
        json=payload,
        timeout=120,
    )
    action_created.raise_for_status.assert_called_once_with()


def test_close_closes_nvue_session():
    """closing() must release the pooled requests session."""
    conn = CumulusConnection.__new__(CumulusConnection)
    conn._host = _TEST_HOST
    session = MagicMock()
    conn._session = session

    conn.close()

    session.close.assert_called_once_with()
    assert conn._session is None
    conn.close()


def test_get_diff_raises_when_added_direction_response_fails():
    """A failed added-direction GET must not be flattened into nv set lines."""

    conn = CumulusConnection.__new__(CumulusConnection)
    conn._base_url = f"https://{_TEST_HOST}:8765/nvue_v1/"
    removed = MagicMock()
    removed.raise_for_status = MagicMock()
    removed.json.return_value = {}
    added = MagicMock()
    added.raise_for_status.side_effect = requests.HTTPError("500")
    added.json.return_value = {"interface": {"swp1": {"description": "should-not-apply"}}}
    conn.get = MagicMock(side_effect=[removed, added])

    with pytest.raises(requests.HTTPError):
        conn._get_diff("rev-1")

    added.json.assert_not_called()
