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

import json
import shlex
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
    assert "grep -Fqw -- mcp.nvcm.air" in commands[1]
    assert "192.0.2.10 mcp.nvcm.air" in commands[1]
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


def _bootstrap_deployment():
    return subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {
                            "imagePullSecrets": [{"name": "registry-credentials"}],
                            "securityContext": {"runAsUser": 1000},
                            "initContainers": [
                                {
                                    "name": "temporal-setup",
                                    "image": "registry.example/bootstrap:test",
                                    "imagePullPolicy": "Always",
                                    "env": [
                                        {"name": "TEMPORAL_ADDR", "value": "custom-frontend:7443"},
                                        {"name": "TEMPORAL_NAMESPACE", "value": "custom-namespace"},
                                    ],
                                }
                            ],
                        }
                    }
                }
            }
        ),
        stderr="",
    )


def _temporal_check_manager(monkeypatch, results, deployment_response=None, cleanup_response=None):
    manager = AirSimulationManager.__new__(AirSimulationManager)
    monkeypatch.setattr(manager, "_ssh_cmd", lambda _host, _port: ["ssh", "worker.example"])
    monkeypatch.setattr(sim_manager_module.time, "sleep", lambda _seconds: None)
    commands = []
    responses = iter(results)

    def run(cmd, **kwargs):
        commands.append(cmd[-1])
        if "kubectl get deployment/" in cmd[-1]:
            return deployment_response or _bootstrap_deployment()
        if "kubectl delete pod" in cmd[-1]:
            return cleanup_response or subprocess.CompletedProcess([], 0, stdout="", stderr="")
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(sim_manager_module.subprocess, "run", run)
    return manager, commands


def _attribute_response(exclude=()):
    return subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "customAttributes": {
                    name: kind
                    for name, kind in AirSimulationManager._TEMPORAL_SEARCH_ATTRIBUTES.items()
                    if name not in exclude
                }
            }
        ),
        stderr="",
    )


def test_temporal_search_attributes_use_deployed_bootstrap_cli(monkeypatch):
    manager, commands = _temporal_check_manager(monkeypatch, [_attribute_response()])
    assert manager.ensure_temporal_search_attributes("worker.example", 17117)
    assert len(commands) == 3
    args = shlex.split(commands[1])
    assert args[args.index("--image") + 1] == "registry.example/bootstrap:test"
    assert args[args.index("--image-pull-policy") + 1] == "Always"
    assert args[args.index("--address") + 1] == "custom-frontend:7443"
    assert args[args.index("--namespace") + 1] == "custom-namespace"
    assert args[args.index("--output") + 1] == "json"
    assert "python" not in args
    assert "--rm" in args
    overrides = json.loads(args[args.index("--overrides") + 1])["spec"]
    assert overrides["imagePullSecrets"] == [{"name": "registry-credentials"}]
    assert overrides["securityContext"] == {"runAsUser": 1000}
    assert overrides["automountServiceAccountToken"] is False
    assert overrides["activeDeadlineSeconds"] == 120
    pod = args[args.index("run") + 1]
    assert pod in commands[2]
    assert "--ignore-not-found --wait=false" in commands[2]


@pytest.mark.parametrize(
    "response",
    [
        subprocess.CompletedProcess([], 127, stdout="", stderr="executable not found"),
        subprocess.CompletedProcess([], 0, stdout="not JSON", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="[]", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.TimeoutExpired("ssh", 150),
    ],
)
def test_temporal_check_errors_cleanup_without_restart_or_missing_claim(
    monkeypatch, caplog, response
):
    manager, commands = _temporal_check_manager(monkeypatch, [response, response])
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=2)
    assert len(commands) == 6
    assert sum("kubectl delete pod" in command for command in commands) == 2
    assert not any("rollout" in command for command in commands)
    assert "Could not check Temporal search attributes" in caplog.text
    assert "Unable to verify Temporal search attributes" in caplog.text
    assert "Missing Temporal search attributes" not in caplog.text
    assert "still missing" not in caplog.text


