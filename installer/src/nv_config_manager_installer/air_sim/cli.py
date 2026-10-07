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
"""Click commands for DSX Air simulation demos."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import click

from nv_config_manager_installer.air_sim.constants import DEFAULT_AIR_SIM_CONFIG_PATH, NVCM_BOX_USER
from nv_config_manager_installer.air_sim.orchestrator import (
    SimOrchestrator,
    StepStatus,
)
from nv_config_manager_installer.air_sim.provider import AirProviderHooks
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.providers.errors import InstallerProviderError
from nv_config_manager_installer.providers.registry import get_installer_provider
from nv_config_manager_installer.tui.air_sim.app import NVCMAirSimApp


@click.group("air-sim")
def air_sim() -> None:
    """Create and deploy DSX Air simulation demos."""


@air_sim.command("init")
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_AIR_SIM_CONFIG_PATH,
    help="Path to DSX Air simulation YAML config.",
)
def init_air_sim(config_path: Path) -> None:
    """Launch the interactive DSX Air simulation TUI wizard."""
    config = SimConfig.load_or_default(config_path)
    try:
        provider = get_installer_provider(config.dcim_provider)
    except InstallerProviderError as exc:
        raise click.ClickException(str(exc)) from exc
    app = NVCMAirSimApp(config=config, config_path=config_path, provider=provider)
    app.run()


@air_sim.command("deploy")
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to DSX Air simulation YAML config.",
)
def deploy_air_sim(config_path: Path) -> None:
    """Run DSX Air simulation bringup from a config file."""
    config = SimConfig.from_yaml(config_path)
    try:
        provider = get_installer_provider(config.dcim_provider)
    except InstallerProviderError as exc:
        raise click.ClickException(str(exc)) from exc
    hooks = provider.air.hooks if provider.air is not None else AirProviderHooks()
    callback = _CliCallback(config.oob_ssh_password)
    orchestrator = SimOrchestrator(config, callback, provider_hooks=hooks)
    orchestrator.run()
    if not callback.success:
        sys.exit(1)


class _CliCallback:
    """Simple stdout callback for headless DSX Air simulation deploys."""

    def __init__(self, ssh_password: str) -> None:
        self.success = False
        self.host = ""
        self.port = 0
        self._ssh_password = ssh_password

    def on_step(self, step_id: str, status: StepStatus, message: str = "") -> None:
        icon = {
            StepStatus.PENDING: "[ ]",
            StepStatus.RUNNING: "[>]",
            StepStatus.SUCCESS: "[*]",
            StepStatus.FAILED: "[!]",
            StepStatus.SKIPPED: "[-]",
        }[status]
        suffix = f"  {message}" if message else ""
        click.echo(f"{icon} {step_id}{suffix}")

    def on_log(self, line: str) -> None:
        click.echo(line)

    def _ssh_cmd(self, host: str, port: int) -> str:
        return (
            f"sshpass -p {shlex.quote(self._ssh_password)} ssh"
            f" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            f" -o PreferredAuthentications=password"
            f" -p {port} {NVCM_BOX_USER}@{host}"
        )

    def on_ssh_ready(self, host: str, port: int) -> None:
        click.echo(f"SSH ready: {host}:{port}")
        click.echo(f"  {self._ssh_cmd(host, port)}")

    def on_deploy_started(self, host: str, port: int) -> None:
        click.echo(f"Deployment started over SSH: {host}:{port}")

    def on_complete(self, success: bool, host: str = "", port: int = 0) -> None:
        self.success = success
        self.host = host
        self.port = port
        if success:
            click.echo("DSX Air simulation bringup completed.")
            if host:
                click.echo(f"SSH: {host}:{port}")
                click.echo(f"  {self._ssh_cmd(host, port)}")
        else:
            click.echo("DSX Air simulation bringup failed.", err=True)
