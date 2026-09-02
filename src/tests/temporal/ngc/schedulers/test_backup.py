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
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest

from nv_config_manager.temporal.ngc.schedulers.backup import BackupScheduler


def test_certificate_rotation_runs_nightly_utc():
    assert BackupScheduler.CERTIFICATE_SPEC.cron_expressions == ["0 2 * * *"]
    assert BackupScheduler.CERTIFICATE_SPEC.time_zone_name == "UTC"


@pytest.mark.asyncio
async def test_devices_to_schedule(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_backup_enabled_device_ids.return_value = {"device1", "device2"}
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.backup.create_dcim_client",
        lambda: client,
    )

    scheduler = BackupScheduler()
    devices = await scheduler.devices_to_schedule()

    assert devices == {"device1", "device2"}
    client.get_backup_enabled_device_ids.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_certificate_scheduling_is_optional_for_dcim_providers(monkeypatch):
    class ProviderWithoutCertificateScheduling:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

    config = Mock()
    config.has_section.return_value = True
    config.getboolean.return_value = False
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.backup.load_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.backup.create_dcim_client",
        ProviderWithoutCertificateScheduling,
    )

    scheduler = BackupScheduler()

    assert await scheduler.certificate_devices_to_schedule() == set()


@pytest.mark.asyncio
async def test_scheduled_devices():
    schedules = [
        Mock(id="backup-device1"),
        Mock(id="backup-device2"),
        Mock(id="other-schedule"),
    ]

    mock_list_schedules = AsyncMock()
    mock_list_schedules.__aiter__.return_value = schedules

    mock_client = AsyncMock()
    mock_client.list_schedules.return_value = mock_list_schedules

    scheduler = BackupScheduler()
    scheduled_devices = await scheduler.scheduled_devices(mock_client)

    assert scheduled_devices == {"device1", "device2"}


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.temporal_client")
@patch("nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.devices_to_schedule")
@patch("nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.scheduled_devices")
@patch("nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.schedule_device")
@patch("nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.unschedule_device")
@patch(
    "nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.certificate_devices_to_schedule"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.scheduled_certificate_devices"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.schedule_certificate_device"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.backup.BackupScheduler.unschedule_certificate_device"
)
async def test_reconcile_schedules(
    unschedule_certificate_device_mock,
    schedule_certificate_device_mock,
    scheduled_certificate_devices_mock,
    certificate_devices_to_schedule_mock,
    unschedule_device_mock,
    schedule_device_mock,
    scheduled_devices_mock,
    devices_to_schedule_mock,
    temporal_client_mock,
):
    devices_to_schedule_mock.return_value = {"device1", "device2"}
    scheduled_devices_mock.return_value = {"device2", "device3"}
    certificate_devices_to_schedule_mock.return_value = {"device1", "device4"}
    scheduled_certificate_devices_mock.return_value = {"device1", "device5"}

    scheduler = BackupScheduler()
    await scheduler.reconcile_schedules()

    schedule_device_mock.assert_called_once_with("device1", ANY)
    unschedule_device_mock.assert_called_once_with("device3", ANY)
    schedule_certificate_device_mock.assert_called_once_with("device4", ANY)
    unschedule_certificate_device_mock.assert_called_once_with("device5", ANY)
