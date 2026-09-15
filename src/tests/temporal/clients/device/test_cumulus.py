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
