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
"""Regression tests for the DSX Air template plugin."""

from pathlib import Path

import yaml
from jinja2 import ChoiceLoader, FileSystemLoader
from nv_config_manager_dcim import (
    DeviceRenderData,
    LocationRenderData,
    RenderData,
    RenderDeviceIdentity,
    RenderFirmwareData,
    RenderInterface,
    RenderL2Vni,
    RenderLocation,
    RenderOverlayData,
    RenderVlan,
)
from nv_config_manager_templates.render import Renderer

REPOSITORY_ROOT = Path(__file__).parents[3]
PLUGIN_TEMPLATE_ROOT = (
    REPOSITORY_ROOT
    / "development/air_sim/template_plugins/superpod-template-plugin/src"
    / "nv_config_manager_superpod_templates/templates"
)
BRIDGE_TEMPLATE = "cumulus-linux/superpod-demo-common/include/bridge.j2"


def test_bridge_uses_provider_neutral_overlay_data() -> None:
    """Air VLAN-to-VNI mappings render from the typed overlay contract."""
    location = RenderLocation(name="SPO01", kind="Site")
    vlan = RenderVlan(vid=100, name="OOB-MGMT")
    render_data = RenderData(
        device=DeviceRenderData(
            identity=RenderDeviceIdentity(
                id="air-oob-mleaf-01",
                name="oob-mleaf-01",
                platform="Cumulus Linux",
                role="OOB-MLEAF",
                model="Cumulus VX",
                location=location,
            ),
            interfaces=(
                RenderInterface(
                    name="vlan100",
                    type="VIRTUAL",
                    enabled=True,
                    untagged_vlan=vlan,
                ),
            ),
            overlays=RenderOverlayData(
                l2_vnis=(RenderL2Vni(vlan=vlan, vni=10100),),
            ),
            firmware=RenderFirmwareData(desired_version="5.16.1"),
        ),
        location=LocationRenderData(location=location),
    )
    renderer = Renderer(enable_plugins=False)
    renderer.environment.loader = ChoiceLoader(
        [FileSystemLoader(PLUGIN_TEMPLATE_ROOT), renderer.environment.loader]
    )

    rendered = yaml.safe_load(renderer.render(BRIDGE_TEMPLATE, render_data))

    assert rendered["bridge"]["domain"]["br_default"]["vlan"]["100"]["vni"] == {"10100": {}}
