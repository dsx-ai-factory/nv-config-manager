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
"""Tests for the standalone certificate rotation scheduler."""

from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest

from nv_config_manager.temporal.ngc.schedulers.certificate_rotation import (
    CertificateRotationScheduler,
)


def test_certificate_rotation_runs_nightly_utc() -> None:
    """Certificate rotation retains its nightly UTC schedule and jitter."""
    assert CertificateRotationScheduler.SPEC.cron_expressions == ["0 2 * * *"]
    assert CertificateRotationScheduler.SPEC.time_zone_name == "UTC"


@pytest.mark.asyncio
async def test_scheduling_is_optional_for_dcim_providers(monkeypatch) -> None:
    """Providers without certificate enumeration reconcile no schedules."""

    class ProviderWithoutCertificateScheduling:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

    config = Mock()
    config.has_section.return_value = True
    config.getboolean.return_value = False
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.certificate_rotation.load_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.certificate_rotation.create_dcim_client",
        ProviderWithoutCertificateScheduling,
    )

    scheduler = CertificateRotationScheduler()

    assert await scheduler.devices_to_schedule() == set()


@pytest.mark.asyncio
async def test_scheduling_is_skipped_without_pki(monkeypatch) -> None:
    """A deployment without PKI configuration does not create schedules."""
    config = Mock()
    config.has_section.return_value = False
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.certificate_rotation.load_config",
        lambda: config,
    )

    scheduler = CertificateRotationScheduler()

    assert await scheduler.devices_to_schedule() == set()


@pytest.mark.asyncio
async def test_devices_to_schedule_returns_provider_ids(monkeypatch) -> None:
    """The scheduler uses the downstream provider's certificate enumeration."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_certificate_enabled_device_ids.return_value = ["device1", "device2"]
    config = Mock()
    config.has_section.return_value = True
    config.getboolean.return_value = True
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.certificate_rotation.load_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "nv_config_manager.temporal.ngc.schedulers.certificate_rotation.create_dcim_client",
        lambda: client,
    )

    scheduler = CertificateRotationScheduler()

    assert await scheduler.devices_to_schedule() == {"device1", "device2"}
    client.get_certificate_enabled_device_ids.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_scheduled_devices_ignores_other_schedule_types() -> None:
    """The standalone scheduler manages only certificate schedule IDs."""
    schedules = [
        Mock(id="backup-device1"),
        Mock(id="certificate-rotation-device2"),
        Mock(id="certificate-rotation-device3"),
        Mock(id="other-schedule"),
    ]
    mock_list_schedules = AsyncMock()
    mock_list_schedules.__aiter__.return_value = schedules
    mock_client = AsyncMock()
    mock_client.list_schedules.return_value = mock_list_schedules

    scheduler = CertificateRotationScheduler()
    scheduled_devices = await scheduler.scheduled_devices(mock_client)

    assert scheduled_devices == {"device2", "device3"}
    mock_client.list_schedules.assert_awaited_once_with()


@pytest.mark.asyncio
@patch(
    "nv_config_manager.temporal.ngc.schedulers.certificate_rotation."
    "CertificateRotationScheduler.temporal_client"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.certificate_rotation."
    "CertificateRotationScheduler.devices_to_schedule"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.certificate_rotation."
    "CertificateRotationScheduler.scheduled_devices"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.certificate_rotation."
    "CertificateRotationScheduler.schedule_device"
)
@patch(
    "nv_config_manager.temporal.ngc.schedulers.certificate_rotation."
    "CertificateRotationScheduler.unschedule_device"
)
async def test_reconcile_schedules(
    unschedule_device_mock,
    schedule_device_mock,
    scheduled_devices_mock,
    devices_to_schedule_mock,
    temporal_client_mock,
) -> None:
    """Certificate reconciliation adds and removes only its own schedules."""
    devices_to_schedule_mock.return_value = {"device1", "device2"}
    scheduled_devices_mock.return_value = {"device2", "device3"}

    scheduler = CertificateRotationScheduler()
    await scheduler.reconcile_schedules()

    schedule_device_mock.assert_called_once_with("device1", ANY)
    unschedule_device_mock.assert_called_once_with("device3", ANY)
