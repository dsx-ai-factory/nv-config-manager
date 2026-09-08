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
