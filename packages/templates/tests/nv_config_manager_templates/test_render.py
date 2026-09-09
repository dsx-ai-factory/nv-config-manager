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
"""Render tests for public reference templates."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from jinja2 import DictLoader
from nv_config_manager_dcim import DeviceRenderData, RenderData, RenderDataExtension

from nv_config_manager_templates.dataclasses.interface import ConnectedDevice
from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.render import Renderer

RESOURCES_DIR = Path(__file__).resolve().parents[1] / "resources"
EXPECTED_CONFIG_DIR = RESOURCES_DIR / "expected_config"
RENDER_DATA_DIR = RESOURCES_DIR / "render-data"


def test_connected_device_peer_group_is_provider_neutral() -> None:
    """The shared model derives peer groups without organization-specific aliases."""
    device = ConnectedDevice(
        name="peer-01",
        role="edge-router",
        asn=None,
        tags=[],
        peer_ipv4=None,
        peer_ipv6=None,
    )

    assert device.peer_group == "EDGE-ROUTER"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate render tests from expected_config/{device}/{firmware_version}/{entrypoint}."""
    if "expected_config" not in metafunc.fixturenames:
        return

    tests = []
    ids = []
    for device_dir in sorted(path for path in EXPECTED_CONFIG_DIR.iterdir() if path.is_dir()):
        render_data_path = RENDER_DATA_DIR / f"{device_dir.name}.json"
        if not render_data_path.is_file():
            continue
        for version_dir in sorted(path for path in device_dir.iterdir() if path.is_dir()):
            for config_path in sorted(path for path in version_dir.iterdir() if path.is_file()):
                tests.append(
                    (render_data_path, version_dir.name, f"{config_path.name}.j2", config_path)
                )
                ids.append(f"{device_dir.name}/{version_dir.name}/{config_path.name}")

    metafunc.parametrize(
        "render_data_input,firmware_version,entrypoint,expected_config", tests, ids=ids
    )


def test_rendered_config(
    render_data_input: Path,
    firmware_version: str,
    entrypoint: str,
    expected_config: Path,
) -> None:
    """Test rendering a template using portable provider-neutral data."""
    os.environ["NV_CONFIG_MANAGER_SKIP_VAULT"] = "1"

    with render_data_input.open(encoding="utf-8") as file:
        render_data = RenderData.from_cache(json.load(file))
    render_data = render_data.model_copy(
        update={
            "device": render_data.device.model_copy(
                update={
                    "firmware": render_data.device.firmware.model_copy(
                        update={"desired_version": firmware_version}
                    )
                }
            )
        }
    )

    expected_output = expected_config.read_text(encoding="utf-8")

    renderer = Renderer()
    template = next(
        template
        for template in renderer.list_entrypoints(render_data.device)
        if template.endswith(f"/{entrypoint}")
    )

    output = renderer.render(template, render_data)

    if template.endswith(".yaml.j2"):
        try:
            yaml.safe_load(output)
        except yaml.scanner.ScannerError as exc:
            pytest.fail(f"Rendered configuration did not produce valid YAML: {exc}")

    assert output.rstrip() == expected_output.rstrip()


def test_missing_site_aggregate_fails_edge_render() -> None:
    """Edge templates must fail when the required Site-Aggregate prefix is missing."""
    os.environ["NV_CONFIG_MANAGER_SKIP_VAULT"] = "1"

    with (RENDER_DATA_DIR / "a09-u28-p01-bleaf-01.json").open(encoding="utf-8") as file:
        render_data = RenderData.from_cache(json.load(file))
    render_data = render_data.model_copy(
        update={
            "device": render_data.device.model_copy(
                update={
                    "firmware": render_data.device.firmware.model_copy(
                        update={"desired_version": "5.16.1"}
                    )
                }
            ),
            "location": render_data.location.model_copy(
                update={
                    "address_space": render_data.location.address_space.model_copy(
                        update={"prefixes": ()}
                    )
                }
            ),
        }
    )

    renderer = Renderer()
    template = next(
        template
        for template in renderer.list_entrypoints(render_data.device)
        if template.endswith("/startup.yaml.j2")
    )

    with pytest.raises(FilterException, match="Found no aggregates in role 'Site-Aggregate'"):
        renderer.render(template, render_data)


def test_renderer_exposes_plugin_extension_data(public_leaf_data, public_location_data) -> None:
    """Plugin filters receive extension data, not provider cache envelope metadata."""
    render_data = RenderData(
        device=public_leaf_data,
        location=public_location_data,
        plugin_data={
            "example": RenderDataExtension(
                schema="example.render-data",
                version=1,
                data={"enabled": True},
            )
        },
    )
    renderer = Renderer(enable_plugins=False)
    renderer.environment.loader = DictLoader(
        {"extension-test.j2": "{{ plugin_data.example.enabled }}"}
    )

    assert renderer.render("extension-test.j2", render_data) == "True"


def test_cumulus_516_renders_certificate_imports_and_mtls_otel(
    public_leaf_data, public_location_data
) -> None:
    """Cumulus imports assigned certs before applying full-mTLS OTLP config."""
    device_payload = public_leaf_data.model_dump(mode="json")
    device_payload["firmware"]["desired_version"] = "5.16.1"
    device_payload["certificates"] = [
        {
            "id": "otel-ca",
            "source": "switch-telemetry-ca",
            "kind": "ca",
            "services": ["ztp"],
        },
        {"id": "otel-client", "source": "switch-telemetry", "kind": "identity"},
    ]
    device_payload["telemetry"] = {
        "otlp": {
            "ca_certificate": "otel-ca",
            "destinations": [
                {
                    "address": "192.0.2.40",
                    "port": 4317,
                    "client_certificate": "otel-client",
                }
            ],
        }
    }
    render_data = RenderData(
        device=DeviceRenderData.model_validate(device_payload),
        location=public_location_data,
    )
    renderer = Renderer(enable_plugins=False)
    entrypoints = renderer.list_entrypoints(render_data.device)
    boot_template = next(path for path in entrypoints if path.endswith("/boot-script.j2"))
    startup_template = next(path for path in entrypoints if path.endswith("/startup.yaml.j2"))

    boot_script = renderer.render(boot_template, render_data)
    startup = yaml.safe_load(renderer.render(startup_template, render_data))

    certificate_base_url = (
        "sftp://ztp:ztp@192.0.2.10:2222/device/c9e574df-2295-4258-b5b2-16247b6e3aa7/certificates"
    )
    assert (
        "nv action import system security ca-certificate otel-ca uri "
        f"{certificate_base_url}/otel-ca"
    ) in boot_script
    assert (
        "nv action import system security certificate otel-client uri-bundle "
        f"{certificate_base_url}/otel-client"
    ) in boot_script
    assert boot_script.index("ca-certificate otel-ca") < boot_script.index(
        "certificate otel-client"
    )
    assert boot_script.index("ca-certificate otel-ca") < boot_script.index("nv config replace")
    assert (
        "retry_command nv action fetch system file-path /tmp/startup.yaml "
        "\\\n  uri sftp://ztp:ztp@192.0.2.10:2222/device/"
        "c9e574df-2295-4258-b5b2-16247b6e3aa7/startup.yaml "
        "\\\n  file-permissions 600"
    ) in boot_script

    grpc = startup[0]["set"]["system"]["telemetry"]["export"]["otlp"]["grpc"]
    assert grpc["insecure"] == "disabled"
    assert grpc["certificate"] == "otel-ca"
    assert grpc["destination"]["192.0.2.40"] == {
        "port": 4317,
        "client-certificate": "otel-client",
    }
