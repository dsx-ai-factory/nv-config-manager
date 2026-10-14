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
"""Tests for the provider-backed template updater."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nv_config_manager_templates.version import TemplateVersion

from nv_config_manager.dcim import RenderTemplateVersion
from nv_config_manager.render.producer import load_stale_renders, update_stale_renders

CURRENT_VERSION = "engine=nv-config-manager-templates:1.0.0;plugins=none"
OLDER_VERSION = "engine=nv-config-manager-templates:0.9.0;plugins=none"


@pytest.fixture
def mock_dcim_client():
    """Expose a provider client through the normal lifecycle seam."""
    client = AsyncMock()
    client.get_render_template_versions.return_value = []
    client.get_render_enabled_device_ids.return_value = ["device1", "device2", "device3"]

    @asynccontextmanager
    async def session():
        yield client

    with patch("nv_config_manager.render.producer.dcim_client_session", session):
        yield client


@pytest.mark.asyncio
async def test_load_stale_renders_uses_provider_template_versions(mock_dcim_client):
    """Only devices with an older recorded template version are selected."""
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(device_id="device1", template_version=OLDER_VERSION),
        RenderTemplateVersion(device_id="device2", template_version=CURRENT_VERSION),
        RenderTemplateVersion(device_id="device3", template_version=None),
    ]

    assert await load_stale_renders(CURRENT_VERSION) == ["device1"]
    mock_dcim_client.get_render_template_versions.assert_awaited_once()
    mock_dcim_client.get_render_enabled_device_ids.assert_awaited_once_with(
        is_aggregate_managed=False
    )


@pytest.mark.asyncio
async def test_load_stale_renders_skips_incomparable_versions(mock_dcim_client):
    """Existing version-vector comparison behavior is unchanged."""
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(
            device_id="device1",
            template_version=(
                "engine=nv-config-manager-templates:1.0.0;"
                "plugins=nv-config-manager-dgxc-templates:1.0.0"
            ),
        )
    ]

    assert (
        await load_stale_renders(
            "engine=nv-config-manager-templates:1.0.0;"
            "plugins=nv-config-manager-azure-templates:1.0.0"
        )
        == []
    )


@pytest.mark.asyncio
@patch("nv_config_manager.render.producer.execute_render", new_callable=AsyncMock)
@patch("nv_config_manager.render.producer.create_lock")
async def test_update_stale_renders_locks_and_renders_devices(
    mock_create_lock, mock_execute_render, mock_dcim_client
):
    """The updater retains bounded direct render behavior."""
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(device_id="device1", template_version=OLDER_VERSION),
        RenderTemplateVersion(device_id="device2", template_version=OLDER_VERSION),
    ]
    lock = MagicMock()
    lock.acquire = AsyncMock(return_value=True)
    lock.release = AsyncMock()
    mock_create_lock.return_value = lock

    result = await update_stale_renders(
        desired_version=TemplateVersion.parse(CURRENT_VERSION), concurrency=2, lock_timeout=10
    )

    assert result == 2
    mock_create_lock.assert_any_call("device1", blocking=True, blocking_timeout=10)
    mock_create_lock.assert_any_call("device2", blocking=True, blocking_timeout=10)
    assert mock_execute_render.await_count == 2
    assert lock.release.await_count == 2


@pytest.mark.asyncio
@patch("nv_config_manager.render.producer.execute_render", new_callable=AsyncMock)
@patch("nv_config_manager.render.producer.create_lock")
async def test_update_stale_renders_counts_lock_and_render_failures(
    mock_create_lock, mock_execute_render, mock_dcim_client
):
    """A lock miss or failed render is reported without aborting the updater."""
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(device_id="device1", template_version=OLDER_VERSION),
        RenderTemplateVersion(device_id="device2", template_version=OLDER_VERSION),
    ]
    lock = MagicMock()
    lock.acquire = AsyncMock(side_effect=[False, True])
    lock.release = AsyncMock()
    mock_create_lock.return_value = lock
    mock_execute_render.side_effect = RuntimeError("bad render data")

    result = await update_stale_renders(
        desired_version=TemplateVersion.parse(CURRENT_VERSION), concurrency=1, lock_timeout=10
    )

    assert result == 0
    mock_execute_render.assert_awaited_once()
    lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_stale_renders_skips_empty_enabled_inventory(mock_dcim_client):
    """No version lookup is needed when every device has rendering disabled."""
    mock_dcim_client.get_render_enabled_device_ids.return_value = []

    assert await load_stale_renders(CURRENT_VERSION) == []
    mock_dcim_client.get_render_template_versions.assert_not_awaited()


@pytest.mark.asyncio
@patch("nv_config_manager.render.producer.execute_render", new_callable=AsyncMock)
@patch("nv_config_manager.render.producer.create_lock")
async def test_update_stale_renders_excludes_disabled_devices(
    mock_create_lock, mock_execute_render, mock_dcim_client
):
    """A disabled device with an old version is never locked or rendered."""
    mock_dcim_client.get_render_enabled_device_ids.return_value = ["device1", "device2"]
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(device_id="device1", template_version=OLDER_VERSION),
        RenderTemplateVersion(device_id="device2", template_version=CURRENT_VERSION),
        RenderTemplateVersion(device_id="device3", template_version=OLDER_VERSION),
    ]
    lock = MagicMock()
    lock.acquire = AsyncMock(return_value=True)
    lock.release = AsyncMock()
    mock_create_lock.return_value = lock

    assert await update_stale_renders(desired_version=CURRENT_VERSION) == 1

    mock_dcim_client.get_render_enabled_device_ids.assert_awaited_once_with(
        is_aggregate_managed=False
    )
    mock_dcim_client.get_render_template_versions.assert_awaited_once()
    mock_create_lock.assert_called_once_with("device1", blocking=True, blocking_timeout=120)
    mock_execute_render.assert_awaited_once_with(
        "device1", f"Template version change: {CURRENT_VERSION}", "template-updater"
    )
    lock.release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_aggregate", [False, True])
@patch("nv_config_manager.render.producer.is_aggregate_environment")
@patch("nv_config_manager.render.producer.execute_render", new_callable=AsyncMock)
@patch("nv_config_manager.render.producer.create_lock")
async def test_update_stale_renders_excludes_devices_owned_by_other_environments(
    mock_create_lock,
    mock_execute_render,
    mock_is_aggregate_environment,
    mock_dcim_client,
    is_aggregate,
):
    """Stale synced devices are rendered only by their owning environment."""
    mock_is_aggregate_environment.return_value = is_aggregate
    inventory = {False: ["site-device"], True: ["aggregate-device"]}
    mock_dcim_client.get_render_enabled_device_ids.side_effect = lambda is_aggregate_managed: (
        inventory[is_aggregate_managed]
    )
    mock_dcim_client.get_render_template_versions.return_value = [
        RenderTemplateVersion(device_id=device_id, template_version=OLDER_VERSION)
        for device_id in ("site-device", "aggregate-device")
    ]
    lock = MagicMock()
    lock.acquire = AsyncMock(return_value=True)
    lock.release = AsyncMock()
    mock_create_lock.return_value = lock

    assert await update_stale_renders(desired_version=CURRENT_VERSION) == 1

    owned_device = inventory[is_aggregate][0]
    mock_dcim_client.get_render_enabled_device_ids.assert_awaited_once_with(
        is_aggregate_managed=is_aggregate
    )
    mock_create_lock.assert_called_once_with(owned_device, blocking=True, blocking_timeout=120)
    mock_execute_render.assert_awaited_once_with(
        owned_device, f"Template version change: {CURRENT_VERSION}", "template-updater"
    )
    lock.release.assert_awaited_once()
