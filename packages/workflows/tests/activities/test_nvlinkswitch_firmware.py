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
"""Tests for package-owned NVLink switch firmware activities."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_dcim.models import FirmwareBundle, FirmwareComponent
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pytest_mock import MockerFixture
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import nvlinkswitch_firmware
from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    CompareRunningDesiredInput,
    GetRunningFirmwareInput,
    RebootDeviceInput,
    UpdateDeviceContextInput,
    ValidateRenderTargetsInput,
    ValidateTargetFilesInput,
    compare_running_desired,
    get_running_firmware,
    helpers,
    reboot_device,
    update_device_context,
    validate_render_targets,
    validate_target_files,
)
from nv_config_manager_workflows.runtime import (
    ConfigStoreNotConfiguredError,
    ConfigStoreRuntime,
    DCIMNotConfiguredError,
    ZTPClientNotConfiguredError,
)


def _device() -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-id",
        name="nvlink-1",
        platform=Platform.CUMULUS_LINUX,
        role="nvlink-switch",
        site="site-1",
        device_type="switch",
        primary_ip4="192.0.2.10",
        primary_ip6=None,
    )


def _bundle(
    *, reported_version: str | None = "1.2.3", file_name: str | None = "cpld.bin"
) -> FirmwareBundle:
    return FirmwareBundle(
        version="bundle-1",
        desired_os_version="5.0.0",
        components={
            "cpld": FirmwareComponent(
                reported_version=reported_version,
                file_name=file_name,
                source_path="cumulus/1.2.3/cpld.bin",
            )
        },
    )


def _async_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def test_get_running_firmware_normalizes_components_and_payloads(
    mocker: MockerFixture,
) -> None:
    connection = MagicMock()
    connection.get_firmware_versions.return_value = {
        "CPLD1": {"actual-firmware": "1.2.3"},
        "BIOS": 7,
    }
    mocker.patch.object(nvlinkswitch_firmware, "get_device_connection", return_value=connection)

    result = get_running_firmware(GetRunningFirmwareInput(device_data=_device()))

    assert result.running_firmware == {"cpld1": "1.2.3", "bios": "7"}


def test_get_running_firmware_wraps_device_failures(mocker: MockerFixture) -> None:
    connection = MagicMock()
    connection.get_firmware_versions.side_effect = ValueError("unavailable")
    mocker.patch.object(nvlinkswitch_firmware, "get_device_connection", return_value=connection)

    with pytest.raises(ApplicationError, match="Failed to get running firmware: unavailable"):
        get_running_firmware(GetRunningFirmwareInput(device_data=_device()))


async def test_compare_running_desired_maps_cpld1_and_detects_os_changes(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_desired_firmware_and_os_from_context",
        new=AsyncMock(return_value=({"cpld": "1.2.3"}, "5.0.0")),
    )

    current = await compare_running_desired(
        CompareRunningDesiredInput(
            device_data=_device(),
            running_os="5.0.0",
            running_firmware={"cpld1": "1.2.3"},
            bundle_version="bundle-1",
        )
    )
    changed = await compare_running_desired(
        CompareRunningDesiredInput(
            device_data=_device(),
            running_os="4.0.0",
            running_firmware={},
            bundle_version="bundle-1",
        )
    )

    assert current.upgrade_needed is False
    assert current.differences == {}
    assert changed.upgrade_needed is True
    assert changed.differences == {"cpld": {"actual": "", "expected": "1.2.3"}}


async def test_missing_reported_version_keeps_layered_failure_text(
    mocker: MockerFixture,
) -> None:
    client = _async_client()
    client.get_firmware_bundle = AsyncMock(return_value=_bundle(reported_version=None))
    mocker.patch.object(helpers, "get_dcim_client", return_value=client)

    with pytest.raises(ApplicationError) as exc_info:
        await compare_running_desired(
            CompareRunningDesiredInput(
                device_data=_device(),
                running_os="5.0.0",
                running_firmware={},
                bundle_version="bundle-1",
            )
        )

    assert exc_info.value.message.startswith(
        "Failed to compare versions: Failed to get desired firmware and OS: "
        "No reported version found for cpld in bundle bundle-1."
    )


async def test_dcim_bundle_failure_keeps_all_error_prefixes(
    mocker: MockerFixture,
) -> None:
    client = _async_client()
    client.get_firmware_bundle = AsyncMock(side_effect=RuntimeError("dcim unavailable"))
    mocker.patch.object(helpers, "get_dcim_client", return_value=client)

    with pytest.raises(ApplicationError) as exc_info:
        await compare_running_desired(
            CompareRunningDesiredInput(
                device_data=_device(),
                running_os="5.0.0",
                running_firmware={},
                bundle_version="bundle-1",
            )
        )

    error = exc_info.value
    assert error.message == (
        "Failed to compare versions: Failed to get desired firmware and OS: "
        "Failed to get device firmware bundle: dcim unavailable"
    )
    assert error.type is None
    assert error.details == ()
    assert error.non_retryable is False


async def test_compare_running_desired_wraps_bundle_helper_failure(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        helpers,
        "get_firmware_bundle",
        new=AsyncMock(side_effect=ValueError("bundle failed")),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await compare_running_desired(
            CompareRunningDesiredInput(
                device_data=_device(),
                running_os="4.0.0",
                running_firmware={},
                bundle_version="bundle-1",
            )
        )

    error = exc_info.value
    assert error.message == (
        "Failed to compare versions: Failed to get desired firmware and OS: bundle failed"
    )
    assert error.type is None
    assert error.details == ()
    assert error.non_retryable is False


async def test_dcim_configuration_errors_are_not_wrapped(
    mocker: MockerFixture,
) -> None:
    error = DCIMNotConfiguredError("DCIM client is disabled")
    mocker.patch.object(helpers, "get_dcim_client", side_effect=error)

    activity_calls = (
        compare_running_desired(
            CompareRunningDesiredInput(
                device_data=_device(),
                running_os="5.0.0",
                running_firmware={},
                bundle_version="bundle-1",
            )
        ),
        update_device_context(
            UpdateDeviceContextInput(device_data=_device(), bundle_version="bundle-1")
        ),
    )

    for activity_call in activity_calls:
        with pytest.raises(DCIMNotConfiguredError) as exc_info:
            await activity_call

        assert exc_info.value is error
        assert exc_info.value.non_retryable is True


async def test_update_device_context_reads_bundle_then_writes_exact_intent(
    mocker: MockerFixture,
) -> None:
    bundle_client = _async_client()
    bundle_client.get_firmware_bundle = AsyncMock(return_value=_bundle())
    update_client = _async_client()
    update_client.set_device_firmware_intent = AsyncMock()
    get_bundle_client = mocker.patch.object(
        helpers,
        "get_dcim_client",
        return_value=bundle_client,
    )
    get_update_client = mocker.patch.object(
        nvlinkswitch_firmware,
        "get_dcim_client",
        return_value=update_client,
    )

    await update_device_context(
        UpdateDeviceContextInput(device_data=_device(), bundle_version="bundle-1")
    )

    get_bundle_client.assert_called_once_with()
    get_update_client.assert_called_once_with()
    update_client.set_device_firmware_intent.assert_awaited_once_with(
        "device-id", "bundle-1", "5.0.0"
    )


async def test_validate_render_targets_accepts_all_expected_files(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_firmware_bundle",
        new=AsyncMock(return_value=_bundle()),
    )
    config_client = _async_client()
    config_client.load_file = AsyncMock(
        return_value=SimpleNamespace(content="install /firmware/cpld.bin")
    )
    runtime = MagicMock(spec=ConfigStoreRuntime)
    runtime.client.return_value = config_client
    mocker.patch.object(nvlinkswitch_firmware, "get_config_store_runtime", return_value=runtime)
    heartbeat = mocker.patch.object(nvlinkswitch_firmware.activity, "heartbeat")
    start = datetime(2026, 1, 1)
    now = mocker.patch.object(nvlinkswitch_firmware, "datetime")
    now.now.side_effect = [start, start, start]

    await validate_render_targets(
        ValidateRenderTargetsInput(device_data=_device(), desired_firmware={"cpld": "1.2.3"})
    )

    runtime.client.assert_called_once_with(ConfigStoreType.INTENDED)
    config_client.load_file.assert_awaited_once_with(
        device_uuid="device-id", filename="fwupdate-commands.txt"
    )
    heartbeat.assert_called_once_with("Validating targets (0m)")


async def test_validate_render_targets_reports_missing_bundle_component(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_firmware_bundle",
        new=AsyncMock(return_value=_bundle()),
    )
    config_client = _async_client()
    config_client.load_file = AsyncMock(return_value=SimpleNamespace(content=""))
    runtime = MagicMock(spec=ConfigStoreRuntime)
    runtime.client.return_value = config_client
    mocker.patch.object(nvlinkswitch_firmware, "get_config_store_runtime", return_value=runtime)
    start = datetime(2026, 1, 1)
    now = mocker.patch.object(nvlinkswitch_firmware, "datetime")
    now.now.side_effect = [start, start, start, start + timedelta(minutes=3)]
    mocker.patch.object(nvlinkswitch_firmware.asyncio, "sleep", new_callable=AsyncMock)
    mocker.patch.object(nvlinkswitch_firmware.activity, "heartbeat")

    with pytest.raises(ApplicationError) as exc_info:
        await validate_render_targets(
            ValidateRenderTargetsInput(device_data=_device(), desired_firmware={"bios": "2.0"})
        )

    assert exc_info.value.message == (
        "Failed to validate render targets: Timeout waiting for firmware commands "
        "to be rendered with new targets. Last check showed missing files: "
        "['bios: no file info found']"
    )


async def test_validate_render_targets_preserves_configuration_errors(
    mocker: MockerFixture,
) -> None:
    error = ConfigStoreNotConfiguredError("Config Store runtime is disabled")
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_firmware_bundle",
        new=AsyncMock(return_value=_bundle()),
    )
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_config_store_runtime",
        side_effect=error,
    )

    with pytest.raises(ConfigStoreNotConfiguredError) as exc_info:
        await validate_render_targets(
            ValidateRenderTargetsInput(
                device_data=_device(),
                desired_firmware={"cpld": "1.2.3"},
            )
        )

    assert exc_info.value is error
    assert exc_info.value.non_retryable is True


async def test_validate_target_files_aggregates_missing_paths(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_firmware_bundle",
        new=AsyncMock(return_value=_bundle()),
    )
    ztp = _async_client()
    ztp.check_file_exists = AsyncMock(return_value=False)
    mocker.patch.object(nvlinkswitch_firmware, "get_ztp_client", return_value=ztp)

    with pytest.raises(ApplicationError) as exc_info:
        await validate_target_files(
            ValidateTargetFilesInput(
                device_data=_device(), desired_firmware={"cpld": "1.2.3", "bios": "2"}
            )
        )

    assert exc_info.value.message == (
        "Failed to validate target files: Firmware files not found on ZTP server: "
        "['cpld: cumulus/1.2.3/cpld.bin', "
        "'bios: no s3_path found in firmware info']"
    )


async def test_validate_target_files_preserves_configuration_errors(
    mocker: MockerFixture,
) -> None:
    error = ZTPClientNotConfiguredError("ZTP client is disabled")
    mocker.patch.object(
        nvlinkswitch_firmware,
        "get_firmware_bundle",
        new=AsyncMock(return_value=_bundle()),
    )
    mocker.patch.object(nvlinkswitch_firmware, "get_ztp_client", side_effect=error)

    with pytest.raises(ZTPClientNotConfiguredError) as exc_info:
        await validate_target_files(
            ValidateTargetFilesInput(
                device_data=_device(),
                desired_firmware={"cpld": "1.2.3"},
            )
        )

    assert exc_info.value is error
    assert exc_info.value.non_retryable is True


def test_reboot_device_returns_timestamp_and_wraps_failures(mocker: MockerFixture) -> None:
    connection = MagicMock()
    mocker.patch.object(nvlinkswitch_firmware, "get_device_connection", return_value=connection)
    now = datetime(2026, 1, 1, 12, 30)
    datetime_mock = mocker.patch.object(nvlinkswitch_firmware, "datetime")
    datetime_mock.now.return_value = now

    assert reboot_device(RebootDeviceInput(device_data=_device())).start_time == now.isoformat()

    connection.reboot.side_effect = RuntimeError("refused")
    with pytest.raises(ApplicationError, match="Failed to initiate reboot: refused"):
        reboot_device(RebootDeviceInput(device_data=_device()))
