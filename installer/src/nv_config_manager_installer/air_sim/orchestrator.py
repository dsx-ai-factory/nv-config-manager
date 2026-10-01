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
"""Orchestrator for DSX Air simulation bringup with TUI/CLI callbacks."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Protocol

from nv_config_manager_installer.air_sim.cloud_init import generate_server_cloud_init
from nv_config_manager_installer.air_sim.constants import (
    CONFIG_MANAGER_INSTALL_CONFIG,
    NVCM_BOX_USER,
)
from nv_config_manager_installer.air_sim.context_topology import write_site_design_from_mock_context
from nv_config_manager_installer.air_sim.installer_config import (
    build_deploy_command,
    generate_air_sim_install_yaml,
)
from nv_config_manager_installer.air_sim.models import NVCMServerConfig
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager
from nv_config_manager_installer.air_sim.topology import (
    AirTopologyBuilder,
    _create_version_override_yaml,
    _resolve_oob_server_ips_from_topology,
)


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


STEPS: list[tuple[str, str]] = [
    ("parse-topology", "Resolve topology"),
    ("validate-images", "Validate DSX Air images"),
    ("create-sim", "Create DSX Air simulation"),
    ("attach-cloud-init", "Attach cloud-init"),
    ("start-sim", "Start simulation"),
    ("create-ssh", "Create SSH service"),
    ("wait-setup", "Wait for cloud-init"),
    ("upload-files", "Upload installer config"),
    ("run-deploy", "Run nvcm installer"),
    ("post-deploy", "Post-deploy setup"),
]


def _monitor_setup_command(host: str, port: int) -> str:
    """Return a monitor command that is safe to display or persist."""
    return (
        f"sshpass -p '<password>' ssh -p {port} "
        f"{NVCM_BOX_USER}@{host} 'sudo tail -f /var/log/nvcm-setup.log'"
    )


class OrchestratorCallback(Protocol):
    def on_step(self, step_id: str, status: StepStatus, message: str = "") -> None: ...
    def on_log(self, line: str) -> None: ...
    def on_ssh_ready(self, host: str, port: int) -> None: ...
    def on_deploy_started(self, host: str, port: int) -> None: ...
    def on_complete(self, success: bool, host: str = "", port: int = 0) -> None: ...


class _CallbackLogHandler(logging.Handler):
    """Forwards log records to an on_log callback."""

    def __init__(self, on_log: Callable[[str], None]) -> None:
        super().__init__()
        self._on_log = on_log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._on_log(self.format(record))
        except Exception:
            pass


class SimOrchestrator:
    """Run the full DSX Air simulation bringup."""

    def __init__(self, config: SimConfig, callback: OrchestratorCallback) -> None:
        self._cfg = config
        self._cb = callback

    def _log(self, msg: str) -> None:
        self._cb.on_log(msg)

    def _step(self, step_id: str, status: StepStatus, message: str = "") -> None:
        self._cb.on_step(step_id, status, message)

    def run(self) -> None:
        handler = _CallbackLogHandler(self._log)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        pkg_logger = logging.getLogger("nv_config_manager_installer.air_sim")
        prev_level = pkg_logger.level
        pkg_logger.setLevel(logging.DEBUG)
        pkg_logger.addHandler(handler)
        success = False
        host = ""
        port = 0
        try:
            host, port = self._run_impl()
            success = True
        except Exception as exc:
            self._log(f"[ERROR] {exc}")
        finally:
            pkg_logger.removeHandler(handler)
            pkg_logger.setLevel(prev_level)
            self._cb.on_complete(success, host, port)

    def _resolve_topology_path(self, cfg: SimConfig) -> str:
        if cfg.topology_path:
            return cfg.topology_path
        if cfg.use_mock_context_for_fabric:
            return write_site_design_from_mock_context(cfg.mock_blueprint, cfg.deployment_name)
        raise RuntimeError(
            "topology_path or both mock_blueprint and deployment_name are required "
            "to generate the DSX Air fabric."
        )

    def _generate_install_yaml(
        self, cfg: SimConfig, site_name: str, lb_allowed_prefixes: list[str]
    ) -> str:
        """Build the base installer document for the simulation.

        Provider-specific installers may override this hook to extend their
        deployment configuration without replacing the AIR orchestration flow.
        """
        return generate_air_sim_install_yaml(cfg, site_name, lb_allowed_prefixes)

    def _create_simulation_manager(self, cfg: SimConfig) -> AirSimulationManager:
        """Construct the AIR API/remote-host client used by the orchestration flow."""
        return AirSimulationManager(
            ngc_api_key=cfg.ngc_api_key,
            use_internal=cfg.use_internal,
            org_id=cfg.org_id,
            ssh_password=cfg.oob_ssh_password,
        )

    def _build_deploy_command(self, cfg: SimConfig) -> str:
        """Return the provider installer's remote deployment command."""
        return build_deploy_command(cfg)

    def _run_provider_pre_deploy(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
    ) -> None:
        """Install provider-owned services before NVIDIA Config Manager deploys."""

    def _provider_gateway_hostnames(self, cfg: SimConfig) -> tuple[str, ...]:
        """Return provider-owned UI hostnames routed through the simulation gateway."""
        return ()

    def _run_provider_post_deploy(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
    ) -> None:
        """Run setup owned by the bundled provider after common services are ready."""
        manager.create_nautobot_demo_user(host, port)

    def _wait_for_provider_configs(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
        expected_total: int,
    ) -> None:
        """Wait for the bundled provider to persist rendered configurations."""
        manager.wait_for_intended_configs(host, port, expected_total=expected_total)

    def _run_post_deploy(
        self,
        manager: AirSimulationManager,
        cfg: SimConfig,
        builder: AirTopologyBuilder,
        simulation_id: str,
        host: str,
        port: int,
        internal_mac: str,
        oob_gateway: str | None,
    ) -> None:
        """Run common post-deploy setup, with a hook for provider-owned behavior."""
        manager.configure_etc_hosts(
            host,
            port,
            additional_hostnames=self._provider_gateway_hostnames(cfg),
        )
        resolved_iface = manager.resolve_iface_by_mac(host, port, internal_mac)
        manager.configure_nat_rules(
            host,
            port,
            oob_gateway=oob_gateway,
            relay_return_networks=builder.relay_return_prefixes,
            internal_iface=resolved_iface or "eth1",
        )

        cumulus_reset = [d.name for d in builder.devices.values() if "Cumulus" in d.platform]
        manager.queue_render_all(host, port)
        self._wait_for_provider_configs(manager, host, port, len(cumulus_reset))
        manager.restart_dhcp_refresh(host, port)

        if not cfg.no_reset_before_dhcp and cumulus_reset:
            manager.reset_cumulus_nodes(simulation_id, cumulus_reset)

        self._run_provider_post_deploy(manager, host, port)
        manager.ensure_temporal_search_attributes(host, port)

    def _run_impl(self) -> tuple[str, int]:
        cfg = self._cfg

        self._step("parse-topology", StepStatus.RUNNING)
        topology_path = self._resolve_topology_path(cfg)
        if cfg.cumulus_version:
            topology_path = _create_version_override_yaml(topology_path, cfg.cumulus_version)

        nvcm_server: NVCMServerConfig | None = None
        if cfg.server_mode == "use-existing":
            nvcm_server = NVCMServerConfig(
                use_existing_server=cfg.oob_server_name,
                nvcm_size=cfg.size,
            )
        elif cfg.server_mode == "create-new" and cfg.attach_switch and cfg.attach_interface:
            nvcm_server = NVCMServerConfig(
                attach_switch=cfg.attach_switch,
                attach_interface=cfg.attach_interface,
                nvcm_size=cfg.size,
            )

        builder = AirTopologyBuilder(
            yaml_path=topology_path,
            simulation_name=cfg.simulation_name or None,
            minimal_mode=False,
            nvcm_server=nvcm_server,
        )
        self._step("parse-topology", StepStatus.SUCCESS)

        self._step("validate-images", StepStatus.RUNNING)
        manager = self._create_simulation_manager(cfg)
        cumulus_versions = builder.cumulus_firmware_versions()
        if cumulus_versions:
            builder.set_cumulus_image_overrides(manager.resolve_cumulus_vx_images(cumulus_versions))
            self._step(
                "validate-images",
                StepStatus.SUCCESS,
                f"{len(cumulus_versions)} Cumulus version(s)",
            )
        else:
            self._step("validate-images", StepStatus.SKIPPED, "No Cumulus devices")

        topology = builder.build_topology()
        self._log(
            f"Site: {builder.site_name}  "
            f"Devices: {len(builder.devices)}  "
            f"Nodes: {len(topology['nodes'])}  "
            f"Links: {len(topology['links'])}"
        )

        derived_ip, derived_gw = _resolve_oob_server_ips_from_topology(
            builder.site_design, cfg.oob_server_name
        )
        internal_ip = derived_ip
        oob_gateway = derived_gw
        lb_allowed = builder.lb_allowed_prefixes
        bgp_asn = builder.resolve_device_bgp_asn(cfg.oob_server_name) or "4266000000"

        self._step("create-sim", StepStatus.RUNNING)
        simulation_id = manager.create_simulation(builder.simulation_name, topology)
        self._log(f"Simulation: {simulation_id}")
        if nvcm_server:
            manager.prepare_nvcm_server(simulation_id, nvcm_server.server_name)
        self._step("create-sim", StepStatus.SUCCESS)

        internal_mac = ""
        full_setup = bool(cfg.config_manager_repo)

        if cfg.auto_configure:
            self._step("attach-cloud-init", StepStatus.RUNNING)
            server_dev = builder.devices.get(cfg.oob_server_name)
            if not server_dev:
                raise RuntimeError(f"Server {cfg.oob_server_name!r} not in topology")
            internal_mac = server_dev.interface_macs.get("eth1", "")
            if not internal_mac:
                raise RuntimeError(
                    f"No MAC for {cfg.oob_server_name}:eth1; add mac_address to the topology"
                )

            cloud_init = generate_server_cloud_init(
                internal_mac=internal_mac,
                oob_ssh_password=cfg.oob_ssh_password,
                git_token=cfg.git_token,
                config_manager_repo=cfg.config_manager_repo,
                config_manager_ref=cfg.config_manager_ref,
                deploy_size=cfg.size,
                internal_ip=internal_ip,
                site_name=builder.site_name,
                oob_gateway=oob_gateway,
                lb_allowed_prefixes=",".join(lb_allowed),
                relay_return_networks=" ".join(builder.relay_return_prefixes),
                bgp_asn=bgp_asn,
            )
            manager.attach_cloud_init(simulation_id, cfg.oob_server_name, cloud_init)
            self._log(
                "Cloud-init attached " + ("(full setup)" if full_setup else "(minimal setup)")
            )
            self._step("attach-cloud-init", StepStatus.SUCCESS)
        else:
            self._step("attach-cloud-init", StepStatus.SKIPPED)

        if not cfg.no_aggressive_dhcp:
            cumulus_names = [d.name for d in builder.devices.values() if "Cumulus" in d.platform]
            if cumulus_names:
                manager.attach_dhclient_tuning(simulation_id, cumulus_names)
                self._log(f"Aggressive DHCP attached to {len(cumulus_names)} switch(es)")

        self._step("start-sim", StepStatus.RUNNING)
        self._log("Waiting for simulation to boot (this may take several minutes)...")
        manager.start_simulation(simulation_id, wait=True)
        self._log(f"Simulation running: {simulation_id}")
        self._step("start-sim", StepStatus.SUCCESS)

        if not cfg.auto_configure:
            for step_id in (
                "create-ssh",
                "wait-setup",
                "upload-files",
                "run-deploy",
                "post-deploy",
            ):
                self._step(step_id, StepStatus.SKIPPED)
            return "", 0

        if not shutil.which("sshpass"):
            raise RuntimeError(
                "sshpass not found; required for SSH automation. Install sshpass and retry."
            )

        self._step("create-ssh", StepStatus.RUNNING)
        ssh_info = manager.create_ssh_service(simulation_id, cfg.oob_server_name, "eth0")
        if not ssh_info:
            raise RuntimeError(f"Could not create SSH service for {cfg.oob_server_name}:eth0")
        host, port = ssh_info
        self._log(f"SSH ready: {NVCM_BOX_USER}@{host}:{port}")
        self._step("create-ssh", StepStatus.SUCCESS)
        self._cb.on_ssh_ready(host, port)

        if not full_setup or cfg.wait_timeout == 0:
            for step_id in ("wait-setup", "upload-files", "run-deploy", "post-deploy"):
                self._step(step_id, StepStatus.SKIPPED)
            self._log(f"\nMonitor setup: {_monitor_setup_command(host, port)}")
            return host, port

        self._step("wait-setup", StepStatus.RUNNING)
        setup_ok = manager.wait_for_cloud_init(host, port, timeout=cfg.wait_timeout)
        if not setup_ok:
            self._step("wait-setup", StepStatus.FAILED)
            for step_id in ("upload-files", "run-deploy", "post-deploy"):
                self._step(step_id, StepStatus.SKIPPED)
            self._log(f"\nSetup timed out. Check: {_monitor_setup_command(host, port)}")
            return host, port
        self._step("wait-setup", StepStatus.SUCCESS)

        self._step("upload-files", StepStatus.RUNNING)
        install_yaml = self._generate_install_yaml(
            cfg,
            site_name=builder.site_name,
            lb_allowed_prefixes=lb_allowed,
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="nv-config-manager-install-", delete=False
        ) as tmp:
            tmp.write(install_yaml)
            tmp_path = tmp.name

        ok = manager.upload_to_server(
            host,
            port,
            tmp_path,
            f"/home/{NVCM_BOX_USER}/{CONFIG_MANAGER_INSTALL_CONFIG}",
        )
        Path(tmp_path).unlink(missing_ok=True)
        if not ok:
            self._step("upload-files", StepStatus.FAILED)
            raise RuntimeError(f"Failed to upload {CONFIG_MANAGER_INSTALL_CONFIG}")
        self._log(f"Uploaded {CONFIG_MANAGER_INSTALL_CONFIG}")
        self._step("upload-files", StepStatus.SUCCESS)

        if not cfg.deploy:
            for step_id in ("run-deploy", "post-deploy"):
                self._step(step_id, StepStatus.SKIPPED)
            self._log(f"\nSetup done. SSH in and run:\n  {self._build_deploy_command(cfg)}")
            return host, port

        self._step("run-deploy", StepStatus.RUNNING)
        self._cb.on_deploy_started(host, port)
        self._run_provider_pre_deploy(manager, host, port)
        deploy_cmd = self._build_deploy_command(cfg)
        self._log(f"Running deploy command:\n  {deploy_cmd}")
        deploy_ok = manager.run_deploy(host, port, deploy_cmd, timeout=cfg.deploy_timeout)
        if not deploy_ok:
            self._step("run-deploy", StepStatus.FAILED)
            raise RuntimeError("nv-config-manager-installer deploy failed")
        self._step("run-deploy", StepStatus.SUCCESS)

        self._step("post-deploy", StepStatus.RUNNING)
        self._run_post_deploy(
            manager,
            cfg,
            builder,
            simulation_id,
            host,
            port,
            internal_mac,
            oob_gateway,
        )
        self._step("post-deploy", StepStatus.SUCCESS)
        self._log(f"\nDone! {NVCM_BOX_USER}@{host}:{port}")
        return host, port
