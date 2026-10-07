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
"""DSX Air behavior for the bundled Nautobot provider."""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label

from nv_config_manager_installer.air_sim.constants import (
    CONFIG_MANAGER_HOSTNAME,
    CONFIG_MANAGER_NAMESPACE,
    CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    DEFAULT_NAUTOBOT_DEMO_PASSWORD,
    DEFAULT_NAUTOBOT_DEMO_USERNAME,
)
from nv_config_manager_installer.air_sim.installer_config import remote_repo_path
from nv_config_manager_installer.air_sim.provider import AirProviderHooks, AirProviderStatus
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager
from nv_config_manager_installer.tui.air_sim.screens.population import PopulationPanel
from nv_config_manager_installer.tui.widgets import LabeledSwitch

LOG = logging.getLogger(__name__)

_MOCK_TOPOLOGY_JOB = "custom.mock_topology.jobs.mock_topology_design.MockTopologyDesign"
_KUBE = "KUBECONFIG=/home/nvcm/.kube/config"

_PROVISIONING_NBSHELL = (
    "from nautobot.dcim.models import Device;"
    "qs=Device.objects.filter(platform__name='Cumulus Linux');"
    "total=qs.count();"
    "status_field=Device._meta.get_field('status');"
    "status_lookup='status__name' if getattr(status_field,'remote_field',None) else 'status';"
    "status_filter={status_lookup:'Provisioned'};"
    "prov=qs.filter(**status_filter).count();"
    "pending=qs.exclude(**status_filter);"
    "names=[d.name for d in pending.order_by('name')[:5]];"
    "print(f'{prov}/{total}|{chr(44).join(names)}')"
)

_INTENDED_CONFIG_NBSHELL = (
    "from nv_config_manager.models import ConfigManagerDeviceStatus, IntendedConfig;"
    "total=ConfigManagerDeviceStatus.objects.filter(render_enabled=True).count();"
    "ready=IntendedConfig.objects.filter(device_id__render_enabled=True).count();"
    "print(f'{ready}/{total}')"
)

LAUNCH_STATUS = AirProviderStatus(
    display_name="Nautobot",
    web_pod_prefix=CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    access_url=f"https://{CONFIG_MANAGER_HOSTNAME}",
    access_display_name="Config Manager",
    dependent_pod_prefixes=(
        "nv-config-manager-nautobot-celery",
        "nv-config-manager-nautobot-celery-beat",
        "nv-config-manager-render-",
        "nv-config-manager-temporal-",
        "nv-config-manager-ztp",
        "nv-config-manager-dhcp",
    ),
    excluded_web_pod_prefixes=(
        "nv-config-manager-nautobot-celery",
        "nv-config-manager-nautobot-celery-beat",
    ),
)


def _normalize_post_deploy_job(job_spec: dict[str, Any]) -> dict[str, str]:
    job = str(job_spec.get("job", "")).strip()
    raw_input = job_spec.get("input", "")
    job_input = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
    return {"job": job, "input": job_input}


