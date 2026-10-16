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
"""Tests for package-owned switch OS activities."""

import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, OSImageVersions, Platform
from pytest_mock import MockerFixture
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import os as os_root
from nv_config_manager_workflows.activities.os import (
    CleanupMlnxOSInput,
    DownloadMlnxOSInput,
    ExecuteZTPInput,
    GetCurrentOSInput,
    GetMlnxOSVersionInput,
    GetOSImageVersionsInput,
    InstallMlnxOSInput,
    PollImageInput,
    PollZTPStatusInput,
    ReloadMlnxOSInput,
    UpdateIntendedOSImageInput,
    WaitRebootInput,
    cleanup_mlnx_os,
    download_mlnx_os,
    execute_ztp,
    get_current_os,
    get_mlnx_os_version,
    get_os_image_versions,
    helpers,
    install_mlnx_os,
    poll_image,
    poll_ztp_status,
    reload_mlnx_os,
    update_intended_os_image,
    wait_reboot,
)
from nv_config_manager_workflows.activities.os import activities as os_activities
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.device.mellanox import MellanoxConnection


@dataclass(frozen=True)
class _MellanoxConnectionFixture:
    """A Mellanox connection and its explicitly typed method mocks."""

    connection: MellanoxConnection
    execute_command: MagicMock
    execute_enable_command: MagicMock
    cleanup: MagicMock


def _device(platform: Platform = Platform.CUMULUS_LINUX) -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-id",
        name="leaf-1",
        platform=platform,
        role="leaf",
        site="site-1",
        device_type="switch",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _connection(mocker: MockerFixture) -> MagicMock:
    connection = MagicMock(spec=NetworkConnection)
    mocker.patch.object(os_activities, "get_device_connection", return_value=connection)
    mocker.patch.object(helpers, "get_device_connection", return_value=connection)
    return connection


def _mellanox_connection(mocker: MockerFixture) -> _MellanoxConnectionFixture:
    connection = object.__new__(MellanoxConnection)
    execute_command = MagicMock()
    execute_enable_command = MagicMock()
    cleanup = MagicMock()
    connection.execute_command = execute_command  # type: ignore[method-assign]
    connection.execute_enable_command = execute_enable_command  # type: ignore[method-assign]
    connection.__del__ = cleanup  # type: ignore[method-assign]
    mocker.patch.object(os_activities, "get_device_connection", return_value=connection)
    mocker.patch.object(helpers, "get_device_connection", return_value=connection)
    return _MellanoxConnectionFixture(
        connection=connection,
        execute_command=execute_command,
        execute_enable_command=execute_enable_command,
        cleanup=cleanup,
    )


def _mock_now(mocker: MockerFixture, *values: datetime) -> MagicMock:
    datetime_mock = mocker.patch.object(os_activities, "datetime", wraps=datetime)
    datetime_mock.now.side_effect = values
    mocker.patch.object(helpers, "datetime", datetime_mock)
    return datetime_mock


