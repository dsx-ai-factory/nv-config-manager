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
"""Tests for package-owned diagnostics activities."""

from collections.abc import Callable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager_workflows.activities import diagnostics
from nv_config_manager_workflows.activities.diagnostics import helpers, models
from nv_config_manager_workflows.tech_support import TECH_SUPPORT_BUNDLE_TTL, tech_support_key

TEST_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-cumulus-switch",
    role="tor-switch",
    platform=Platform.CUMULUS_LINUX,
    site="SITEA",
    device_type="sn5600",
    primary_ip4="192.0.2.100",
    primary_ip6=None,
)


def test_package_reexports_models_and_helpers() -> None:
    """The package keeps its public API while definitions live in focused modules."""
    assert diagnostics.RunDiagnosticsInput is models.RunDiagnosticsInput
    assert diagnostics.RunDiagnosticsOutput is models.RunDiagnosticsOutput
    assert diagnostics.TechSupportInput is models.TechSupportInput
    assert diagnostics.TechSupportOutput is models.TechSupportOutput
    assert diagnostics.COMMAND_DESCRIPTIONS is helpers.COMMAND_DESCRIPTIONS
    assert diagnostics.PLATFORM_COMMANDS is helpers.PLATFORM_COMMANDS
    assert diagnostics.get_available_commands is helpers.get_available_commands
    assert diagnostics.validate_commands is helpers.validate_commands


def test_command_catalog_and_normalization_contract() -> None:
    """Catalog lookup preserves platform support, normalization, order, and duplicates."""
    assert diagnostics.PLATFORM_COMMANDS[Platform.MLNX_OS] == set()
    assert diagnostics.PLATFORM_COMMANDS[Platform.UFM] == set()
    assert "show_bgp_summary" not in diagnostics.PLATFORM_COMMANDS[Platform.NV_OS]
    available = diagnostics.get_available_commands(Platform.CUMULUS_LINUX)
    assert list(available) == [
        name
        for name in diagnostics.PLATFORM_COMMANDS[Platform.CUMULUS_LINUX]
        if name in diagnostics.COMMAND_DESCRIPTIONS
    ]
    assert diagnostics.validate_commands(
        Platform.CUMULUS_LINUX,
        [" Show Version ", "show-interfaces", "unsupported", "show version"],
    ) == ["show_version", "show_interfaces", "show_version"]


def test_run_diagnostics_uses_one_closed_connection_and_captures_each_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command failure is rendered inline and does not stop later commands."""
    connection = MagicMock()
    connection.run_diagnostic_command.side_effect = ["version output", RuntimeError("failed")]
    provider = Mock(return_value=connection)
    monkeypatch.setattr(diagnostics, "get_device_connection", provider)

    result = diagnostics.run_diagnostic_commands(
        diagnostics.RunDiagnosticsInput(
            device_data=TEST_DEVICE,
            commands=["show_version", "show_interfaces", "unsupported"],
        )
    )

    assert result == diagnostics.RunDiagnosticsOutput(
        device_name=TEST_DEVICE.name,
        outputs={"show_version": "version output", "show_interfaces": "ERROR: failed"},
    )
    provider.assert_called_once_with(TEST_DEVICE)
    assert connection.run_diagnostic_command.call_args_list == [
        call("show_version"),
        call("show_interfaces"),
    ]
    connection.close.assert_called_once_with()


def test_tech_support_output_defaults_are_empty() -> None:
    """Optional result metadata retains its serialized empty-string defaults."""
    assert diagnostics.TechSupportOutput(device_name="switch").model_dump() == {
        "device_name": "switch",
        "redis_key": "",
        "download_url": "",
        "cl_support_log": "",
    }


def test_collect_tech_support_stores_raw_bytes_and_reports_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundle storage preserves bytes, TTL, key, URL, closure, and heartbeat text."""
    device = TEST_DEVICE.model_copy(update={"name": "switch: 01"})
    content = b"\x00bundle\xff"
    connection = MagicMock()

    def collect(heartbeat: Callable[[], None]) -> tuple[bytes, str]:
        heartbeat()
        return content, "cl-support output"

    connection.get_tech_support_bundle.side_effect = collect
    cache = Mock()
    cache.set = AsyncMock()
    heartbeats = Mock()
    monkeypatch.setattr(diagnostics, "get_device_connection", Mock(return_value=connection))
    monkeypatch.setattr(diagnostics, "get_redis_client", Mock(return_value=cache))
    monkeypatch.setattr(
        diagnostics, "get_api_base_url", Mock(return_value="https://api.example.test/")
    )
    monkeypatch.setattr(
        diagnostics.activity,
        "info",
        Mock(return_value=Mock(workflow_id="workflow: 42")),
    )
    monkeypatch.setattr(diagnostics.activity, "heartbeat", heartbeats)
    monkeypatch.setattr(
        diagnostics, "time", Mock(monotonic=Mock(side_effect=[100.0, 121.9, 145.8]))
    )

    result = diagnostics.collect_tech_support_bundle(
        diagnostics.TechSupportInput(device_data=device)
    )

    key = tech_support_key("workflow: 42", device.name)
    assert result == diagnostics.TechSupportOutput(
        device_name=device.name,
        redis_key=key,
        download_url=("https://api.example.test/v1/workflow/workflow: 42/tech-support/switch: 01"),
        cl_support_log="cl-support output",
    )
    cache.set.assert_awaited_once_with(
        key,
        content,
        ttl=timedelta(hours=24),
        serialize=False,
    )
    assert TECH_SUPPORT_BUNDLE_TTL == timedelta(hours=24)
    connection.close.assert_called_once_with()
    assert heartbeats.call_args_list == [
        call(f"Generating cl-support bundle on {device.name} (21s elapsed)..."),
        call(
            f"Bundle stored in Redis on {device.name} ({len(content)} bytes, 45s total). "
            f"Key: {key}\n\ncl-support output:\ncl-support output"
        ),
    ]


def test_collect_tech_support_allows_intentionally_blank_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured blank external URL disables links without losing bundle storage."""
    connection = MagicMock()
    connection.get_tech_support_bundle.return_value = (b"bundle", "")
    cache = Mock(set=AsyncMock())
    monkeypatch.setattr(diagnostics, "get_device_connection", Mock(return_value=connection))
    monkeypatch.setattr(diagnostics, "get_redis_client", Mock(return_value=cache))
    monkeypatch.setattr(diagnostics, "get_api_base_url", Mock(return_value=""))
    monkeypatch.setattr(diagnostics.activity, "info", Mock(return_value=Mock(workflow_id="wf")))
    monkeypatch.setattr(diagnostics.activity, "heartbeat", Mock())
    monkeypatch.setattr(diagnostics, "time", Mock(monotonic=Mock(side_effect=[1.0, 2.0])))

    result = diagnostics.collect_tech_support_bundle(
        diagnostics.TechSupportInput(device_data=TEST_DEVICE)
    )

    assert result.download_url == ""


def test_collect_tech_support_requires_workflow_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collection still rejects execution outside a workflow context."""
    monkeypatch.setattr(diagnostics.activity, "info", Mock(return_value=Mock(workflow_id=None)))

    try:
        diagnostics.collect_tech_support_bundle(
            diagnostics.TechSupportInput(device_data=TEST_DEVICE)
        )
    except RuntimeError as exc:
        assert str(exc) == "Tech-support collection requires a workflow ID"
    else:
        raise AssertionError("expected missing workflow ID failure")
