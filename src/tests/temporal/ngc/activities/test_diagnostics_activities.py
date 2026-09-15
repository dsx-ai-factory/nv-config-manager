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
"""Tests for run_diagnostic_commands and collect_tech_support_bundle activities.

Code under test:
  src/nv_config_manager/temporal/ngc/activities/diagnostics.py
    - run_diagnostic_commands()   line 86
    - collect_tech_support_bundle()  line 102
    - TechSupportOutput._coerce_bytes  line 71

NetworkConnection.from_device_data (device.py line 568) is patched so no
real SSH or HTTP connections are made.
"""

from unittest.mock import MagicMock, patch

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
    from nv_config_manager.temporal.ngc.activities.diagnostics import (
        RunDiagnosticsInput,
        RunDiagnosticsOutput,
        TechSupportInput,
        TechSupportOutput,
        collect_tech_support_bundle,
        run_diagnostic_commands,
    )


# =============================================================================
# Shared test data
# =============================================================================

TEST_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-cumulus-switch",
    role="tor-switch",
    platform="cumulus-linux",
    site="SITEA",
    device_type="sn5600",
    primary_ip4="192.0.2.100",
    primary_ip6=None,
)

VALID_COMMANDS = ["show_version", "show_interfaces"]


# =============================================================================
# Helpers
# =============================================================================


def _make_mock_connection(
    command_outputs: dict[str, str] | None = None,
    bundle: bytes = b"bundle",
    cl_support_log: str = "mock cl-support log",
) -> MagicMock:
    """Return a mock NetworkConnection with configurable outputs."""
    conn = MagicMock()
    if command_outputs is not None:
        conn.run_diagnostic_command.side_effect = lambda name: command_outputs[name]
    else:
        conn.run_diagnostic_command.return_value = "stub output"
    conn.get_tech_support_bundle.return_value = (bundle, cl_support_log)
    return conn


# =============================================================================
# run_diagnostic_commands
# =============================================================================


def test_run_diagnostic_commands_calls_from_device_data():
    """from_device_data is called with the activity input's device_data."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ) as mock_factory:
        run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    mock_factory.assert_called_once_with(TEST_DEVICE)


def test_run_diagnostic_commands_returns_run_diagnostics_output():
    """Activity returns a RunDiagnosticsOutput instance."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    assert isinstance(result, RunDiagnosticsOutput)


def test_run_diagnostic_commands_device_name_in_output():
    """Output device_name matches the input device's name."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    assert result.device_name == TEST_DEVICE.name


def test_run_diagnostic_commands_returns_outputs_dict():
    """outputs dict has one key per valid command submitted."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    assert set(result.outputs.keys()) == set(VALID_COMMANDS)


def test_run_diagnostic_commands_output_values_are_strings():
    """Each value in outputs is a string."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    for value in result.outputs.values():
        assert isinstance(value, str)


def test_run_diagnostic_commands_per_command_error_captured():
    """When one command raises an exception, its output is 'ERROR: ...',
    other commands are unaffected — the activity never aborts mid-device."""

    def side_effect(name: str) -> str:
        if name == "show_version":
            raise RuntimeError("connection refused")
        return "ok output"

    mock_conn = _make_mock_connection()
    mock_conn.run_diagnostic_command.side_effect = side_effect

    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=VALID_COMMANDS)
        )

    assert result.outputs["show_version"].startswith("ERROR:")
    assert result.outputs["show_interfaces"] == "ok output"


def test_run_diagnostic_commands_error_message_contains_exception():
    """The ERROR: string captures the exception message."""
    mock_conn = _make_mock_connection()
    mock_conn.run_diagnostic_command.side_effect = ValueError("timed out")

    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=["show_version"])
        )

    assert "timed out" in result.outputs["show_version"]


def test_run_diagnostic_commands_invalid_commands_dropped():
    """Unknown command names are filtered out; outputs dict is empty."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=["nonexistent_command"])
        )

    assert result.outputs == {}
    mock_conn.run_diagnostic_command.assert_not_called()


def test_run_diagnostic_commands_empty_commands_list():
    """Empty commands list → empty outputs dict, connection still created."""
    mock_conn = _make_mock_connection()
    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(RunDiagnosticsInput(device_data=TEST_DEVICE, commands=[]))

    assert result.outputs == {}


def test_run_diagnostic_commands_normalizes_command_names():
    """'show version' (spaces) normalizes to 'show_version' as the output key."""
    mock_conn = _make_mock_connection()
    mock_conn.run_diagnostic_command.return_value = "version output"

    with patch(
        "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
        return_value=mock_conn,
    ):
        result = run_diagnostic_commands(
            RunDiagnosticsInput(device_data=TEST_DEVICE, commands=["show version"])
        )

    assert "show_version" in result.outputs
    mock_conn.run_diagnostic_command.assert_called_once_with("show_version")


# =============================================================================
# collect_tech_support_bundle
# =============================================================================

# collect_tech_support_bundle is a sync activity that calls activity.info() for
# the workflow_id and asyncio.run() to save bytes to Redis.  Tests patch both
# so no real Temporal or Redis context is needed.


def _make_mock_activity_info(workflow_id: str = "test-workflow-id") -> MagicMock:
    info = MagicMock()
    info.workflow_id = workflow_id
    return info


def _run_collect_tech_support(mock_conn: MagicMock) -> TechSupportOutput:
    """Call collect_tech_support_bundle with all external deps patched."""
    with (
        patch("nv_config_manager.temporal.ngc.activities.diagnostics.activity.heartbeat"),
        patch(
            "nv_config_manager.temporal.ngc.activities.diagnostics.activity.info",
            return_value=_make_mock_activity_info(),
        ),
        patch("nv_config_manager.temporal.ngc.activities.diagnostics.asyncio.run"),
        patch(
            "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
            return_value=mock_conn,
        ),
    ):
        return collect_tech_support_bundle(TechSupportInput(device_data=TEST_DEVICE))


def test_collect_tech_support_bundle_calls_from_device_data():
    """from_device_data is called with the activity input's device_data."""
    mock_conn = _make_mock_connection()
    with (
        patch("nv_config_manager.temporal.ngc.activities.diagnostics.activity.heartbeat"),
        patch(
            "nv_config_manager.temporal.ngc.activities.diagnostics.activity.info",
            return_value=_make_mock_activity_info(),
        ),
        patch("nv_config_manager.temporal.ngc.activities.diagnostics.asyncio.run"),
        patch(
            "nv_config_manager.temporal.ngc.activities.diagnostics.NetworkConnection.from_device_data",
            return_value=mock_conn,
        ) as mock_factory,
    ):
        collect_tech_support_bundle(TechSupportInput(device_data=TEST_DEVICE))

    mock_factory.assert_called_once_with(TEST_DEVICE)


def test_collect_tech_support_bundle_returns_tech_support_output():
    """Activity returns a TechSupportOutput instance."""
    result = _run_collect_tech_support(_make_mock_connection(bundle=b"tarball bytes"))
    assert isinstance(result, TechSupportOutput)


def test_collect_tech_support_bundle_device_name_in_output():
    """Output device_name matches the input device's name."""
    result = _run_collect_tech_support(_make_mock_connection(bundle=b"data"))
    assert result.device_name == TEST_DEVICE.name


def test_collect_tech_support_bundle_has_redis_key():
    """Output redis_key is set after a successful bundle collection."""
    result = _run_collect_tech_support(_make_mock_connection(bundle=b"tarball bytes"))
    assert isinstance(result.redis_key, str)
