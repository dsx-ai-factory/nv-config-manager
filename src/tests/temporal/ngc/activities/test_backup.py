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
"""Test configuration backup activities."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import nv_config_manager.temporal.ngc.activities.backup as backup_activities
from nv_config_manager.dcim import ConfigurationBackupMetadata
from nv_config_manager.temporal.ngc.activities.backup import (
    RecordBackupConfigManagerPluginInput,
    record_backup_config_manager_plugin,
)


def _record_input(
    *, commit_id: str = "7", deployed_commit_id: str | None = None
) -> RecordBackupConfigManagerPluginInput:
    return RecordBackupConfigManagerPluginInput(
        workflow_id="current-workflow",
        device_id="device-id",
        commit_id=commit_id,
        path="SITE/device/startup.yaml",
        user="test-user",
        commit_message="Backup trigger: API User: test-user",
        deployed_commit_id=deployed_commit_id,
    )


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, AsyncMock]:
    """Patch the external clients used by the backup metadata activity."""
    config_store_client = MagicMock()
    config_store_client.file_url.return_value = "https://config-store.example/backup"

    dcim_client = AsyncMock()
    dcim_client.__aenter__.return_value = dcim_client
    monkeypatch.setattr(
        backup_activities, "config_store_client", lambda _file_type: config_store_client
    )
    monkeypatch.setattr(backup_activities, "create_dcim_client", lambda: dcim_client)
    monkeypatch.setattr(
        backup_activities, "config_store_ui_url", lambda: "https://config-store.example"
    )
    return config_store_client, dcim_client


@pytest.mark.asyncio
async def test_record_backup_does_not_report_metadata_only_update_as_new_backup(
    clients: tuple[MagicMock, AsyncMock],
) -> None:
    """A changed intended commit must not imply that Config Store wrote a new backup."""
    _, dcim_client = clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id="previous-intended-commit",
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(_record_input())

    assert changed is False
    assert display.startswith("No diff to previous backup execution:")
    intent = dcim_client.record_configuration_backup.await_args.args[0]
    assert intent.workflow_id == "previous-workflow"
    assert intent.deployed_commit_id is None


@pytest.mark.asyncio
async def test_record_backup_reports_new_config_store_version(
    clients: tuple[MagicMock, AsyncMock],
) -> None:
    """A new Config Store commit is reported as a changed backup."""
    _, dcim_client = clients
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
    assert (
        dcim_client.record_configuration_backup.await_args.args[0].workflow_id == "current-workflow"
    )


@pytest.mark.asyncio
async def test_record_backup_retry_preserves_original_changed_result(
    clients: tuple[MagicMock, AsyncMock],
) -> None:
    """A retry after a completed plugin update still reports the original backup write."""
    _, dcim_client = clients
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


@pytest.mark.asyncio
async def test_record_backup_treats_empty_deployed_commit_as_none(
    clients: tuple[MagicMock, AsyncMock],
) -> None:
    """The plugin's empty-string representation of no deployed commit is unchanged."""
    _, dcim_client = clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id="",
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(_record_input())

    assert changed is False
    assert display.startswith("No diff to previous backup execution:")
    dcim_client.record_configuration_backup.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_backup_normalizes_empty_input_deployed_commit(
    clients: tuple[MagicMock, AsyncMock],
) -> None:
    """An empty input deployed commit matches a stored null value."""
    _, dcim_client = clients
    dcim_client.get_configuration_backup_metadata.return_value = ConfigurationBackupMetadata(
        commit_id="7",
        deployed_commit_id=None,
        workflow_id="previous-workflow",
    )

    changed, display = await record_backup_config_manager_plugin(
        _record_input(deployed_commit_id="")
    )

    assert changed is False
    assert display.startswith("No diff to previous backup execution:")
    dcim_client.record_configuration_backup.assert_not_awaited()