def build_content_jobs(cfg: SimConfig) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return ``dcim.settings.jobs`` and ``dcim.settings.run_after_deploy`` entries."""
    jobs: list[dict[str, str]] = []
    run_after_deploy: list[dict[str, str]] = []

    if cfg.run_mock_topology_job:
        jobs.append(
            {"path": remote_repo_path(cfg.mock_topology_path or "development/mock_topology")}
        )
        run_after_deploy.append(
            {
                "job": _MOCK_TOPOLOGY_JOB,
                "input": json.dumps(
                    {"blueprint": cfg.mock_blueprint, "deployment_name": cfg.deployment_name}
                ),
            }
        )

    for path in cfg.extra_job_paths:
        if path:
            jobs.append({"path": remote_repo_path(path)})

    for job_spec in cfg.extra_run_after_deploy:
        normalized = _normalize_post_deploy_job(job_spec)
        if normalized["job"]:
            run_after_deploy.append(normalized)

    return jobs, run_after_deploy


def _nbshell_command(script: str, namespace: str, deployment: str) -> str:
    return (
        f"{_KUBE} kubectl exec -i -n {namespace} deployment/{deployment}"
        f" -- nautobot-server nbshell --command {shlex.quote(script)}"
    )


def _parse_provisioning(output: str) -> tuple[int, int, list[str]] | None:
    for line in output.strip().splitlines():
        if "/" in line:
            counts, _, remaining = line.partition("|")
            prov_str, _, total_str = counts.partition("/")
            names = [n.strip() for n in remaining.split(",") if n.strip()]
            return int(prov_str.strip()), int(total_str.strip()), names
    return None


def get_provisioning_status(
    manager: AirSimulationManager,
    host: str,
    port: int,
    namespace: str = CONFIG_MANAGER_NAMESPACE,
    deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
) -> tuple[int, int, list[str]]:
    """Return (provisioned, total, remaining[:5]) from Nautobot, or (0,0,[]) on error."""
    try:
        result = manager.run_remote(
            host, port, _nbshell_command(_PROVISIONING_NBSHELL, namespace, deployment), timeout=30
        )
        if result.returncode == 0 and (parsed := _parse_provisioning(result.stdout)):
            return parsed
    except Exception:
        pass
    return 0, 0, []


def wait_for_intended_configs(
    manager: AirSimulationManager,
    host: str,
    port: int,
    expected_total: int | None = None,
    namespace: str = CONFIG_MANAGER_NAMESPACE,
    deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    timeout: int = 180,
    interval: int = 10,
) -> bool:
    """Wait until render-enabled devices have intended config records."""
    cmd = _nbshell_command(_INTENDED_CONFIG_NBSHELL, namespace, deployment)
    deadline = time.time() + timeout
    last_counts = "0/0"
    while time.time() < deadline:
        try:
            result = manager.run_remote(host, port, cmd, timeout=30)
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    clean = line.strip()
                    if not re.match(r"^\d+/\d+$", clean):
                        continue
                    ready_str, _, total_str = clean.partition("/")
                    ready = int(ready_str)
                    total = int(total_str)
                    last_counts = clean
                    target = expected_total if expected_total is not None else total
                    if target and ready >= target:
                        LOG.info("Intended configs ready: %s", clean)
                        return True
                    break
        except Exception:
            pass
        time.sleep(interval)

    LOG.warning("Timed out waiting for intended configs; last count: %s", last_counts)
    return False


def create_demo_user(
    manager: AirSimulationManager,
    host: str,
    port: int,
    username: str = DEFAULT_NAUTOBOT_DEMO_USERNAME,
    password: str = DEFAULT_NAUTOBOT_DEMO_PASSWORD,
    namespace: str = CONFIG_MANAGER_NAMESPACE,
    deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    timeout: int = 60,
) -> bool:
    """Create or update the demo superuser in Nautobot via kubectl exec and nbshell."""
    script = (
        "from django.contrib.auth import get_user_model\n"
        "User = get_user_model()\n"
        f"u, created = User.objects.get_or_create(\n"
        f"    username={username!r},\n"
        f'    defaults={{"email": "{username}@localhost"}},\n'
        ")\n"
        f"u.set_password({password!r})\n"
        "u.is_superuser = True\n"
        "u.is_staff = True\n"
        "u.save()\n"
        f'print("Created user {username}" if created else "Updated password for {username}")\n'
    )
    heredoc_end = "NAUTOBOT_DEMO_END"
    cmd_create = f"cat > /tmp/demo_user.py << '{heredoc_end}'\n{script}\n{heredoc_end}"
    cmd_run = (
        f"{_KUBE} kubectl exec -i -n {namespace} deployment/{deployment} -- "
        "nautobot-server nbshell < /tmp/demo_user.py"
    )
    try:
        LOG.info("Creating Nautobot user %s via nbshell...", username)
        r1 = manager.run_remote(host, port, cmd_create, timeout=15)
        if r1.returncode != 0:
            LOG.warning("Failed to write demo user script: %s", r1.stderr or r1.stdout)
            return False
        r2 = manager.run_remote(host, port, cmd_run, timeout=timeout)
        if r2.returncode != 0:
            LOG.warning("Failed to create demo user: %s", r2.stderr or r2.stdout)
            return False
        for line in (r2.stdout or "").strip().splitlines():
            LOG.info("[nautobot-demo] %s", line)
        return True
    except subprocess.TimeoutExpired:
        LOG.warning("Nautobot nbshell timed out")
        return False
    except Exception as exc:
        LOG.warning("Failed to create Nautobot demo user: %s", exc)
        return False


class NautobotAirHooks(AirProviderHooks):
    """Populate and probe the bundled Nautobot during an AIR bringup."""

    def validate_config(self, cfg: SimConfig) -> str:
        if cfg.run_mock_topology_job and not cfg.mock_topology_path:
            return "Mock topology path required for bundled Nautobot population."
        return ""

    def install_settings(self, cfg: SimConfig) -> dict[str, Any]:
        jobs, run_after_deploy = build_content_jobs(cfg)
        settings: dict[str, Any] = {}
        if jobs:
            settings["jobs"] = jobs
        if run_after_deploy:
            settings["run_after_deploy"] = run_after_deploy
        return settings

    def gateway_hostnames(self, cfg: SimConfig) -> tuple[str, ...]:
        return (f"nautobot.{CONFIG_MANAGER_HOSTNAME}", f"svc-nautobot.{CONFIG_MANAGER_HOSTNAME}")

    def post_deploy(self, manager: AirSimulationManager, host: str, port: int) -> None:
        create_demo_user(manager, host, port)

    def wait_for_configs(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
        expected_total: int,
    ) -> None:
        wait_for_intended_configs(manager, host, port, expected_total=expected_total)

    def provisioning_status(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
    ) -> tuple[int, int, list[str]]:
        return get_provisioning_status(manager, host, port)


class NautobotPopulationPanel(PopulationPanel):
    """Configure the bundled Nautobot mock-topology population job."""

    def compose(self) -> ComposeResult:
        yield Label("Nautobot", classes="subsection-label")
        yield LabeledSwitch(
            "Populate bundled Nautobot with the mock topology job",
            value=self._config.run_mock_topology_job,
            id="run-mock-topology-job",
        )
        with Vertical(id="nautobot-population-fields"):
            yield Label("Mock Topology Job Path", classes="field-label")
            yield Input(
                value=self._config.mock_topology_path,
                placeholder="development/mock_topology",
                id="mock-topology-path",
            )

    def on_mount(self) -> None:
        self._toggle_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "run-mock-topology-job":
            self._toggle_fields()

    def _toggle_fields(self) -> None:
        self.query_one("#nautobot-population-fields").display = self.query_one(
            "#run-mock-topology-job", LabeledSwitch
        ).value

    def write_to_config(self, config: SimConfig) -> None:
        config.run_mock_topology_job = self.query_one("#run-mock-topology-job", LabeledSwitch).value
        config.mock_topology_path = self.query_one("#mock-topology-path", Input).value.strip()

    def sync_from_config(self, config: SimConfig) -> None:
        super().sync_from_config(config)
        self.query_one("#run-mock-topology-job", LabeledSwitch).value = config.run_mock_topology_job
        self.query_one("#mock-topology-path", Input).value = config.mock_topology_path
        self._toggle_fields()

    def get_status(self, config: SimConfig) -> str:
        if config.run_mock_topology_job and not config.mock_topology_path:
            return "[!]"
        return "[*]"
