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
"""Tests for DSX Air sim orchestrator topology resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nv_config_manager_installer.air_sim.orchestrator import (
    SimOrchestrator,
    _monitor_setup_command,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager


class _Callback:
    def on_step(self, step_id, status, message=""):
        pass

    def on_log(self, line):
        pass

    def on_ssh_ready(self, host, port):
        pass

    def on_deploy_started(self, host, port):
        pass

    def on_complete(self, success, host="", port=0):
        pass


def test_resolve_topology_prefers_direct_path() -> None:
    cfg = SimConfig(topology_path="/tmp/direct.yaml", run_mock_topology_job=True)
    orchestrator = SimOrchestrator(cfg, _Callback())

    assert orchestrator._resolve_topology_path(cfg) == "/tmp/direct.yaml"


def test_resolve_topology_generates_from_mock_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_write_site_design_from_mock_context(blueprint: str, deployment_name: str) -> str:
        calls.append((blueprint, deployment_name))
        return "/tmp/generated.yaml"

    monkeypatch.setattr(
        "nv_config_manager_installer.air_sim.orchestrator.write_site_design_from_mock_context",
        fake_write_site_design_from_mock_context,
    )
    cfg = SimConfig(
        topology_path="",
        run_mock_topology_job=True,
        mock_blueprint="air_trial",
        deployment_name="demo",
    )
    orchestrator = SimOrchestrator(cfg, _Callback())

    assert orchestrator._resolve_topology_path(cfg) == "/tmp/generated.yaml"
    assert calls == [("air_trial", "demo")]


def test_resolve_topology_generation_is_independent_from_dcim_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nv_config_manager_installer.air_sim.orchestrator.write_site_design_from_mock_context",
        lambda blueprint, deployment_name: f"/tmp/{blueprint}-{deployment_name}.yaml",
    )
    cfg = SimConfig(
        topology_path="",
        generate_fabric_from_mock_context=True,
        run_mock_topology_job=False,
        mock_blueprint="air_superpod",
        deployment_name="external-dcim",
    )
    orchestrator = SimOrchestrator(cfg, _Callback())

    assert orchestrator._resolve_topology_path(cfg) == "/tmp/air_superpod-external-dcim.yaml"


def test_monitor_setup_command_uses_password_placeholder() -> None:
    command = _monitor_setup_command("worker.example", 17117)

    assert command.startswith("sshpass -p '<password>'")
    assert "worker.example" in command
    assert "17117" in command


def test_derived_orchestrator_replaces_provider_post_deploy_behavior() -> None:
    pre_deploy_calls: list[tuple[str, int]] = []
    calls: list[tuple[str, int]] = []
    config_waits: list[int] = []

    class ProviderOrchestrator(SimOrchestrator):
        def _run_provider_pre_deploy(
            self,
            manager: AirSimulationManager,
            host: str,
            port: int,
        ) -> None:
            pre_deploy_calls.append((host, port))

        def _provider_gateway_hostnames(self, cfg: SimConfig) -> tuple[str, ...]:
            return ("netbox.nvcm.air",)

        def _run_provider_post_deploy(
            self,
            manager: AirSimulationManager,
            host: str,
            port: int,
        ) -> None:
            calls.append((host, port))

        def _wait_for_provider_configs(
            self,
            manager: AirSimulationManager,
            host: str,
            port: int,
            expected_total: int,
        ) -> None:
            config_waits.append(expected_total)

        def _build_deploy_command(self, cfg: SimConfig) -> str:
            return "run-netbox-installer"

    cfg = SimConfig(no_reset_before_dhcp=True)
    orchestrator = ProviderOrchestrator(cfg, _Callback())
    manager = Mock(spec=AirSimulationManager)
    builder = SimpleNamespace(relay_return_prefixes=[], devices={})

    orchestrator._run_provider_pre_deploy(manager, "worker.example", 17117)
    orchestrator._run_post_deploy(
        manager,
        cfg,
        builder,
        "simulation-id",
        "worker.example",
        17117,
        "00:11:22:33:44:55",
        "192.0.2.1",
    )

    assert pre_deploy_calls == [("worker.example", 17117)]
    assert calls == [("worker.example", 17117)]
    assert config_waits == [0]
    assert orchestrator._build_deploy_command(cfg) == "run-netbox-installer"
    manager.configure_etc_hosts.assert_called_once_with(
        "worker.example",
        17117,
        additional_hostnames=("netbox.nvcm.air",),
    )
    manager.create_nautobot_demo_user.assert_not_called()
    manager.wait_for_intended_configs.assert_not_called()
    manager.ensure_temporal_search_attributes.assert_called_once_with("worker.example", 17117)
