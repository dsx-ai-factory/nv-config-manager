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
"""Tests for the provider-neutral local template CLI."""

from __future__ import annotations

import json

from click.testing import CliRunner
from nv_config_manager_dcim import (
    DeviceRenderData,
    LocationRenderData,
    RenderData,
    RenderDataExtension,
    RenderDeviceIdentity,
    RenderLocation,
)

from nv_config_manager_templates import cli as template_cli


def _render_data() -> RenderData:
    """Return a compact provider-owned payload suitable for cache tests."""
    return RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id="device-1",
                name="leaf-1",
                platform="Cumulus Linux",
                role="Leaf",
                model="SN5600",
                location=RenderLocation(id="location-1", name="site-1", kind="Site"),
            )
        ),
        location=LocationRenderData(
            location=RenderLocation(id="location-1", name="site-1", kind="Site")
        ),
        plugin_data={
            "example": RenderDataExtension(
                schema="example.render-data",
                version=1,
                data={"enabled": True},
            )
        },
    )


def test_cache_query_writes_portable_render_data(monkeypatch, tmp_path) -> None:
    """Live provider data is persisted as one provider-neutral cache envelope."""
    output_path = tmp_path / "render-data.json"
    provider_config = tmp_path / "provider.toml"
    provider_config.write_text("[provider]\nname = 'sample-dcim'\n", encoding="utf-8")
    monkeypatch.setattr(template_cli, "_load_render_data", lambda *_: _render_data())

    result = CliRunner().invoke(
        template_cli.cli,
        [
            "cache-query",
            "--provider-config",
            str(provider_config),
            "--device-id",
            "device-1",
            "--output-render-data-file",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    cached_render_data = RenderData.from_cache(json.loads(output_path.read_text(encoding="utf-8")))
    assert cached_render_data == _render_data()


def test_live_render_data_uses_sdk_provider_discovery(monkeypatch, tmp_path) -> None:
    """The library creates its selected provider through the standalone SDK."""
    calls: list[object] = []
    provider_config = tmp_path / "provider.toml"
    provider_config.write_text(
        "[provider]\nname = 'sample-dcim'\n[provider.settings]\nendpoint = 'example'\n",
        encoding="utf-8",
    )

    class Client:
        async def get_device_selection_by_name(self, name: str):
            calls.append(("selection", name))
            return type("Selection", (), {"id": "device-1"})()

        async def get_render_data(self, request) -> RenderData:
            calls.append(("render", request.device_id, request.plugin_data_requirements))
            return _render_data()

        async def close(self) -> None:
            calls.append("close")

    def create_client(name: str, settings: object) -> Client:
        calls.append(("create", name, settings))
        return Client()

    monkeypatch.setattr(template_cli, "create_dcim_client", create_client)

    result = template_cli._load_render_data(
        str(provider_config),
        device_id=None,
        device_name="device-name",
        plugin_data_requirements={},
    )

    assert result == _render_data()
    assert calls == [
        ("create", "sample-dcim", {"endpoint": "example"}),
        ("selection", "device-name"),
        ("render", "device-1", {}),
        "close",
    ]


def test_render_rejects_removed_split_cache_option(tmp_path) -> None:
    """The CLI exposes only the portable cache representation."""
    render_data_path = tmp_path / "render-data.json"
    split_device_path = tmp_path / "device.json"
    render_data_path.write_text(json.dumps(_render_data().to_cache()), encoding="utf-8")
    split_device_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        template_cli.cli,
        [
            "render",
            "--template",
            "unused.j2",
            "--cached-render-data",
            str(render_data_path),
            "--cached-data",
            str(split_device_path),
        ],
    )

    assert result.exit_code != 0
    assert "No such option: --cached-data" in result.output
