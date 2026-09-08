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

import pytest

from nv_config_manager.temporal.client.device import (
    DiffChangedException,
    MellanoxConnection,
    NetworkDeviceException,
)


def _mellanox_connection(*, port: int = 22) -> MellanoxConnection:
    conn = MellanoxConnection.__new__(MellanoxConnection)
    conn._host = "192.0.2.1"
    conn._port = port
    conn._username = "admin"
    conn._passwords_to_try = ["pw"]
    conn._working_password = None
    conn.client = None
    return conn


@patch("nv_config_manager.temporal.client.device.mellanox.ConnectHandler")
def test_connect_passes_configured_ssh_port(mock_connect_handler):
    """ConnectHandler receives the connection's SSH port, not only the default 22."""
    mock_connect_handler.return_value = MagicMock()
    conn = _mellanox_connection(port=2222)

    conn._connect()

    assert mock_connect_handler.call_args.kwargs["port"] == 2222
    assert mock_connect_handler.call_args.kwargs["host"] == "192.0.2.1"


def test_commit_preserves_diff_changed_exception():
    """A mismatched approved diff raises DiffChangedException, not a wrapped failure."""
    conn = _mellanox_connection()
    conn.perform_candidate_diff = MagicMock(return_value="new-diff")

    with pytest.raises(DiffChangedException, match="changed since approval"):
        conn.commit_candidate_config("config", "old-diff")


def test_commit_wraps_other_failures_as_network_device_exception():
    """Unexpected commit errors stay wrapped as NetworkDeviceException."""
    conn = _mellanox_connection()
    conn.perform_candidate_diff = MagicMock(side_effect=RuntimeError("ssh dropped"))

    with pytest.raises(NetworkDeviceException, match="Failed to commit candidate configuration"):
        conn.commit_candidate_config("config", "old-diff")


def test_close_disconnects_netmiko_client():
    """closing() must disconnect the SSH session and tolerate a second close."""
    conn = _mellanox_connection()
    client = MagicMock()
    conn.client = client

    conn.close()

    client.disconnect.assert_called_once_with()
    assert conn.client is None
    conn.close()
    client.disconnect.assert_called_once_with()