def test_temporal_check_preserves_remote_error_diagnostics(monkeypatch, caplog):
    response = subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied")
    manager, commands = _temporal_check_manager(monkeypatch, [response])
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=1)
    assert "permission denied" in caplog.text
    assert "exit 1" in caplog.text


def test_temporal_check_retries_a_failed_query_without_restarting(monkeypatch):
    manager, commands = _temporal_check_manager(
        monkeypatch,
        [
            subprocess.TimeoutExpired("ssh", 150),
            _attribute_response(),
        ],
    )
    assert manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=2)
    assert len(commands) == 6
    assert not any("rollout" in command for command in commands)


def test_temporal_check_restarts_only_for_confirmed_missing_attributes(monkeypatch, caplog):
    success = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    manager, commands = _temporal_check_manager(
        monkeypatch,
        [
            _attribute_response(exclude=["ReadRoles"]),
            success,
            success,
            _attribute_response(),
        ],
    )
    assert manager.ensure_temporal_search_attributes("worker.example", 17117)
    assert len(commands) == 8
    assert "rollout restart" in commands[3]
    assert "rollout status" in commands[4]
    assert "Missing Temporal search attributes (attempt 1/3): ReadRoles" in caplog.text


@pytest.mark.parametrize("fail_rollout", [False, True])
def test_temporal_check_reports_failed_recovery(monkeypatch, caplog, fail_rollout):
    responses = [_attribute_response(exclude=["ReadRoles"])]
    if fail_rollout:
        responses.append(subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    responses.append(subprocess.CompletedProcess([], 1, stdout="", stderr="rollout failed"))
    manager, commands = _temporal_check_manager(monkeypatch, responses)
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117)
    assert "Could not restart Temporal worker or complete its rollout" in caplog.text
    assert "rollout failed" in caplog.text


def test_temporal_check_matches_exact_attribute_names(monkeypatch, caplog):
    response = _attribute_response(exclude=["User"])
    attributes = json.loads(response.stdout)
    attributes["customAttributes"]["UserSuffix"] = "Keyword"
    response.stdout = json.dumps(attributes)
    manager, commands = _temporal_check_manager(monkeypatch, [response])
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=1)
    assert "still missing after 1 attempts: User." in caplog.text
    assert len(commands) == 3


@pytest.mark.parametrize(
    "deployment_response",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"),
        subprocess.CompletedProcess(
            [], 0, stdout='{"spec": {"template": {"spec": {"initContainers": []}}}}', stderr=""
        ),
        subprocess.CompletedProcess([], 0, stdout="not JSON", stderr=""),
        subprocess.CompletedProcess([], 0, stdout='{"unexpected": []}', stderr=""),
    ],
)
def test_temporal_bootstrap_discovery_failures_do_not_create_pods_or_restart(
    monkeypatch, caplog, deployment_response
):
    manager, commands = _temporal_check_manager(
        monkeypatch, [], deployment_response=deployment_response
    )
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=1)
    assert len(commands) == 1
    assert "kubectl get deployment/" in commands[0]
    assert "Could not check Temporal search attributes" in caplog.text
    assert "still missing" not in caplog.text


def test_temporal_check_reports_cleanup_failure_without_losing_query_result(monkeypatch, caplog):
    cleanup = subprocess.CompletedProcess([], 1, stdout="", stderr="delete denied")
    manager, commands = _temporal_check_manager(
        monkeypatch, [_attribute_response()], cleanup_response=cleanup
    )
    assert manager.ensure_temporal_search_attributes("worker.example", 17117)
    assert "Could not clean up Temporal check pod" in caplog.text
    assert "delete denied" in caplog.text


def test_temporal_check_handles_an_empty_custom_attribute_map(monkeypatch, caplog):
    response = subprocess.CompletedProcess(
        [], 0, stdout='{"systemAttributes": {"WorkflowId": "Keyword"}}', stderr=""
    )
    manager, commands = _temporal_check_manager(monkeypatch, [response])
    assert not manager.ensure_temporal_search_attributes("worker.example", 17117, max_attempts=1)
    assert "Missing Temporal search attributes" in caplog.text
    assert "Unable to verify" not in caplog.text
