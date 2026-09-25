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
"""Tests for the workflow-owned base network connection."""

import json
import logging
from unittest.mock import Mock, patch

import pytest
from nv_config_manager_logging import LogCategory

from nv_config_manager_workflows.clients.device import (
    NetworkConnection,
    NetworkDeviceException,
)


def _connection(*, passwords: list[str] | None = None) -> NetworkConnection:
    return NetworkConnection(
        "host",
        22,
        settings={
            "username": "user",
            "passwords": passwords or [],
            "mock": False,
        },
    )


def test_base_connection_copies_password_candidates() -> None:
    passwords = ["new", "old"]
    connection = _connection(passwords=passwords)

    passwords.reverse()

    assert connection._passwords_to_try == ["new", "old"]


def test_rotation_prioritizes_successful_password_without_logging_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    username = "username-sentinel"
    newest = "new-password-sentinel"
    older = "old-password-sentinel"
    connection = NetworkConnection(
        "host",
        22,
        settings={"username": username, "passwords": [newest, older], "mock": False},
    )
    callback = Mock(side_effect=[ValueError(f"{username}: {newest}"), "connected"])

    with caplog.at_level(logging.DEBUG):
        result = connection._try_passwords_with_callback(callback, (ValueError,))

    assert result == "connected"
    assert [call.args[0] for call in callback.call_args_list] == [newest, older]
    assert connection._get_passwords_to_try() == [older, newest]
    assert any(
        getattr(record, "category", None) == LogCategory.TEMPORAL_ACTIVITY
        for record in caplog.records
    )
    for credential in (username, newest, older):
        assert credential not in caplog.text


def test_all_passwords_fail_without_logging_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    username = "username-sentinel"
    password = "password-sentinel"
    failure = ValueError(f"{username}: {password}")
    connection = NetworkConnection(
        "host",
        22,
        settings={"username": username, "passwords": [password], "mock": False},
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(NetworkDeviceException) as caught:
        connection._try_passwords_with_callback(Mock(side_effect=failure), (ValueError,))

    assert caught.value.__cause__ is failure
    assert username not in caplog.text
    assert password not in caplog.text


def test_run_diagnostic_command_dispatches_and_serializes_result() -> None:
    connection = _connection()
    result = {"version": "1.2.3"}

    with patch.object(connection, "diag_get_version", return_value=result) as diagnostic:
        serialized = connection.run_diagnostic_command("show_version")

    diagnostic.assert_called_once_with()
    assert json.loads(serialized) == result


def test_run_diagnostic_command_rejects_unknown_command() -> None:
    connection = _connection()

    with pytest.raises(NetworkDeviceException, match="not supported"):
        connection.run_diagnostic_command("unknown")


def test_run_diagnostic_command_classifies_unimplemented_command() -> None:
    connection = _connection()

    with (
        patch.object(connection, "diag_get_version", side_effect=NotImplementedError),
        pytest.raises(NetworkDeviceException, match="not implemented"),
    ):
        connection.run_diagnostic_command("show_version")


def test_context_manager_closes_connection() -> None:
    connection = _connection()

    with patch.object(connection, "close") as close:
        with connection as entered:
            assert entered is connection

    close.assert_called_once_with()
