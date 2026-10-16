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
"""Tests for package-owned Render activities."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient, ConfigStoreFileNotFound
from nv_config_manager_clients.render import FileCommit, RenderClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pytest_mock import MockerFixture
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities import render as render_root
from nv_config_manager_workflows.activities.device_password_rotation import (
    ValidateRenderedPasswordChangeInput,
    validate_rendered_password_change,
)
from nv_config_manager_workflows.activities.device_password_rotation import (
    activities as password_activities,
)
from nv_config_manager_workflows.activities.os import (
    ValidateRenderedImageChangeInput,
    validate_rendered_image_change,
)
from nv_config_manager_workflows.activities.os import helpers as os_helpers
from nv_config_manager_workflows.activities.os import models as os_models
from nv_config_manager_workflows.activities.render import (
    ExecuteRenderInput,
    ExecuteRenderOutput,
    execute_render,
)
from nv_config_manager_workflows.activities.render import activities as render_activities
from nv_config_manager_workflows.runtime import ConfigStoreRuntime, FirmwareStorage


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


def _async_client(spec: type[object] | None = None) -> MagicMock:
    client = MagicMock(spec=spec)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _configure_config_store(mocker: MockerFixture, client: MagicMock) -> MagicMock:
    runtime = MagicMock(spec=ConfigStoreRuntime)
    runtime.client.return_value = client
    mocker.patch.object(render_activities, "get_config_store_runtime", return_value=runtime)
    mocker.patch.object(password_activities, "get_config_store_runtime", return_value=runtime)
    mocker.patch.object(os_helpers, "get_config_store_runtime", return_value=runtime)
    return runtime


async def test_execute_render_uses_separate_clients_and_preserves_snapshot_order(
    mocker: MockerFixture,
) -> None:
    render_client = _async_client(RenderClient)
    render_client.execute_render = AsyncMock(
        return_value=[FileCommit(filename="startup.yaml", commit="6")]
    )
    config_client = _async_client(ConfigStoreClient)
    config_client.list_device_configs = AsyncMock(
        return_value=[
            {"filename": "tenant.yaml", "version": 5},
            {"filename": "startup.yaml", "version": 6},
        ]
    )
    get_render_client = mocker.patch.object(
        render_activities,
        "get_render_client",
        return_value=render_client,
    )
    runtime = _configure_config_store(mocker, config_client)

    result = await execute_render(
        ExecuteRenderInput(device_id="device-id", workflow_id="workflow-id")
    )

    assert result == ExecuteRenderOutput(
        updated_files=[FileCommit(filename="startup.yaml", commit="6")],
        snapshot_files=[
            FileCommit(filename="tenant.yaml", commit="5"),
            FileCommit(filename="startup.yaml", commit="6"),
        ],
    )
    assert result.get_commit("tenant.yaml") == "5"
    assert result.get_commit("missing.yaml") is None
    get_render_client.assert_called_once_with()
    render_client.execute_render.assert_awaited_once_with("device-id", "workflow-id")
    render_client.__aexit__.assert_awaited_once_with(None, None, None)
    runtime.client.assert_called_once_with(ConfigStoreType.INTENDED)
    config_client.list_device_configs.assert_awaited_once_with("device-id")
    config_client.__aexit__.assert_awaited_once_with(None, None, None)


async def test_execute_render_preserves_snapshot_when_no_files_change(
    mocker: MockerFixture,
) -> None:
    render_client = _async_client(RenderClient)
    render_client.execute_render = AsyncMock(return_value=[])
    config_client = _async_client(ConfigStoreClient)
    config_client.list_device_configs = AsyncMock(
        return_value=[
            {"filename": "tenant.yaml", "version": 7},
            {"filename": "startup.yaml", "version": 11},
        ]
    )
    mocker.patch.object(render_activities, "get_render_client", return_value=render_client)
    _configure_config_store(mocker, config_client)

    result = await execute_render(
        ExecuteRenderInput(device_id="device-id", workflow_id="workflow-id")
    )

    assert result == ExecuteRenderOutput(
        updated_files=[],
        snapshot_files=[
            FileCommit(filename="tenant.yaml", commit="7"),
            FileCommit(filename="startup.yaml", commit="11"),
        ],
    )


async def test_execute_render_propagates_client_failure(mocker: MockerFixture) -> None:
    render_client = _async_client(RenderClient)
    error = RuntimeError("render unavailable")
    render_client.execute_render = AsyncMock(side_effect=error)
    mocker.patch.object(render_activities, "get_render_client", return_value=render_client)

    with pytest.raises(RuntimeError, match="render unavailable") as exc_info:
        await execute_render(ExecuteRenderInput(device_id="device-id", workflow_id="workflow-id"))

    assert exc_info.value is error


def test_render_root_does_not_bind_private_helper_implementation_details() -> None:
    private_names = (
        "_IMAGE_RENDER_POLL_INTERVAL_SECONDS",
        "_IMAGE_RENDER_POLL_TIMEOUT",
        "_JUNIPER_INTENDED_CONFIG_FILE",
        "_heartbeat_render_poll",
        "_juniper_firmware_present",
        "_juniper_full_config_present",
        "_validate_cumulus_boot_script_image",
        "_validate_juniper_upgrade_artifacts",
    )

    assert all(not hasattr(render_root, name) for name in private_names)


def test_os_models_preserve_polling_defaults_and_first_matching_commit() -> None:
    output = ExecuteRenderOutput(
        snapshot_files=[
            FileCommit(filename="startup.yaml", commit="7"),
            FileCommit(filename="startup.yaml", commit="8"),
        ]
    )

    assert output.get_commit("startup.yaml") == "7"
    assert output.get_commit("missing.yaml") is None
    assert os_models._IMAGE_RENDER_POLL_TIMEOUT == timedelta(minutes=5)
    assert os_models._IMAGE_RENDER_POLL_INTERVAL_SECONDS == 30
    assert os_models._JUNIPER_INTENDED_CONFIG_FILE == "full-config"


async def test_validate_cumulus_image_matches_exact_version_substring(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    client.load_file = AsyncMock(
        return_value=SimpleNamespace(content="#!/bin/sh\nVERSION_ID=5.0.0\n")
    )
    _configure_config_store(mocker, client)
    heartbeat = mocker.patch.object(os_helpers, "_heartbeat_render_poll")
    sleep = mocker.patch.object(os_helpers.asyncio, "sleep", new_callable=AsyncMock)

    result = await validate_rendered_image_change(
        ValidateRenderedImageChangeInput(
            device_data=_device(),
            desired_image="5.0.0",
        )
    )

    assert result is True
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="boot-script",
    )
    heartbeat.assert_called_once()
    sleep.assert_not_awaited()


async def test_validate_cumulus_image_timeout_message(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    _configure_config_store(mocker, client)
    start = datetime(2026, 1, 1)
    datetime_mock = mocker.patch.object(os_helpers, "datetime")
    datetime_mock.now.side_effect = [start, start + timedelta(minutes=5)]

    with pytest.raises(ApplicationError) as exc_info:
        await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(
                device_data=_device(),
                desired_image="5.0.0",
            )
        )

    assert exc_info.value.message == (
        "Timeout waiting for image version 5.0.0 to be present in boot script"
    )
    client.load_file.assert_not_awaited()


async def test_juniper_full_config_missing_is_not_ready_but_other_errors_propagate() -> None:
    client = _async_client(ConfigStoreClient)
    client.load_file = AsyncMock(side_effect=ConfigStoreFileNotFound("missing"))

    assert await os_helpers._juniper_full_config_present(client, "device-id") is False

    client.load_file.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await os_helpers._juniper_full_config_present(client, "device-id")


async def test_validate_juniper_waits_for_firmware_and_full_config(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    client.load_file = AsyncMock(return_value=SimpleNamespace(content="config"))
    _configure_config_store(mocker, client)
    storage = _async_client()
    storage.firmware_exists = AsyncMock(return_value=True)
    mocker.patch.object(os_helpers, "get_firmware_storage", return_value=storage)
    mocker.patch.object(os_helpers, "_heartbeat_render_poll")

    result = await validate_rendered_image_change(
        ValidateRenderedImageChangeInput(
            device_data=_device(Platform.JUNIPER_JUNOS),
            desired_image="24.4R2",
        )
    )

    assert result is True
    storage.firmware_exists.assert_awaited_once_with("juniper-junos", "24.4R2")
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="full-config",
    )
    client.__aexit__.assert_awaited_once_with(None, None, None)
    storage.__aexit__.assert_awaited_once_with(None, None, None)


async def test_validate_juniper_timeout_preserves_message(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    _configure_config_store(mocker, client)
    storage = _async_client()
    storage.firmware_exists = AsyncMock(return_value=False)
    mocker.patch.object(os_helpers, "get_firmware_storage", return_value=storage)
    start = datetime(2026, 1, 1)
    datetime_mock = mocker.patch.object(os_helpers, "datetime")
    datetime_mock.now.side_effect = [start, start + timedelta(minutes=5)]

    with pytest.raises(ApplicationError) as exc_info:
        await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(
                device_data=_device(Platform.JUNIPER_JUNOS),
                desired_image="24.4R2",
            )
        )

    assert exc_info.value.message == (
        "Timeout waiting for Juniper firmware 24.4R2 and full-config for device device-id"
    )


async def test_validate_rendered_image_rejects_unsupported_platform() -> None:
    with pytest.raises(NotImplementedError, match="^Platform arista-eos not supported$"):
        await validate_rendered_image_change(
            ValidateRenderedImageChangeInput(
                device_data=_device(Platform.ARISTA_EOS),
                desired_image="5.0.0",
            )
        )


async def test_validate_password_change_preserves_match_and_heartbeat(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    client.load_file = AsyncMock(return_value=SimpleNamespace(content="hashed-password secret"))
    _configure_config_store(mocker, client)
    heartbeat = mocker.patch.object(password_activities.activity, "heartbeat")
    start = datetime(2026, 1, 1)
    datetime_mock = mocker.patch.object(password_activities, "datetime")
    datetime_mock.now.side_effect = [start, start, start]

    result = await validate_rendered_password_change(
        ValidateRenderedPasswordChangeInput(
            device_data=_device(),
            desired_password_string="hashed-password secret",
        )
    )

    assert result is True
    heartbeat.assert_called_once_with("Validating password render (0m)")
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="startup.yaml",
    )


async def test_validate_password_change_rejects_missing_filename(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    _configure_config_store(mocker, client)

    with pytest.raises(ApplicationError) as exc_info:
        await validate_rendered_password_change(
            ValidateRenderedPasswordChangeInput(
                device_data=_device(Platform.UFM),
                desired_password_string="secret",
            )
        )

    assert exc_info.value.message == "No intended config filename found for device leaf-1"


async def test_validate_password_change_timeout_does_not_expose_password(
    mocker: MockerFixture,
) -> None:
    client = _async_client(ConfigStoreClient)
    _configure_config_store(mocker, client)
    start = datetime(2026, 1, 1)
    datetime_mock = mocker.patch.object(password_activities, "datetime")
    datetime_mock.now.side_effect = [start, start + timedelta(minutes=5)]

    with pytest.raises(ApplicationError) as exc_info:
        await validate_rendered_password_change(
            ValidateRenderedPasswordChangeInput(
                device_data=_device(),
                desired_password_string="hashed-password secret",
            )
        )

    assert exc_info.value.message == (
        "Timeout waiting for the desired password string to be present in "
        "startup.yaml for device leaf-1"
    )
    assert "hashed-password secret" not in exc_info.value.message
    client.load_file.assert_not_awaited()


def test_firmware_storage_protocol_remains_narrow() -> None:
    assert set(FirmwareStorage.__dict__) >= {
        "__aenter__",
        "__aexit__",
        "firmware_exists",
    }
