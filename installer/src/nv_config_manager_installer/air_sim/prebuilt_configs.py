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
"""Pre-built TUI configurations for public demo workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nv_config_manager_installer.air_sim.constants import (
    DEFAULT_AIR_DEMO_TEMPLATE_PLUGIN_PATH,
    DEFAULT_AIR_TRIAL_CONFIG,
    DEFAULT_CONFIG_MANAGER_REPO,
    DEFAULT_MOCK_TOPOLOGY_PATH,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig


@dataclass(frozen=True)
class PrebuiltConfig:
    """A named configuration preset that can populate the TUI."""

    id: str
    label: str
    description: str
    path: Path | None = None


PREBUILT_CONFIGS: tuple[PrebuiltConfig, ...] = (
    PrebuiltConfig(
        id="air-trial",
        label="DSX Air free trial demo",
        description="Resource-capped ZTP and multi-deploy demo for public DSX Air trial accounts.",
        path=DEFAULT_AIR_TRIAL_CONFIG,
    ),
    PrebuiltConfig(
        id="superpod",
        label="SuperPOD demo",
        description=(
            "Two-rack public SuperPOD mockup built from mock_topology context "
            "with dedicated demo templates."
        ),
    ),
)


def get_prebuilt_config(config_id: str) -> PrebuiltConfig | None:
    """Return metadata for a pre-built config."""
    return next((config for config in PREBUILT_CONFIGS if config.id == config_id), None)


def load_prebuilt_config(
    config_id: str,
    config_model: type[SimConfig] = SimConfig,
) -> SimConfig:
    """Return a fresh simulation config populated from a named preset."""
    preset = get_prebuilt_config(config_id)
    if preset is None:
        raise ValueError(f"Unknown pre-built config: {config_id}")

    if preset.path:
        return config_model.from_yaml(preset.path)

    if preset.id == "superpod":
        return config_model(
            topology_path="",
            mock_blueprint="air_superpod",
            deployment_name="demo",
            simulation_name="nv-config-manager-superpod-demo",
            oob_server_name="oob-mgmt-server",
            server_mode="use-existing",
            auto_configure=True,
            git_token="",
            config_manager_repo=DEFAULT_CONFIG_MANAGER_REPO,
            config_manager_ref="main",
            cumulus_version="",
            size="small",
            deploy=True,
            run_mock_topology_job=True,
            mock_topology_path=str(DEFAULT_MOCK_TOPOLOGY_PATH),
            template_plugin_paths=[str(DEFAULT_AIR_DEMO_TEMPLATE_PLUGIN_PATH)],
            use_internal=False,
            ngc_api_key="",
        )

    raise ValueError(f"Pre-built config has no loader: {preset.id}")
