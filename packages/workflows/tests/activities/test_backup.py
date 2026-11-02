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
"""Tests for package-owned configuration backup activities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_dcim.models import ConfigurationBackupMetadata
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from pytest_mock import MockerFixture

from nv_config_manager_workflows.activities.backup import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
    load_running_configuration,
    persist_config_backup,
    record_backup_config_manager_plugin,
)
from nv_config_manager_workflows.activities.backup import activities as backup_activities
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


def _config_store_runtime(client: MagicMock) -> MagicMock:
    runtime = MagicMock(spec=ConfigStoreRuntime)
    runtime.client.return_value = client
    runtime.ui_url = "https://config-store.example"
    runtime.default_user_domain = "default.example"
    return runtime


def _persist_input(user_domain: str | None) -> PersistConfigBackupInput:
    return PersistConfigBackupInput(
        device_data=_device(),
        device_running_config="nv set system hostname leaf-1",
        commit_message="scheduled backup",
        user="operator",
        user_domain=user_domain,
    )


def _record_input(
    *, commit_id: str = "7", deployed_commit_id: str | None = None
) -> RecordBackupConfigManagerPluginInput:
    return RecordBackupConfigManagerPluginInput(
        workflow_id="current-workflow",
        device_id="device-id",
        commit_id=commit_id,
        path="SITE/device/startup.yaml",
        user="operator",
        commit_message="scheduled backup",
        deployed_commit_id=deployed_commit_id,
    )


def test_load_running_configuration_uses_provider_and_closes_connection(
    mocker: MockerFixture,
) -> None:
    connection = MagicMock(spec=NetworkConnection)
    connection.get_running_configuration.return_value = "running config"
    provider = mocker.patch.object(
        backup_activities,
        "get_device_connection",
        return_value=connection,
    )
    device = _device()

    assert load_running_configuration(device) == "running config"

    provider.assert_called_once_with(device)
    connection.get_running_configuration.assert_called_once_with()
    connection.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("user_domain", "expected_domain"),
    [(None, "default.example"), ("", ""), ("caller.example", "caller.example")],
)
async def test_persist_config_backup_preserves_user_domain_policy(
    mocker: MockerFixture,
    user_domain: str | None,
    expected_domain: str,
) -> None:
    client = _config_store_client()
    client.persist_files = AsyncMock(return_value=[SimpleNamespace(commit="backup-commit")])
    runtime = _config_store_runtime(client)
    mocker.patch.object(backup_activities, "get_config_store_runtime", return_value=runtime)

    assert await persist_config_backup(_persist_input(user_domain)) == "backup-commit"

    runtime.client.assert_called_once_with(ConfigStoreType.BACKUP)
    client.persist_files.assert_awaited_once_with(
        device_uuid="device-id",
        files={"startup.yaml": "nv set system hostname leaf-1"},
        commit_message="scheduled backup",
        user="operator",
        user_domain=expected_domain,
    )
    client.load_file.assert_not_awaited()
    client.__aexit__.assert_awaited_once_with(None, None, None)


async def test_persist_config_backup_returns_existing_commit_when_nothing_changes(
    mocker: MockerFixture,
) -> None:
    client = _config_store_client()
    client.persist_files = AsyncMock(return_value=[])
    client.load_file = AsyncMock(return_value=SimpleNamespace(commit="existing-commit"))
    mocker.patch.object(
        backup_activities,
        "get_config_store_runtime",
        return_value=_config_store_runtime(client),
    )

    assert await persist_config_backup(_persist_input(None)) == "existing-commit"

    client.load_file.assert_awaited_once_with(
        device_uuid="device-id",
        filename="startup.yaml",
    )


@pytest.fixture
def record_clients(mocker: MockerFixture) -> tuple[MagicMock, MagicMock]:
    config_store_client = _config_store_client()
    config_store_client.file_url.return_value = "https://config-store.example/backup"
    runtime = _config_store_runtime(config_store_client)
    dcim_client = MagicMock()
    dcim_client.__aenter__ = AsyncMock(return_value=dcim_client)
    dcim_client.__aexit__ = AsyncMock(return_value=None)
    dcim_client.get_configuration_backup_metadata = AsyncMock()
    dcim_client.record_configuration_backup = AsyncMock()
    mocker.patch.object(backup_activities, "get_config_store_runtime", return_value=runtime)
    mocker.patch.object(backup_activities, "get_dcim_client", return_value=dcim_client)
    return config_store_client, dcim_client


async def test_record_backup_preserves_metadata_only_workflow(
    record_clients: tuple[MagicMock, MagicMock],
) -> None:
    config_store_client, dcim_client = record_clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id="previous-intended-commit",
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(_record_input())

    assert changed is False
    assert display == (
        "No diff to previous backup execution:\n"
        "[Configuration Backup](https://config-store.example/backup)"
    )
    config_store_client.file_url.assert_called_once_with(
        device_uuid="device-id",
        filename="startup.yaml",
    )
    intent = dcim_client.record_configuration_backup.await_args.args[0]
    assert intent.model_dump() == {
        "device_id": "device-id",
        "config_store_url": "https://config-store.example",
        "commit_id": "7",
        "filename": "startup.yaml",
        "user": "operator",
        "commit_message": "scheduled backup",
        "workflow_id": "previous-workflow",
        "deployed_commit_id": None,
    }


async def test_record_backup_reports_new_config_store_version(
    record_clients: tuple[MagicMock, MagicMock],
) -> None:
    _, dcim_client = record_clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="6",
        deployed_commit_id="intended-commit",
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(
        _record_input(commit_id="7", deployed_commit_id="intended-commit")
    )

    assert changed is True
    assert display.startswith("Persisted new backup configuration:")
    intent = dcim_client.record_configuration_backup.await_args.args[0]
    assert intent.workflow_id == "current-workflow"


@pytest.mark.parametrize("stored_deployed_commit", [None, ""])
async def test_record_backup_normalizes_empty_deployed_commits(
    record_clients: tuple[MagicMock, MagicMock],
    stored_deployed_commit: str | None,
) -> None:
    _, dcim_client = record_clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id=stored_deployed_commit,
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(
        _record_input(deployed_commit_id="")
    )

    assert changed is False
    assert display.startswith("No diff to previous backup execution:")
    dcim_client.record_configuration_backup.assert_not_awaited()


async def test_record_backup_retry_preserves_original_changed_result(
    record_clients: tuple[MagicMock, MagicMock],
) -> None:
    _, dcim_client = record_clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id="intended-commit",
        workflow_id="current-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(
        _record_input(deployed_commit_id="intended-commit")
    )

    assert changed is True
    assert display.startswith("Persisted new backup configuration:")
    dcim_client.record_configuration_backup.assert_not_awaited()
