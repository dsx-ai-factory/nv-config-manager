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
"""Tests for package-owned configuration deployment activities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pytest_mock import MockerFixture
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.deploy import (
    ConfigApplyActivityInput,
    DiffActivityInput,
    LoadPartialConfigurationActivityInput,
    ValidateConfigDiffActivityInput,
    WaitForTenantRenderInput,
    apply_approved_configuration,
    load_intended_configuration,
    load_partial_configuration,
    perform_candidate_diff,
    validate_config_diff,
    wait_for_tenant_render,
)
from nv_config_manager_workflows.activities.deploy import activities as deploy_activities
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.runtime import ConfigStoreRuntime


def _device() -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-id",
        name="leaf-1",
        platform=Platform.CUMULUS_LINUX,
        role="leaf",
        site="site-1",
        device_type="switch",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


def _config_store_client() -> MagicMock:
    client = MagicMock(spec=ConfigStoreClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _configure_client(mocker: MockerFixture, client: MagicMock) -> MagicMock:
    runtime = MagicMock(spec=ConfigStoreRuntime)
    runtime.client.return_value = client
    mocker.patch.object(deploy_activities, "get_config_store_runtime", return_value=runtime)
    return runtime


async def test_load_intended_configuration_returns_content_commit_and_url(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.load_file = AsyncMock(
        return_value=SimpleNamespace(content="intended config", commit="7")
    )
    client.file_url.return_value = "https://config-store/startup.yaml?v=7"
    runtime = _configure_client(mocker, client)

    result = await load_intended_configuration(_device())

    assert result == (
        "intended config",
        "7",
        "https://config-store/startup.yaml?v=7",
    )
    runtime.client.assert_called_once_with(ConfigStoreType.INTENDED)
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="startup.yaml",
    )
    client.file_url.assert_called_once_with(
        device_uuid="device-id",
        filename="startup.yaml",
        version="7",
    )


async def test_load_partial_configuration_uses_latest_file(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.load_file = AsyncMock(return_value=SimpleNamespace(content="tenant", commit="8"))
    client.file_url.return_value = "https://config-store/tenant.yaml?v=8"
    _configure_client(mocker, client)

    result = await load_partial_configuration(
        LoadPartialConfigurationActivityInput(
            device_data=_device(),
            config_file="tenant.yaml",
        )
    )

    assert result == ("tenant", "8", "https://config-store/tenant.yaml?v=8")
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="tenant.yaml",
    )
    client.get_config_file.assert_not_awaited()


async def test_load_partial_configuration_converts_pinned_version_and_values(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.get_config_file = AsyncMock(return_value={"content": 123, "version": 7})
    client.file_url.return_value = "https://config-store/tenant.yaml?v=7"
    _configure_client(mocker, client)

    result = await load_partial_configuration(
        LoadPartialConfigurationActivityInput(
            device_data=_device(),
            config_file="tenant.yaml",
            commit_id="7",
        )
    )

    assert result == ("123", "7", "https://config-store/tenant.yaml?v=7")
    client.get_config_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="tenant.yaml",
        version=7,
    )
    client.load_file.assert_not_awaited()


def test_perform_candidate_diff_passes_partial_and_closes_connection(
    mocker: MockerFixture,
) -> None:
    connection = MagicMock(spec=NetworkConnection)
    connection.perform_candidate_diff.return_value = "candidate diff"
    provider = mocker.patch.object(
        deploy_activities,
        "get_device_connection",
        return_value=connection,
    )
    activity_input = DiffActivityInput(
        device_data=_device(),
        configuration="tenant config",
        partial=True,
    )

    assert perform_candidate_diff(activity_input) == "candidate diff"

    provider.assert_called_once_with(activity_input.device_data)
    connection.perform_candidate_diff.assert_called_once_with("tenant config", partial=True)
    connection.close.assert_called_once_with()


def test_apply_approved_configuration_preserves_all_flags_and_closes_connection(
    mocker: MockerFixture,
) -> None:
    connection = MagicMock(spec=NetworkConnection)
    mocker.patch.object(
        deploy_activities,
        "get_device_connection",
        return_value=connection,
    )

    apply_approved_configuration(
        ConfigApplyActivityInput(
            device_data=_device(),
            configuration="tenant config",
            approved_diff="approved diff",
            partial=True,
            commit_confirm=False,
        )
    )

    connection.commit_candidate_config.assert_called_once_with(
        "tenant config",
        "approved diff",
        commit_confirm=False,
        partial=True,
    )
    connection.close.assert_called_once_with()


def test_validate_config_diff_requires_at_least_one_pattern_type() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        validate_config_diff(
            ValidateConfigDiffActivityInput(tenant_config="tenant", diff="nv set interface swp1")
        )

    error = exc_info.value
    assert error.message == (
        "At least one of 'allowed_patterns' or 'disallowed_patterns' must be provided"
    )
    assert error.type is None
    assert error.details == ()
    assert error.non_retryable is False


def test_validate_config_diff_preserves_blank_and_ordered_invalid_line_behavior() -> None:
    blank = validate_config_diff(
        ValidateConfigDiffActivityInput(
            tenant_config="tenant",
            diff=" \n",
            allowed_patterns=[r"^nv set interface"],
        )
    )
    invalid = validate_config_diff(
        ValidateConfigDiffActivityInput(
            tenant_config="tenant",
            diff=" nv set interface swp1\n\nnv unset system hostname\nnv set router bgp ",
            allowed_patterns=[r"^nv set interface"],
            disallowed_patterns=[r"hostname$"],
        )
    )

    assert blank.model_dump() == {"valid": True, "message": None}
    assert invalid.model_dump() == {
        "valid": False,
        "message": (
            "Validation failed: 2 lines not allowed: "
            "['nv unset system hostname', 'nv set router bgp']"
        ),
    }


async def test_wait_for_tenant_render_without_config_id_only_checks_existence(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.load_file = AsyncMock(return_value=SimpleNamespace(commit="anything"))
    _configure_client(mocker, client)

    result = await wait_for_tenant_render(
        WaitForTenantRenderInput(device=_device(), config_id=None)
    )

    assert result.config_id is None
    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="tenant.yaml",
    )


async def test_wait_for_tenant_render_accepts_newer_numeric_commit(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.load_file = AsyncMock(
        side_effect=[SimpleNamespace(commit="6"), SimpleNamespace(commit="8")]
    )
    _configure_client(mocker, client)
    sleep = mocker.patch.object(deploy_activities.asyncio, "sleep", new_callable=AsyncMock)

    result = await wait_for_tenant_render(
        WaitForTenantRenderInput(
            device=_device(),
            config_id="7",
            interval=2,
            max_attempts=3,
        )
    )

    assert result.config_id == "8"
    assert client.load_file.await_count == 2
    sleep.assert_awaited_once_with(2)


async def test_wait_for_tenant_render_preserves_timeout_calculation_and_cadence(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.load_file = AsyncMock(return_value=SimpleNamespace(commit="3"))
    _configure_client(mocker, client)
    sleep = mocker.patch.object(deploy_activities.asyncio, "sleep", new_callable=AsyncMock)

    with pytest.raises(ApplicationError) as exc_info:
        await wait_for_tenant_render(
            WaitForTenantRenderInput(
                device=_device(),
                config_id="5",
                interval=2,
                max_attempts=3,
            )
        )

    assert exc_info.value.message == "Tenant render not available after 6 seconds"
    assert client.load_file.await_count == 3
    assert sleep.await_args_list == [call(2), call(2), call(2)]