def test_get_current_os_returns_image_and_wraps_failures(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    connection.get_running_image.return_value = "5.2.0"

    assert get_current_os(GetCurrentOSInput(device_data=_device())).running_os == "5.2.0"

    connection.get_running_image.side_effect = ValueError("connection failed")
    with pytest.raises(ApplicationError) as exc_info:
        get_current_os(GetCurrentOSInput(device_data=_device()))
    error = exc_info.value
    assert error.message == "Failed to get current OS: connection failed"
    assert error.type is None
    assert error.details == ()
    assert error.non_retryable is False


async def test_get_and_update_os_intent_use_dcim_provider(mocker: MockerFixture) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_os_image_versions = AsyncMock(
        return_value=OSImageVersions("5.0.0", "5.1.0", "192.0.2.10")
    )
    client.set_intended_os_image = AsyncMock()
    provider = mocker.patch.object(os_activities, "get_dcim_client", return_value=client)

    versions = await get_os_image_versions(GetOSImageVersionsInput(device_id="device-id"))
    await update_intended_os_image(
        UpdateIntendedOSImageInput(device_id="device-id", desired_firmware="5.1.0")
    )

    assert versions.model_dump() == {
        "intended_firmware": "5.0.0",
        "desired_firmware": "5.1.0",
        "ztp_ipv4_address": "192.0.2.10",
    }
    assert provider.call_count == 2
    client.get_os_image_versions.assert_awaited_once_with("device-id")
    client.set_intended_os_image.assert_awaited_once_with("device-id", "5.1.0")


def test_execute_ztp_preserves_iso_timestamp(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    now = datetime(2026, 1, 2, 3, 4, 5)
    _mock_now(mocker, now)

    result = execute_ztp(ExecuteZTPInput(device_data=_device()))

    assert result.start_time == "2026-01-02T03:04:05"
    connection.execute_ztp.assert_called_once_with()


def test_poll_image_returns_expected_image_and_heartbeats(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    connection.get_running_image.return_value = "5.1.0"
    now = datetime(2026, 1, 1)
    _mock_now(mocker, now, now, now)
    heartbeat = mocker.patch.object(os_activities.activity, "heartbeat")
    sleep = mocker.patch.object(os_activities.time, "sleep")

    result = poll_image(PollImageInput(device_data=_device(), expected_image="5.1.0"))

    assert result.running_image == "5.1.0"
    heartbeat.assert_called_once_with("Polling image (0m)")
    sleep.assert_not_called()


def test_poll_image_returns_last_seen_image_at_timeout(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    connection.get_running_image.return_value = "5.0.0"
    start = datetime(2026, 1, 1)
    _mock_now(mocker, start, start, start, start + timedelta(minutes=30))
    sleep = mocker.patch.object(os_activities.time, "sleep")
    mocker.patch.object(os_activities.activity, "heartbeat")

    result = poll_image(PollImageInput(device_data=_device(), expected_image="5.1.0"))

    assert result.running_image == "5.0.0"
    sleep.assert_called_once_with(30)


def test_poll_image_wraps_no_reachable_image_at_timeout(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    connection.get_running_image.side_effect = RuntimeError("rebooting")
    start = datetime(2026, 1, 1)
    _mock_now(mocker, start, start, start, start + timedelta(minutes=30))
    mocker.patch.object(os_activities.time, "sleep")
    mocker.patch.object(os_activities.activity, "heartbeat")

    with pytest.raises(ApplicationError) as exc_info:
        poll_image(PollImageInput(device_data=_device(), expected_image="5.1.0"))

    assert exc_info.value.message == "Device did not return running image"


def test_ztp_success_helpers_preserve_timestamp_semantics(mocker: MockerFixture) -> None:
    device = MagicMock(spec=NetworkConnection)
    device.get_ztp_status.return_value = "success"
    device.get_uptime.return_value = 60
    execution_time = datetime(2026, 1, 1)
    _mock_now(mocker, execution_time + timedelta(minutes=2))

    assert helpers._verify_device_rebooted(device, execution_time) is True
    assert helpers.check_ztp_success(device, None) is True

    device.get_ztp_status.return_value = "in-progress"
    assert helpers.check_ztp_success(device, execution_time) is False


def test_verify_device_rebooted_swallows_connection_failure() -> None:
    device = MagicMock(spec=NetworkConnection)
    device.get_uptime.side_effect = RuntimeError("offline")
    assert helpers._verify_device_rebooted(device, datetime(2026, 1, 1)) is False


def test_os_root_does_not_bind_private_helper_implementation_details() -> None:
    private_names = (
        "_check_ztp_success",
        "_mellanox_connection",
        "_verify_device_rebooted",
    )

    assert all(not hasattr(os_root, name) for name in private_names)


def test_poll_ztp_status_supports_legacy_missing_timestamp(mocker: MockerFixture) -> None:
    connection = _connection(mocker)
    connection.get_ztp_status.return_value = "success"
    now = datetime(2026, 1, 1)
    _mock_now(mocker, now, now, now)
    heartbeat = mocker.patch.object(os_activities.activity, "heartbeat")

    result = poll_ztp_status(PollZTPStatusInput(device_data=_device()))

    assert result.success is True
    heartbeat.assert_called_once_with("Polling ZTP (0m)")


def test_poll_ztp_status_timeout_returns_false(mocker: MockerFixture) -> None:
    _connection(mocker)
    start = datetime(2026, 1, 1)
    _mock_now(mocker, start, start + timedelta(minutes=1))

    result = poll_ztp_status(PollZTPStatusInput(device_data=_device(), timeout_minutes=1))

    assert result.success is False


def test_wait_reboot_compares_uptime_with_elapsed_execution_time(
    mocker: MockerFixture,
) -> None:
    connection = _connection(mocker)
    connection.get_uptime.return_value = 60
    execution_time = datetime(2026, 1, 1)
    now = execution_time + timedelta(minutes=2)
    _mock_now(mocker, now, now, now, now)
    heartbeat = mocker.patch.object(os_activities.activity, "heartbeat")

    result = wait_reboot(
        WaitRebootInput(
            device_data=_device(),
            ztp_execution_timestamp=execution_time.isoformat(),
        )
    )

    assert result.success is True
    heartbeat.assert_called_once_with("Waiting for reboot (0m)")


def test_get_mlnx_os_version_parses_versions_and_cleans_up(mocker: MockerFixture) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_command.return_value = (
        "Installed version: 3.10.4204-x86\nNext version: 3.11.1000"
    )

    result = get_mlnx_os_version(GetMlnxOSVersionInput(device_data=_device(Platform.MLNX_OS)))

    assert result.current_os_versions == ["3.10.4204", "3.11.1000"]
    fixture.execute_command.assert_called_once_with("show images | include version")
    fixture.cleanup.assert_called_once_with()


def test_get_mlnx_os_version_requires_version_output_and_cleans_up(
    mocker: MockerFixture,
) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_command.return_value = "no image information"

    with pytest.raises(ValueError, match="No version information found in output"):
        get_mlnx_os_version(GetMlnxOSVersionInput(device_data=_device(Platform.MLNX_OS)))

    fixture.cleanup.assert_called_once_with()


def test_download_mlnx_os_preserves_url_command_and_timeout(mocker: MockerFixture) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_enable_command.return_value = "downloaded"

    result = download_mlnx_os(
        DownloadMlnxOSInput(
            device_data=_device(Platform.MLNX_OS),
            ztp_ipv4_address="192.0.2.10",
            intended_version="3.11.1000",
        )
    )

    assert result.model_dump() == {
        "download_status": "downloaded",
        "image_name": "image-X86_64-3.11.1000.img",
    }
    fixture.execute_enable_command.assert_called_once_with(
        command=(
            "image fetch http://192.0.2.10/v1/files/mlnx-os/3.11.1000/image-X86_64-3.11.1000.img"
        ),
        timeout=600,
    )
    fixture.cleanup.assert_called_once_with()


def test_install_mlnx_os_polls_until_install_finishes(mocker: MockerFixture) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_enable_command.side_effect = [
        "install started",
        "install in progress",
        "ready",
    ]
    now = datetime(2026, 1, 1)
    _mock_now(mocker, now, now, now)
    sleep = mocker.patch.object(os_activities.time, "sleep")

    result = install_mlnx_os(
        InstallMlnxOSInput(device_data=_device(Platform.MLNX_OS), image_name="image.img")
    )

    assert result.install_status == "install started"
    assert fixture.execute_enable_command.call_args_list == [
        call(command="image install image.img", timeout=1200),
        call(command="image boot next", timeout=600),
        call(command="image boot next", timeout=600),
    ]
    sleep.assert_called_once_with(60)
    fixture.cleanup.assert_called_once_with()


def test_reload_mlnx_os_preserves_command_socket_and_sleep_order(
    mocker: MockerFixture,
) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_enable_command.side_effect = ["saved", "reloaded"]
    now = datetime(2026, 1, 1)
    _mock_now(mocker, now, now)
    sleep = mocker.patch.object(os_activities.time, "sleep")
    sock = MagicMock()
    sock.connect_ex.return_value = 0
    socket_factory = mocker.patch.object(os_activities.socket, "socket", return_value=sock)

    result = reload_mlnx_os(ReloadMlnxOSInput(device_data=_device(Platform.MLNX_OS)))

    assert result.model_dump() == {
        "save_config_status": "saved",
        "reload_status": "reloaded",
        "is_online": True,
    }
    assert fixture.execute_enable_command.call_args_list == [
        call(command="write memory", timeout=60),
        call(command="reload", timeout=60),
    ]
    sleep.assert_called_once_with(60)
    socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout.assert_called_once_with(5)
    sock.connect_ex.assert_called_once_with(("192.0.2.1", 22))
    sock.close.assert_called_once_with()
    fixture.cleanup.assert_called_once_with()


def test_cleanup_mlnx_os_preserves_delete_command_and_cleanup(mocker: MockerFixture) -> None:
    fixture = _mellanox_connection(mocker)
    fixture.execute_enable_command.return_value = "deleted"

    result = cleanup_mlnx_os(
        CleanupMlnxOSInput(device_data=_device(Platform.MLNX_OS), image_name="image.img")
    )

    assert result.cleanup_status == "deleted"
    fixture.execute_enable_command.assert_called_once_with(
        command="image delete image.img",
        timeout=60,
    )
    fixture.cleanup.assert_called_once_with()


def test_mlnx_activities_reject_non_mellanox_connections(mocker: MockerFixture) -> None:
    mocker.patch.object(
        os_activities,
        "get_device_connection",
        return_value=MagicMock(spec=NetworkConnection),
    )
    mocker.patch.object(
        helpers,
        "get_device_connection",
        return_value=MagicMock(spec=NetworkConnection),
    )

    with pytest.raises(ValueError, match="Failed to create MellanoxConnection"):
        cleanup_mlnx_os(
            CleanupMlnxOSInput(device_data=_device(Platform.MLNX_OS), image_name="image.img")
        )
