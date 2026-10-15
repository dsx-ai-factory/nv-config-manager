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

import signal
import subprocess
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_configure_etc_hosts_adds_provider_hostname_on_existing_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    commands: list[str] = []

    monkeypatch.setattr(manager, "_ssh_cmd", lambda _host, _port: ["ssh", "worker.example"])

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(cmd[-1])
        if len(commands) == 1:
            return SimpleNamespace(stdout="192.0.2.10\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(sim_manager_module.subprocess, "run", fake_run)

    assert manager.configure_etc_hosts(
        "worker.example",
        17117,
        additional_hostnames=("netbox.nvcm.air",),
    )
    assert "grep -Fqw -- nvcm.air" in commands[1]
    assert "grep -Fqw -- netbox.nvcm.air" in commands[1]
    assert "192.0.2.10 netbox.nvcm.air" in commands[1]


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


@pytest.mark.parametrize("cleanup_timeouts", [0, 1, 2])
def test_cloud_init_success_survives_log_stream_cleanup_timeout(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_timeouts: int,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    monkeypatch.setattr(manager, "_ssh_cmd", lambda _host, _port: ["ssh", "worker.example"])
    stream = StringIO(f"{manager._SETUP_COMPLETE_MARKER}\n")
    proc = Mock(pid=12345, stdout=stream)
    proc.wait.side_effect = [
        *[subprocess.TimeoutExpired("log stream", 5) for _ in range(cleanup_timeouts)],
        0,
    ]
    popen = Mock(return_value=proc)
    killpg = Mock()
    monkeypatch.setattr(sim_manager_module.subprocess, "Popen", popen)
    monkeypatch.setattr(
        sim_manager_module.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0))
    )
    monkeypatch.setattr(sim_manager_module.os, "killpg", killpg)
    monkeypatch.setattr(sim_manager_module.select, "select", lambda *_args: ([stream], [], []))

    assert manager.wait_for_cloud_init("worker.example", 22) is True
    assert popen.call_args.kwargs["start_new_session"] is True
    assert killpg.call_args_list[0].args == (proc.pid, signal.SIGTERM)
    assert killpg.call_count == (1 if cleanup_timeouts == 0 else 2)
    if cleanup_timeouts:
        assert killpg.call_args_list[1].args == (proc.pid, signal.SIGKILL)
    assert stream.closed


def test_cloud_init_tail_cleanup_handles_already_exited_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StringIO()
    proc = Mock(pid=12345, stdout=stream)
    monkeypatch.setattr(sim_manager_module.os, "killpg", Mock(side_effect=ProcessLookupError))

    AirSimulationManager._stop_cloud_init_tail(proc)

    proc.wait.assert_called_once_with(timeout=5)
    assert stream.closed


@pytest.mark.parametrize(
    ("scenario", "expected_success"),
    [
        ("status-timeout", True),
        ("marker-timeout", True),
        ("persistent-timeout", False),
        ("cloud-init-error", False),
        ("missing-marker", False),
    ],
)
def test_cloud_init_polling_retries_timeouts_without_logging_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    expected_success: bool,
) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    password = "dummy-ssh-password"
    ssh_base = ["sshpass", "-p", password, "ssh", "worker.example"]
    monkeypatch.setattr(manager, "_ssh_cmd", lambda _host, _port: ssh_base)
    stream = StringIO()
    proc = Mock(pid=12345, stdout=stream)
    proc.wait.return_value = 0
    monkeypatch.setattr(sim_manager_module.subprocess, "Popen", Mock(return_value=proc))
    killpg = Mock()
    monkeypatch.setattr(sim_manager_module.os, "killpg", killpg)

    elapsed = [0.0]
    monkeypatch.setattr(sim_manager_module.time, "monotonic", lambda: elapsed[0])

    def select_without_log_output(*_args):
        elapsed[0] += 11
        return [], [], []

    monkeypatch.setattr(sim_manager_module.select, "select", select_without_log_output)
    attempts = {"status": 0, "marker": 0}
    poll_timeouts = []

    def run(command, **kwargs):
        if command[-1] == "true":
            return SimpleNamespace(returncode=0)
        poll_timeouts.append(kwargs["timeout"])
        is_status = command[-3:] == ["cloud-init", "status", "--long"]
        kind = "status" if is_status else "marker"
        attempts[kind] += 1
        if (
            scenario == "persistent-timeout"
            or (scenario == "status-timeout" and is_status and attempts[kind] == 1)
            or (scenario == "marker-timeout" and not is_status and attempts[kind] == 1)
        ):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if is_status:
            status = "error" if scenario == "cloud-init-error" else "done"
            return SimpleNamespace(returncode=0, stdout=f"status: {status}", stderr="")
        return SimpleNamespace(returncode=1 if scenario == "missing-marker" else 0)

    monkeypatch.setattr(sim_manager_module.subprocess, "run", run)

    with caplog.at_level("INFO"):
        assert manager.wait_for_cloud_init("worker.example", 22, timeout=35) is expected_success

    assert password not in caplog.text
    assert "sshpass" not in caplog.text
    assert stream.closed
    killpg.assert_called_once_with(proc.pid, signal.SIGTERM)
    assert not proc.terminate.called
    assert all(0 < timeout <= 10 for timeout in poll_timeouts)
    if scenario == "persistent-timeout":
        assert attempts["status"] == 3
        assert poll_timeouts[-1] == 2
        assert "Timed out waiting for setup to complete" in caplog.text
    elif scenario == "status-timeout":
        assert attempts["status"] == 2
        assert "Cloud-init status check timed out" in caplog.text
    elif scenario == "marker-timeout":
        assert attempts["marker"] == 2
        assert "Setup marker check timed out" in caplog.text
    elif scenario == "cloud-init-error":
        assert "Cloud-init reported an error" in caplog.text
    else:
        assert "completed without the setup-complete marker" in caplog.text
