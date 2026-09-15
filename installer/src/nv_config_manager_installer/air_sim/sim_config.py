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
"""Wizard state for DSX Air simulation deployment."""

from __future__ import annotations

import dataclasses
import os
import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nv_config_manager_installer.air_sim.constants import (
    DEFAULT_AIR_ORG,
    DEFAULT_CONFIG_MANAGER_REPO,
    DEFAULT_MOCK_TOPOLOGY_PATH,
)

_OOB_SSH_PASSWORD_CHARS = string.ascii_letters + string.digits


def _default_git_token() -> str:
    """Return an optional generic Git token for private forks."""
    return os.environ.get("GIT_TOKEN", os.environ.get("GITHUB_TOKEN", ""))


def generate_oob_ssh_password(length: int = 24) -> str:
    """Generate a shell-friendly password for the DSX Air OOB management server."""
    return "".join(secrets.choice(_OOB_SSH_PASSWORD_CHARS) for _ in range(length))


def _default_path(path: Path) -> str:
    return str(path) if path.exists() else ""


@dataclass
class SimConfig:
    """Configuration for bringing up an NVCM DSX Air simulation."""

    topology_path: str = ""
    mock_blueprint: str = "air_superpod"
    deployment_name: str = "demo"
    simulation_name: str = ""
    oob_server_name: str = "oob-mgmt-server"

    server_mode: str = "use-existing"
    attach_switch: str = ""
    attach_interface: str = ""

    auto_configure: bool = True
    git_token: str = field(default_factory=_default_git_token)
    config_manager_repo: str = DEFAULT_CONFIG_MANAGER_REPO

    size: str = "small"
    config_manager_ref: str = "main"
    cumulus_version: str = ""
    deploy: bool = True

    generate_fabric_from_mock_context: bool | None = None
    run_mock_topology_job: bool = True
    mock_topology_path: str = field(
        default_factory=lambda: _default_path(DEFAULT_MOCK_TOPOLOGY_PATH)
    )
    template_plugin_paths: list[str] = field(default_factory=list)
    extra_job_paths: list[str] = field(default_factory=list)
    extra_run_after_deploy: list[dict[str, Any]] = field(default_factory=list)

    use_internal: bool = False
    org_id: str = DEFAULT_AIR_ORG
    ngc_api_key: str = field(default_factory=lambda: os.environ.get("NGC_API_KEY", ""))
    oob_ssh_password: str = field(default_factory=generate_oob_ssh_password)

    wait_timeout: int = 1800
    deploy_timeout: int = 3600

    no_aggressive_dhcp: bool = False
    no_reset_before_dhcp: bool = False

    def __post_init__(self) -> None:
        if not self.oob_ssh_password:
            self.oob_ssh_password = generate_oob_ssh_password()

    @property
    def use_mock_context_for_fabric(self) -> bool:
        """Return the fabric source while preserving older AIR config semantics."""
        if self.generate_fabric_from_mock_context is not None:
            return self.generate_fabric_from_mock_context
        return self.run_mock_topology_job

    def to_yaml(self, path: Path) -> None:
        """Persist config to a YAML file with 0600 permissions."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(dataclasses.asdict(self), f, default_flow_style=False, sort_keys=False)
        path.chmod(0o600)

    @classmethod
    def from_yaml(cls, path: Path) -> SimConfig:
        """Load config from a YAML file, ignoring unknown keys."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        known = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    @classmethod
    def load_or_default(cls, path: Path) -> SimConfig:
        """Load from path if possible, otherwise return defaults."""
        if path.exists():
            try:
                return cls.from_yaml(path)
            except Exception:
                pass
        return cls()
