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
"""Tests for DSX Air simulation manager helpers."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import nv_config_manager_installer.air_sim.sim_manager as sim_manager_module
from nv_config_manager_installer.air_sim.models import NVCMServerConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager


def _image(name: str, modified: datetime, version: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        version=version,
        modified=modified,
        created=modified,
    )


def _manager_with_images(images: list[SimpleNamespace]) -> AirSimulationManager:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    manager.client = SimpleNamespace(
        images=SimpleNamespace(list=lambda: iter(images)),
    )
    return manager


def test_remote_setup_marker_exists_uses_single_remote_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    commands: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: int,
    ) -> SimpleNamespace:
        assert capture_output is True
        assert timeout == 10
        commands.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sim_manager_module.subprocess, "run", fake_run)

    assert manager._remote_setup_marker_exists(["ssh", "nvcm@worker.example"])
    assert commands == [
        [
            "ssh",
            "nvcm@worker.example",
            "sudo grep -F -q -- 'NVCM DSX Air Setup Complete' "
            "/var/log/cloud-init-output.log /var/log/nvcm-setup.log",
        ]
    ]


def test_remote_setup_marker_timeout_is_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: int,
    ) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(sim_manager_module.subprocess, "run", fake_run)

    assert not manager._remote_setup_marker_exists(["ssh", "nvcm@worker.example"])


def test_terminate_process_kills_after_grace_period() -> None:
    proc = Mock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("ssh", 5), 0]

    AirSimulationManager._terminate_process(proc)

    proc.terminate.assert_called_once_with()
    proc.kill.assert_called_once_with()
    assert proc.wait.call_args_list == [call(timeout=5), call(timeout=5)]


def test_resolve_cumulus_vx_images_prefers_exact_name() -> None:
    manager = _manager_with_images(
        [
            _image(
                "cumulus-linux-vx-amd64-5.16.1.0008.qcow2",
                datetime(2026, 1, 2, tzinfo=UTC),
            ),
            _image("cumulus-vx-5.16.1", datetime(2026, 1, 1, tzinfo=UTC)),
        ]
    )

    assert manager.resolve_cumulus_vx_images(["5.16.1"]) == {"5.16.1": "cumulus-vx-5.16.1"}


def test_resolve_cumulus_vx_images_uses_newest_close_match() -> None:
    manager = _manager_with_images(
        [
            _image(
                "cumulus-linux-vx-amd64-5.16.1.0007.qcow2",
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _image(
                "cumulus-linux-vx-amd64-5.16.1.0008.qcow2",
                datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ]
    )

    assert manager.resolve_cumulus_vx_images(["5.16.1"]) == {
        "5.16.1": "cumulus-linux-vx-amd64-5.16.1.0008.qcow2"
    }


def test_resolve_cumulus_vx_images_requires_match() -> None:
    manager = _manager_with_images([_image("generic/ubuntu2404", datetime(2026, 1, 1, tzinfo=UTC))])

    with pytest.raises(RuntimeError, match="cumulus-vx-5.16.1"):
        manager.resolve_cumulus_vx_images(["5.16.1"])


def test_configure_nat_rules_enables_dhcp_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    commands: list[str] = []

    def fake_ssh_cmd(host: str, port: int) -> list[str]:
        assert host == "worker.example"
        assert port == 17117
        return ["ssh", "nvcm@worker.example"]

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> SimpleNamespace:
        assert capture_output is True
        assert text is True
        remote_command = cmd[-1]
        commands.append(remote_command)
        if "nv-config-manager-dhcp-service" in remote_command:
            return SimpleNamespace(returncode=0, stdout="172.18.255.202\n", stderr="")
        if "nv-config-manager-ztp-service" in remote_command:
            return SimpleNamespace(returncode=0, stdout="172.18.255.201\n", stderr="")
        if "docker network inspect kind" in remote_command:
            return SimpleNamespace(returncode=0, stdout="a0016a226683\n", stderr="")
        if "docker ps" in remote_command:
            return SimpleNamespace(returncode=0, stdout="nvcm-control-plane\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(manager, "_ssh_cmd", fake_ssh_cmd)
    monkeypatch.setattr(sim_manager_module.subprocess, "run", fake_run)

    assert manager.configure_nat_rules(
        "worker.example",
        17117,
        oob_gateway="10.100.0.1",
        relay_return_networks=["10.100.0.0/16"],
        internal_iface="eth1",
    )

    relay_config = next(command for command in commands if "/etc/default/isc-dhcp-relay" in command)
    assert 'SERVERS="172.18.255.202"' in relay_config
    assert 'INTERFACES="eth1 br-a0016a226683"' in relay_config
    assert "sudo systemctl enable isc-dhcp-relay" in commands
    assert "sudo systemctl restart isc-dhcp-relay" in commands
    assert not any("disable --now isc-dhcp-relay" in command for command in commands)


def test_print_socks_instructions_redacts_password(caplog: pytest.LogCaptureFixture) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    manager.ssh_password = "do-not-log-this-password"

    with caplog.at_level("INFO"):
        manager.print_socks_instructions("worker.example", 17117)

    assert manager.ssh_password not in caplog.text
    assert "sshpass -p '<password>'" in caplog.text


def test_setup_nvcm_server_does_not_log_deployment_script(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    node = SimpleNamespace(name="oob-mgmt-server", state="RUNNING")
    interface = SimpleNamespace(id="interface-id", name="eth0")
    service = SimpleNamespace(
        interface=interface,
        node_port=22,
        worker_fqdn="worker.example",
        worker_port=17117,
    )
    manager.client = SimpleNamespace(
        nodes=SimpleNamespace(list=lambda **_kwargs: [node]),
        interfaces=SimpleNamespace(list=lambda **_kwargs: [interface]),
        services=SimpleNamespace(list=lambda **_kwargs: [service]),
    )
    deployment_script = "export API_PASSWORD=do-not-log-this-script"
    monkeypatch.setattr(
        manager,
        "_generate_nvcm_deploy_script",
        lambda **_kwargs: deployment_script,
    )
    monkeypatch.setattr(sim_manager_module.time, "sleep", lambda _seconds: None)

    with caplog.at_level("INFO"):
        result = manager.setup_nvcm_server(
            "simulation-id",
            NVCMServerConfig(use_existing_server="oob-mgmt-server"),
        )

    assert deployment_script not in caplog.text
    assert "content omitted from logs" in caplog.text
    assert result["deploy_script"] == deployment_script
