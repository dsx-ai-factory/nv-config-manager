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

from typing import cast
from unittest.mock import MagicMock

import pytest

from nv_config_manager_workflows.clients.device.arista import AristaConnection
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
