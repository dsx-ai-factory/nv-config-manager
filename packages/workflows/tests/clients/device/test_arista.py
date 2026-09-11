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

from nv_config_manager_workflows.clients.device import (
    AristaConnection,
    ConfigSyntaxException,
    DiffChangedException,
    NetworkDeviceException,
)


def _arista_connection() -> AristaConnection:
    """Return a connection with local state and no network session."""
    conn = AristaConnection.__new__(AristaConnection)
    conn._session_id = "sess-1"
    conn._host = "192.0.2.1"
    conn._node = MagicMock()
    return conn


def test_commit_preserves_diff_changed_exception():
    """A mismatched approved diff raises DiffChangedException, not a wrapped failure."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_abort") as abort,
        patch.object(conn, "_load_candidate_config"),
        patch.object(conn, "_diff", return_value="new-diff"),
        patch.object(conn, "_diff_eq", return_value=False),
    ):
        with pytest.raises(DiffChangedException, match="changed since approval"):
            conn.commit_candidate_config("config", "old-diff")
        abort.assert_called_once()


def test_commit_wraps_other_failures_as_network_device_exception():
    """Unexpected commit errors stay wrapped as NetworkDeviceException."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_abort"),
        patch.object(conn, "_load_candidate_config"),
        patch.object(conn, "_diff", return_value="new-diff"),
        patch.object(conn, "_diff_eq", return_value=True),
        patch.object(conn._node, "enable", side_effect=RuntimeError("eAPI down")),
    ):
        with pytest.raises(NetworkDeviceException, match="Failed to commit session sess-1"):
            conn.commit_candidate_config("config", "new-diff", commit_confirm=False)


def test_commit_preserves_diff_changed_when_abort_fails():
    """Abort failure after a stale diff must not replace DiffChangedException."""
    conn = _arista_connection()
    with (
        patch.object(
            conn, "_abort", side_effect=NetworkDeviceException("Failed to cleanup session sess-1")
        ) as abort,
        patch.object(conn, "_load_candidate_config"),
        patch.object(conn, "_diff", return_value="new-diff"),
        patch.object(conn, "_diff_eq", return_value=False),
    ):
        with pytest.raises(DiffChangedException, match="changed since approval"):
            conn.commit_candidate_config("config", "old-diff")
        abort.assert_called_once()


def test_commit_preserves_config_syntax_when_abort_fails():
    """Abort failure after invalid config must not replace ConfigSyntaxException."""
    conn = _arista_connection()
    with (
        patch.object(
            conn,
            "_load_candidate_config",
            side_effect=ConfigSyntaxException("Invalid configuration supplied."),
        ),
        patch.object(
            conn, "_abort", side_effect=NetworkDeviceException("Failed to cleanup session sess-1")
        ) as abort,
    ):
        with pytest.raises(ConfigSyntaxException, match="Invalid configuration supplied"):
            conn.commit_candidate_config("config", "old-diff")
        abort.assert_called_once()


def test_diff_and_commit_pass_partial_to_load_candidate():
    """Tenant (partial) deploys must not issue rollback clean-config."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_abort"),
        patch.object(conn, "_load_candidate_config") as load_candidate,
        patch.object(conn, "_diff", return_value="new-diff"),
        patch.object(conn, "_diff_eq", return_value=True),
    ):
        conn.perform_candidate_diff("fragment", partial=True)
        load_candidate.assert_called_with("fragment", partial=True)

        load_candidate.reset_mock()
        conn.commit_candidate_config("fragment", "new-diff", partial=True, commit_confirm=False)
        load_candidate.assert_called_with("fragment", partial=True)


def test_diff_and_commit_default_to_full_candidate_load():
    """Full deploys keep the default partial=False load."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_abort"),
        patch.object(conn, "_load_candidate_config") as load_candidate,
        patch.object(conn, "_diff", return_value="new-diff"),
        patch.object(conn, "_diff_eq", return_value=True),
    ):
        conn.perform_candidate_diff("full-config")
        load_candidate.assert_called_with("full-config", partial=False)

        load_candidate.reset_mock()
        conn.commit_candidate_config("full-config", "new-diff", commit_confirm=False)
        load_candidate.assert_called_with("full-config", partial=False)


def test_load_candidate_omits_empty_command_on_partial():
    """Partial loads start the session without a blank rollback placeholder."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_extract_banner_commands", return_value=("hostname leaf1\nend", [])),
        patch.object(conn._node, "run_commands") as run_commands,
    ):
        conn._load_candidate_config("hostname leaf1\nend", partial=True)

    commands = run_commands.call_args[0][0]
    assert commands[0].startswith("configure session ")
    assert "" not in commands
    assert "rollback clean-config" not in commands
    assert commands[1:] == ["hostname leaf1", "end"]


def test_load_candidate_includes_rollback_on_full_load():
    """Full loads still wipe the session with rollback clean-config."""
    conn = _arista_connection()
    with (
        patch.object(conn, "_extract_banner_commands", return_value=("hostname leaf1\nend", [])),
        patch.object(conn._node, "run_commands") as run_commands,
    ):
        conn._load_candidate_config("hostname leaf1\nend", partial=False)

    commands = run_commands.call_args[0][0]
    assert commands[0].startswith("configure session ")
    assert commands[1] == "rollback clean-config"
    assert commands[2:] == ["hostname leaf1", "end"]
