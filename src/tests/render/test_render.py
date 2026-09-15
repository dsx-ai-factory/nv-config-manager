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
"""Tests for provider-backed render execution."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nv_config_manager_templates.models import DeviceRenderData, LocationRenderData, RenderData

from nv_config_manager.common.client import ConfigFile, ConfigFileMetadata
from nv_config_manager.common.client.render import FileCommit
from nv_config_manager.dcim import IntendedConfigurationUpdate, RenderDeviceIdentity, RenderLocation
from nv_config_manager.render.render import execute_render

TEMPLATE_VERSION = "engine=nv-config-manager-templates:0.0.1;plugins=none"


def _render_data() -> RenderData:
    """Return the smallest canonical render payload for renderer mocks."""
    location = RenderLocation(name="site-1", kind="Site")
    return RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id="device-id",
                name="device-1",
                platform="Cumulus Linux",
                role="Leaf",
                model="SN5600",
                location=location,
            )
        ),
        location=LocationRenderData(location=location),
    )


@pytest.mark.asyncio
@patch("nv_config_manager.render.render.config_store_client")
@patch("nv_config_manager.render.render.Renderer")
@patch("nv_config_manager.render.render.template_version_key", return_value=TEMPLATE_VERSION)
@patch("nv_config_manager.render.render.datetime")
@patch(
    "nv_config_manager.render.render.config_store_ui_url",
    return_value="https://config-manager.example.com/",
)
async def test_execute_render_uses_provider_for_data_and_state(
    mock_config_store_ui_url,
    mock_datetime,
    mock_template_version_key,
    mock_renderer,
    mock_config_store,
):
    """Render inputs and resulting intended-config state cross the provider API."""
    dcim_client = AsyncMock()
    render_data = _render_data()
    dcim_client.get_render_data.return_value = render_data

    @asynccontextmanager
    async def session():
        yield dcim_client

    mock_renderer.return_value.render_entrypoints.return_value = {
        "startup.yaml": "test",
        "boot-script": "test",
    }
    mock_renderer.return_value.plugin_data_requirements = {}
    config_store = AsyncMock()
    config_store.persist_files.return_value = [
        ConfigFileMetadata(commit="12345", filename="startup.yaml")
    ]
    config_store.__aenter__.return_value = config_store
    mock_config_store.return_value = config_store
    mock_datetime.now.return_value.isoformat.return_value = "2024-04-04T15:41:22.083507"

    with patch("nv_config_manager.render.render.dcim_client_session", session):
        result = await execute_render("device-id", "test commit message", "test user")

    assert result == [FileCommit(filename="startup.yaml", commit="12345")]
    dcim_client.get_render_data.assert_awaited_once()
    assert dcim_client.get_render_data.await_args.args[0].device_id == "device-id"
    mock_renderer.return_value.render_entrypoints.assert_called_once_with(
        render_data=render_data,
    )
    dcim_client.upsert_intended_configuration.assert_awaited_once_with(
        IntendedConfigurationUpdate(
            device_id="device-id",
            config_store_instance="https://config-manager.example.com/",
            path="startup.yaml",
            commit_id="12345",
            updated="2024-04-04T15:41:22.083507",
            updated_by="test user",
            commit_message="test commit message",
            template_version=TEMPLATE_VERSION,
        )
    )


@pytest.mark.asyncio
async def test_execute_render_logs_post_render_failures_without_wrapping(caplog) -> None:
    """Persistence failures retain their type and gain device context in logs."""
    dcim_client = AsyncMock()
    dcim_client.get_render_data.return_value = _render_data()

    @asynccontextmanager
    async def session():
        yield dcim_client

    renderer = MagicMock()
    renderer.plugin_data_requirements = {}
    renderer.render_entrypoints.return_value = {"startup.yaml": "test"}
    config_store = AsyncMock()
    failure = ValueError("persistence failed")
    config_store.persist_files.side_effect = failure
    config_store.__aenter__.return_value = config_store

    with (
        patch("nv_config_manager.render.render.dcim_client_session", session),
        patch("nv_config_manager.render.render.Renderer", return_value=renderer),
        patch("nv_config_manager.render.render.config_store_client", return_value=config_store),
        caplog.at_level("ERROR"),
    ):
        with pytest.raises(ValueError) as caught:
            await execute_render("device-id", "message", "user")

    assert caught.value is failure
    assert "Failed to persist or synchronize render for device-id" in caplog.text


@pytest.mark.asyncio
@patch("nv_config_manager.render.render.config_store_client")
@patch("nv_config_manager.render.render.Renderer")
@patch("nv_config_manager.render.render.template_version_key", return_value=TEMPLATE_VERSION)
async def test_execute_render_updates_template_version_without_deployable_file(
    mock_template_version_key, mock_renderer, mock_config_store
):
    """A non-deployable update records only the normalized template version."""
    dcim_client = AsyncMock()
    dcim_client.get_render_data.return_value = _render_data()

    @asynccontextmanager
    async def session():
        yield dcim_client

    mock_renderer.return_value.render_entrypoints.return_value = {"boot-script": "test"}
    mock_renderer.return_value.plugin_data_requirements = {}
    config_store = AsyncMock()
    config_store.persist_files.return_value = [
        ConfigFileMetadata(commit="1", filename="boot-script")
    ]
    config_store.__aenter__.return_value = config_store
    mock_config_store.return_value = config_store

    with patch("nv_config_manager.render.render.dcim_client_session", session):
        assert await execute_render("device-id", "message", "user") == [
            FileCommit(filename="boot-script", commit="1")
        ]

    dcim_client.update_render_template_version.assert_awaited_once_with(
        "device-id", TEMPLATE_VERSION
    )


@pytest.mark.asyncio
@patch("nv_config_manager.render.render.config_store_client")
@patch("nv_config_manager.render.render.Renderer")
@patch("nv_config_manager.render.render.template_version_key", return_value=TEMPLATE_VERSION)
@patch(
    "nv_config_manager.render.render.config_store_ui_url", return_value="https://config-manager/"
)
async def test_execute_render_resyncs_unchanged_deployable_file(
    mock_config_store_ui_url, mock_template_version_key, mock_renderer, mock_config_store
):
    """No-diff renders retain the existing Config Store re-sync behavior."""
    dcim_client = AsyncMock()
    dcim_client.get_render_data.return_value = _render_data()

    @asynccontextmanager
    async def session():
        yield dcim_client

    mock_renderer.return_value.render_entrypoints.return_value = {"startup.yaml": "test"}
    mock_renderer.return_value.plugin_data_requirements = {}
    config_store = AsyncMock()
    config_store.persist_files.return_value = None
    config_store.load_file.return_value = ConfigFile(
        content="test",
        commit="42",
        filename="startup.yaml",
        sha="abc123",
        created_at="2024-03-15T10:30:00.000000",
    )
    config_store.__aenter__.return_value = config_store
    mock_config_store.return_value = config_store

    with patch("nv_config_manager.render.render.dcim_client_session", session):
        assert await execute_render("device-id", "message", "user") == []

    dcim_client.upsert_intended_configuration.assert_awaited_once()
    assert dcim_client.upsert_intended_configuration.await_args.args[0].commit_id == "42"
