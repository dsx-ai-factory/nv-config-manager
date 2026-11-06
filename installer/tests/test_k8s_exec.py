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
"""Focused tests for Kubernetes loader pod and exec behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager_installer.k8s import K8sClient, PodExecError, ServiceProxy


def _client() -> K8sClient:
    k8s = object.__new__(K8sClient)
    k8s.kubeconfig = None
    k8s.v1 = MagicMock()
    k8s.apps_v1 = MagicMock()
    return k8s


def test_client_loads_explicit_kubeconfig(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "config"

    with (
        patch(
            "nv_config_manager_installer.k8s.kubectl_current_context",
            return_value="test-context",
        ) as current_context,
        patch("nv_config_manager_installer.k8s.config.load_kube_config") as load_kube_config,
        patch("nv_config_manager_installer.k8s.client.CoreV1Api"),
        patch("nv_config_manager_installer.k8s.client.AppsV1Api"),
        patch("nv_config_manager_installer.k8s.client.CoordinationV1Api"),
    ):
        k8s = K8sClient(kubeconfig=kubeconfig)

    assert k8s.kubeconfig == str(kubeconfig)
    current_context.assert_called_once_with(str(kubeconfig))
    load_kube_config.assert_called_once_with(
        config_file=str(kubeconfig),
        context="test-context",
    )


def test_client_reads_kubeconfig_environment_at_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    kubeconfig = "/tmp/first.yaml:/tmp/second.yaml"
    monkeypatch.setenv("KUBECONFIG", kubeconfig)

    with (
        patch(
            "nv_config_manager_installer.k8s.kubectl_current_context",
            return_value="test-context",
        ) as current_context,
        patch("nv_config_manager_installer.k8s.config.load_kube_config") as load_kube_config,
        patch("nv_config_manager_installer.k8s.client.CoreV1Api"),
        patch("nv_config_manager_installer.k8s.client.AppsV1Api"),
        patch("nv_config_manager_installer.k8s.client.CoordinationV1Api"),
    ):
        k8s = K8sClient()

    assert k8s.kubeconfig == kubeconfig
    current_context.assert_called_once_with(kubeconfig)
    load_kube_config.assert_called_once_with(
        config_file=kubeconfig,
        context="test-context",
    )


def test_exec_command_returns_output_after_zero_exit() -> None:
    k8s = _client()
    websocket = MagicMock()
    websocket.is_open.side_effect = [True, False]
    websocket.returncode = 0
    websocket.read_all.return_value = "done\n"

    with patch("nv_config_manager_installer.k8s.k8s_stream", return_value=websocket) as stream:
        output = k8s.exec_command("loader", "nv-config-manager", ["true"], timeout=30)

    assert output == "done\n"
    assert stream.call_args.kwargs["_request_timeout"] == 30
    websocket.close.assert_called_once()


@pytest.mark.parametrize("returncode", [0, 1, 2, 5, None])
def test_streaming_exec_checks_remote_exit_after_delivering_output(returncode) -> None:
    """Deliver all output before reporting nonzero or missing remote exit status."""
    k8s = _client()
    websocket = MagicMock()
    websocket.is_open.side_effect = [True, False]
    websocket.returncode = returncode
    websocket.read_stdout.return_value = "test output\nlast line"
    websocket.read_stderr.return_value = ""

    with patch("nv_config_manager_installer.k8s.k8s_stream", return_value=websocket):
        output = k8s.exec_command_streaming(
            "ztp", "test-ns", ["python"], container="sftp", stdin='{"token":"test-token"}\n'
        )
        assert next(output) == "test output"
        assert next(output) == "last line"
        if returncode == 0:
            with pytest.raises(StopIteration):
                next(output)
        else:
            with pytest.raises(PodExecError, match=f"failed with exit {returncode}") as failure:
                next(output)
            assert failure.value.returncode == returncode

    websocket.close.assert_called_once()
    websocket.write_stdin.assert_called_once_with('{"token":"test-token"}\n')


def test_kubectl_copy_uses_client_kubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    k8s = _client()
    k8s.kubeconfig = "/tmp/explicit.yaml"
    monkeypatch.setenv("KUBECONFIG", "/tmp/ambient.yaml")

    with (
        patch(
            "nv_config_manager_installer.k8s.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stderr=""),
        ) as run,
        patch.object(k8s, "_verify_remote_path"),
    ):
        k8s._copy_to_pod_kubectl(
            "/tmp/source",
            "loader",
            "nv-config-manager",
            "/tmp/destination",
        )

    assert run.call_args.kwargs["env"]["KUBECONFIG"] == "/tmp/explicit.yaml"


def test_port_forward_uses_client_kubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    k8s = _client()
    k8s.kubeconfig = "/tmp/explicit.yaml"
    monkeypatch.setenv("KUBECONFIG", "/tmp/ambient.yaml")
    process = MagicMock()

    with (
        patch("nv_config_manager_installer.k8s._find_free_port", return_value=12345),
        patch("nv_config_manager_installer.k8s._wait_for_port"),
        patch("nv_config_manager_installer.k8s.subprocess.Popen", return_value=process) as popen,
        k8s.port_forward("nautobot", "nv-config-manager") as local_port,
    ):
        assert local_port == 12345

    assert popen.call_args.kwargs["env"]["KUBECONFIG"] == "/tmp/explicit.yaml"
    process.terminate.assert_called_once()


def test_service_proxy_uses_client_kubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    k8s = _client()
    k8s.kubeconfig = "/tmp/explicit.yaml"
    monkeypatch.setenv("KUBECONFIG", "/tmp/ambient.yaml")
    process = MagicMock()
    proxy = ServiceProxy(k8s, "nautobot", "nv-config-manager")

    with (
        patch("nv_config_manager_installer.k8s._find_free_port", return_value=12345),
        patch("nv_config_manager_installer.k8s._wait_for_port"),
        patch("nv_config_manager_installer.k8s.subprocess.Popen", return_value=process) as popen,
    ):
        proxy.start()
        proxy.stop()

    assert popen.call_args.kwargs["env"]["KUBECONFIG"] == "/tmp/explicit.yaml"
    process.terminate.assert_called_once()


def test_exec_command_raises_for_remote_nonzero_exit() -> None:
    k8s = _client()
    websocket = MagicMock()
    websocket.is_open.return_value = False
    websocket.returncode = 17
    websocket.read_all.return_value = "permission denied"

    with (
        patch("nv_config_manager_installer.k8s.k8s_stream", return_value=websocket),
        pytest.raises(RuntimeError, match="failed with exit 17: permission denied"),
    ):
        k8s.exec_command("loader", "nv-config-manager", ["false"])

    websocket.close.assert_called_once()


def test_create_loader_pod_stays_alive_and_targets_node() -> None:
    k8s = _client()

    k8s.create_loader_pod(
        "loader",
        "nv-config-manager",
        "jobs",
        "/jobs",
        node_name="worker-a",
    )

    body = k8s.v1.create_namespaced_pod.call_args.args[1]
    assert body.spec.node_name == "worker-a"
    assert body.spec.containers[0].command == [
        "sh",
        "-c",
        "while true; do sleep 3600; done",
    ]


def test_get_pvc_mounted_node_uses_a_running_consumer() -> None:
    k8s = _client()
    pending = SimpleNamespace(
        status=SimpleNamespace(phase="Pending"),
        spec=SimpleNamespace(
            node_name="worker-pending",
            volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name="jobs"))],
        ),
    )
    running = SimpleNamespace(
        status=SimpleNamespace(phase="Running"),
        spec=SimpleNamespace(
            node_name="worker-a",
            volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name="jobs"))],
        ),
    )
    k8s.v1.list_namespaced_pod.return_value.items = [pending, running]

    assert k8s.get_pvc_mounted_node("jobs", "nv-config-manager") == "worker-a"


def test_restart_deployment_returns_new_generation() -> None:
    k8s = _client()
    k8s.apps_v1.patch_namespaced_deployment.return_value.metadata.generation = 9

    assert k8s.restart_deployment("render", "nv-config-manager") == 9
    patch_body = k8s.apps_v1.patch_namespaced_deployment.call_args.args[2]
    assert (
        "kubectl.kubernetes.io/restartedAt"
        in patch_body["spec"]["template"]["metadata"]["annotations"]
    )


def test_rollout_completion_waits_for_scaled_down_pods_to_exit() -> None:
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(generation=4),
        spec=SimpleNamespace(replicas=0),
        status=SimpleNamespace(observed_generation=4, replicas=1),
    )

    assert K8sClient._rollout_complete(deployment) is False

    deployment.status.replicas = 0
    assert K8sClient._rollout_complete(deployment) is True
