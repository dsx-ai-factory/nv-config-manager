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
"""Tests for nv_config_manager_installer.deployer -- step sequencing, callbacks, and re-run detection."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nv_config_manager_installer.accounts import build_config_secrets_ini
from nv_config_manager_installer.deployer import (
    Deployer,
    DeployOptions,
    DeployStep,
    StepStatus,
    _get_image_digest_tag,
    _hash_content_dir,
    _kind_preload_images,
    _parallel_build_limit,
    _ParallelCommand,
    _poll_pod_summary,
    _RerunState,
    _run_logged_parallel,
    _unready_pod_summary_lines,
    find_project_root,
)
from nv_config_manager_installer.k8s import LOADER_POD_IMAGE
from nv_config_manager_installer.nautobot_jobs import NautobotJobRunner
from nv_config_manager_installer.schema import (
    ClusterConfig,
    ContentConfig,
    DCIMConfig,
    GatewayType,
    GitTokenEntry,
    ImagePullSecret,
    ImagesConfig,
    ImageSource,
    InfrastructureConfig,
    JobPath,
    JobsConfig,
    K8sSecretGroup,
    KubernetesSecretsConfig,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    PostDeployJob,
    RedfishConfig,
    RedfishVendorCreds,
    SecretsConfig,
    SecretsMethod,
    ServicesConfig,
    SiteConfig,
    TemplatePath,
    ZTPStorageConfig,
    ZTPStorageType,
)

_OPERATOR_VERSIONS = """\
GATEWAY_API_VERSION=v1.4.1
ENVOY_GATEWAY_VERSION=v1.6.5
CERT_MANAGER_VERSION=v1.20.2
CNPG_OPERATOR_VERSION=0.28.0
PROMETHEUS_CRD_VERSION=v0.90.1
PROMETHEUS_OPERATOR_VERSION=84.5.0
"""

_ENVOY_GATEWAY_CRDS = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: referencegrants.gateway.networking.k8s.io
spec:
  group: gateway.networking.k8s.io
---
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: envoyproxies.gateway.envoyproxy.io
spec:
  group: gateway.envoyproxy.io
"""


def _make_config() -> NVConfigManagerInstallConfig:
    return NVConfigManagerInstallConfig(
        cluster=ClusterConfig(hostname="test.local"),
        secrets=SecretsConfig(config_manager_service_username="nv-config-manager"),
        sites=[SiteConfig(name="dc01")],
    )


_TEST_KUBE_CONTEXT = "test-context"


@pytest.fixture(autouse=True)
def _stub_kube_context_helpers():
    """Stub the module-level kube-context helpers used by Deployer.run().

    Two helpers in ``nv_config_manager_installer.deployer`` reach past the K8sClient mock
    and shell out to real ``kubectl``:

    * ``kubectl_current_context()`` — the prereq step calls this to detect a
      mismatch between the Python kubernetes client's bound context and what
      kubectl reports. On a CI runner that has kubectl installed, its
      current-context would never match the mock's ``_TEST_KUBE_CONTEXT`` and
      the mismatch check would raise.
    * ``pin_kubeconfig_to_current_context()`` — Deployer.run() calls this
      first thing to materialize a single-context kubeconfig under /tmp. We
      don't want tests touching the host filesystem or invoking kubectl.
    * ``_gateway_class_helm_owner()`` — helm install checks whether an
      existing GatewayClass belongs to another Helm release. Most deployer
      tests are not exercising that cluster probe and should not require
      kubectl to be installed.

    Pin these boundaries: the context helper returns the same value the mock
    K8sClient advertises; the pinning helper is a no-op (returns None,
    deployer just logs a warning and proceeds); the GatewayClass helper
    behaves as though the resource is absent.
    """
    with (
        patch(
            "nv_config_manager_installer.deployer.kubectl_current_context",
            return_value=_TEST_KUBE_CONTEXT,
        ),
        patch(
            "nv_config_manager_installer.deployer.pin_kubeconfig_to_current_context",
            return_value=None,
        ),
        patch(
            "nv_config_manager_installer.deployer._gateway_class_helm_owner",
            return_value=None,
        ),
    ):
        yield


def _mock_k8s():
    """Create a MagicMock that satisfies the K8sClient interface."""
    k8s = MagicMock()
    k8s.check_connectivity.return_value = True
    k8s.ensure_namespace.return_value = False
    k8s.secret_exists.return_value = False
    k8s.read_secret_data.return_value = {}
    k8s.get_pvc_annotation.return_value = ""
    k8s.list_deployment_names.return_value = []
    k8s.restart_deployment.return_value = 0
    # Match what the real K8sClient sets after binding to a context. The
    # deployer's prereq + create-namespace steps now check these explicitly to
    # detect Python-vs-kubectl context drift; a bare MagicMock would surface
    # as `<MagicMock ...>` and fail the equality check.
    k8s.active_context = _TEST_KUBE_CONTEXT
    k8s.api_server = "https://test.local:6443"
    k8s.namespace_phase.return_value = "Active"
    return k8s


class RecordingCallback:
    """Test callback that records all events."""

    def __init__(self):
        self.step_updates: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.completed: list[tuple[bool, list[str]]] = []

    def on_step_update(self, step: DeployStep) -> None:
        self.step_updates.append((step.id, step.status))

    def on_log(self, message: str) -> None:
        self.logs.append(message)

    def on_complete(self, success: bool, endpoints: list[str]) -> None:
        self.completed.append((success, endpoints))


def test_project_root_defaults_to_installer_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated repository in cwd cannot capture installer path resolution."""
    (tmp_path / "Makefile").write_text("wrapper repository\n")
    monkeypatch.chdir(tmp_path)

    assert find_project_root() == Path(__file__).resolve().parents[2]
    assert find_project_root(tmp_path) == tmp_path.resolve()


class TestDeployerInit:
    def test_steps_initialized(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        assert len(deployer.steps) == 19
        assert all(s.status == StepStatus.PENDING for s in deployer.steps)

    def test_step_ids_unique(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        ids = [s.id for s in deployer.steps]
        assert len(ids) == len(set(ids))

    def test_revalidates_tui_config_before_deployment(self):
        config = _make_config()
        config.services.nautobot = False
        config.services.external_nautobot_url = "https://nb.example.com"
        config.content.run_after_deploy = [PostDeployJob(job="jobs.bootstrap.SiteBootstrap")]

        with pytest.raises(ValueError, match="post-deploy jobs require a local Nautobot"):
            Deployer(config, DeployOptions())


class TestGatewayClassReuse:
    @patch(
        "nv_config_manager_installer.deployer._gateway_class_helm_owner",
        return_value=("kiwi-platform", "kiwi"),
    )
    def test_reuses_gateway_class_owned_by_another_release(self, mock_owner):
        config = _make_config()
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)

        assert deployer._should_reuse_existing_gateway_class() is True
        assert any("gateway.createGatewayClass=false" in line for line in callback.logs)
        mock_owner.assert_called_once_with()

    @patch(
        "nv_config_manager_installer.deployer._gateway_class_helm_owner",
        return_value=("nv-config-manager", "nv-config-manager"),
    )
    def test_keeps_gateway_class_when_owned_by_current_release(self, mock_owner):
        config = _make_config()
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)

        assert deployer._should_reuse_existing_gateway_class() is False
        assert callback.logs == []
        mock_owner.assert_called_once_with()

    @patch("nv_config_manager_installer.deployer._gateway_class_helm_owner", return_value=None)
    def test_keeps_gateway_class_when_absent(self, mock_owner):
        config = _make_config()
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)

        assert deployer._should_reuse_existing_gateway_class() is False
        assert callback.logs == []
        mock_owner.assert_called_once_with()

    @patch(
        "nv_config_manager_installer.deployer._gateway_class_helm_owner",
        return_value=("kiwi-platform", "kiwi"),
    )
    def test_keeps_gateway_class_when_creation_disabled_in_config(self, mock_owner):
        config = _make_config()
        config.infrastructure = InfrastructureConfig(create_gateway_class=False)
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)

        assert deployer._should_reuse_existing_gateway_class() is False
        assert callback.logs == []
        mock_owner.assert_not_called()


class TestStepSequencing:
    def test_derived_provider_hooks_wrap_helm_install(self, monkeypatch, tmp_path):
        calls: list[str] = []

        class ProviderDeployer(Deployer):
            def _run_provider_pre_helm_install(self) -> None:
                calls.append("provider-pre")

            def _helm_install(self) -> None:
                calls.append("helm")

            def _run_provider_post_helm_install(self) -> None:
                calls.append("provider-post")

        deployer = ProviderDeployer(_make_config(), DeployOptions(), RecordingCallback())
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.find_project_root", lambda _explicit: tmp_path
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.pin_kubeconfig_to_current_context", lambda: None
        )
        monkeypatch.setattr(deployer, "_validate_project_paths", lambda _root: None)
        for method_name in (
            "_check_prerequisites",
            "_detect_existing_state",
            "_build_images",
            "_load_kind",
            "_install_crds",
            "_create_namespace",
            "_create_secrets",
            "_populate_vault",
            "_setup_jobs_pvc",
            "_setup_templates_pvc",
            "_setup_ztp_pvc",
            "_generate_values",
            "_patch_gateway",
            "_restart_nautobot",
            "_restart_render_service",
            "_run_post_deploy_jobs",
            "_refresh_caches",
            "_run_integration_tests",
        ):
            monkeypatch.setattr(deployer, method_name, lambda: None)
        monkeypatch.setattr(deployer, "_collect_endpoints", lambda: [])

        assert deployer.run() is True
        assert calls == ["provider-pre", "helm", "provider-post"]

    @patch("nv_config_manager_installer.deployer.shutil.which", return_value=None)
    def test_prereqs_fail_when_kubectl_missing(self, mock_which):
        config = _make_config()
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)
        success = deployer.run()
        assert not success
        assert any(s[1] == StepStatus.FAILED for s in callback.step_updates)

    def test_prereqs_require_docker_for_load_kind(self, monkeypatch):
        def fake_which(tool):
            return None if tool == "docker" else f"/usr/bin/{tool}"

        monkeypatch.setattr("nv_config_manager_installer.deployer.shutil.which", fake_which)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.K8sClient",
            lambda *a, **kw: _mock_k8s(),
        )

        deployer = Deployer(
            _make_config(),
            DeployOptions(load_kind=True, kind_cluster="x"),
            RecordingCallback(),
        )
        with pytest.raises(RuntimeError, match=r"docker is required for --load-kind"):
            deployer._check_prerequisites()

    def test_prereqs_require_running_docker_daemon_for_load_kind(self, monkeypatch):
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.shutil.which",
            lambda tool: f"/usr/bin/{tool}",
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.K8sClient",
            lambda *a, **kw: _mock_k8s(),
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer.kubectl_current_context",
            lambda: None,
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "info"]:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)

        deployer = Deployer(
            _make_config(),
            DeployOptions(load_kind=True, kind_cluster="x"),
            RecordingCallback(),
        )
        with pytest.raises(
            RuntimeError,
            match=r"docker daemon is required for --load-kind but is not reachable",
        ):
            deployer._check_prerequisites()

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_skip_steps_when_not_requested(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s_cls.return_value = _mock_k8s()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = mock_result
        mock_run_logged.return_value = mock_result
        config = _make_config()
        callback = RecordingCallback()
        options = DeployOptions(dry_run=True)
        deployer = Deployer(config, options, callback)
        deployer.run()
        statuses = dict(callback.step_updates)
        assert statuses.get("build-images") == StepStatus.SKIPPED
        assert statuses.get("load-kind") == StepStatus.SKIPPED
        assert statuses.get("install-crds") == StepStatus.SKIPPED
        assert statuses.get("helm-install") == StepStatus.SKIPPED

    def test_callback_protocol(self):
        cb = RecordingCallback()
        step = DeployStep(id="test", label="Test step", status=StepStatus.RUNNING)
        cb.on_step_update(step)
        cb.on_log("test message")
        cb.on_complete(True, ["http://test/"])
        assert len(cb.step_updates) == 1
        assert len(cb.logs) == 1
        assert len(cb.completed) == 1


class TestInstallCrds:
    def test_kgateway_rejects_envoy_gateway_install(self):
        config = _make_config()
        config.infrastructure.gateway = GatewayType.KGATEWAY
        deployer = Deployer(
            config,
            DeployOptions(install_envoy_gateway=True),
            RecordingCallback(),
        )

        with pytest.raises(RuntimeError, match="install kgateway"):
            deployer._install_crds()

    def test_cert_manager_online_install_uses_matching_pin(self, tmp_path, monkeypatch):
        (tmp_path / "helm").mkdir()
        (tmp_path / "operator-versions.env").write_text(_OPERATOR_VERSIONS)
        run_commands: list[list[str]] = []
        logged_commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        deployer = Deployer(
            _make_config(),
            DeployOptions(chart_dir=str(tmp_path / "helm"), install_cert_manager=True),
            RecordingCallback(),
        )
        deployer._install_crds()

        assert any("charts.jetstack.io" in " ".join(cmd) for cmd in run_commands)
        assert any("v1.20.2/cert-manager.crds.yaml" in " ".join(cmd) for cmd in logged_commands)
        cert_cmd = next(
            cmd
            for cmd in logged_commands
            if cmd[:4] == ["helm", "upgrade", "--install", "cert-manager"]
        )
        assert cert_cmd[cert_cmd.index("--version") + 1] == "v1.20.2"

    def test_keda_online_install_leaves_upstream_images_alone(self, tmp_path, monkeypatch):
        """With no private registry, KEDA's image defaults must be left untouched.

        The chart's repository values are registry-relative ("kedacore/keda"), so
        emitting the ``image.*.registry=""`` that the private-registry path needs
        would resolve them against Docker Hub instead of ghcr.io.
        """
        (tmp_path / "helm").mkdir()
        (tmp_path / "operator-versions.env").write_text(_OPERATOR_VERSIONS)
        run_commands: list[list[str]] = []
        logged_commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        config = _make_config()
        config.infrastructure.monitoring.observability_enabled = True
        deployer = Deployer(
            config,
            DeployOptions(chart_dir=str(tmp_path / "helm")),
            RecordingCallback(),
        )
        deployer._install_crds()

        keda_cmd = next(
            cmd for cmd in logged_commands if cmd[:4] == ["helm", "upgrade", "--install", "keda"]
        )
        assert "kedacore/keda" in keda_cmd
        assert keda_cmd[keda_cmd.index("--version") + 1] == "2.20.1"
        assert any("kedacore.github.io" in " ".join(cmd) for cmd in run_commands)
        assert not any(arg.startswith("image.") for arg in keda_cmd), (
            f"KEDA install should not override images without a registry: {keda_cmd}"
        )

    def test_airgap_operator_installs_use_local_artifacts(self, tmp_path, monkeypatch):
        root = tmp_path / "bundle"
        chart_dir = root / "helm"
        charts_dir = root / "charts"
        manifests_dir = root / "manifests"
        chart_dir.mkdir(parents=True)
        charts_dir.mkdir()
        manifests_dir.mkdir()
        (root / "operator-versions.env").write_text(_OPERATOR_VERSIONS)
        (charts_dir / "cert-manager-v1.20.2.tgz").touch()
        (charts_dir / "cloudnative-pg-0.28.0.tgz").touch()
        (charts_dir / "gateway-helm-v1.6.5.tgz").touch()
        (charts_dir / "prometheus-operator-crds-28.0.1.tgz").touch()
        (charts_dir / "keda-2.20.1.tgz").touch()
        (manifests_dir / "gateway-api-v1.4.1.yaml").touch()

        run_commands: list[list[str]] = []
        logged_commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            if cmd[:3] == ["helm", "show", "crds"]:
                return MagicMock(returncode=0, stdout=_ENVOY_GATEWAY_CRDS, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        config = _make_config()
        config.cluster.airgapped = True
        config.infrastructure.monitoring.observability_enabled = True
        config.images = ImagesConfig(
            source=ImageSource.REGISTRY,
            registry="registry.example.com/nv-config-manager",
        )
        deployer = Deployer(
            config,
            DeployOptions(
                chart_dir=str(chart_dir),
                install_envoy_gateway=True,
                install_cert_manager=True,
                install_cnpg_operator=True,
            ),
            RecordingCallback(),
        )
        deployer._install_crds()

        rendered_commands = [" ".join(cmd) for cmd in logged_commands]
        assert any(
            str(manifests_dir / "gateway-api-v1.4.1.yaml") in cmd for cmd in rendered_commands
        )
        assert any("envoy-gateway-crds.yaml" in cmd for cmd in rendered_commands)
        assert any(str(charts_dir / "gateway-helm-v1.6.5.tgz") in cmd for cmd in rendered_commands)
        envoy_cmd = next(
            cmd for cmd in logged_commands if cmd[:4] == ["helm", "upgrade", "--install", "eg"]
        )
        assert "--skip-crds" in envoy_cmd
        assert "--force-conflicts" not in envoy_cmd
        assert "--take-ownership" not in envoy_cmd
        assert (
            "global.images.envoyGateway.image="
            "registry.example.com/nv-config-manager/envoyproxy/gateway:v1.6.5" in envoy_cmd
        )
        assert (
            "global.images.ratelimit.image="
            "registry.example.com/nv-config-manager/envoyproxy/ratelimit:c8765e89" in envoy_cmd
        )

        cert_cmd = next(
            cmd
            for cmd in logged_commands
            if cmd[:4] == ["helm", "upgrade", "--install", "cert-manager"]
        )
        assert any(str(charts_dir / "cert-manager-v1.20.2.tgz") in cmd for cmd in rendered_commands)
        assert any("crds.enabled=true" in cmd for cmd in rendered_commands)
        assert (
            "image.repository=registry.example.com/nv-config-manager/jetstack/cert-manager-controller"
            in cert_cmd
        )
        assert "image.tag=v1.20.2" in cert_cmd
        assert (
            "webhook.image.repository=registry.example.com/nv-config-manager/"
            "jetstack/cert-manager-webhook" in cert_cmd
        )
        assert (
            "cainjector.image.repository=registry.example.com/nv-config-manager/"
            "jetstack/cert-manager-cainjector" in cert_cmd
        )
        assert (
            "startupapicheck.image.repository=registry.example.com/nv-config-manager/"
            "jetstack/cert-manager-startupapicheck" in cert_cmd
        )
        assert (
            "acmesolver.image.repository=registry.example.com/nv-config-manager/"
            "jetstack/cert-manager-acmesolver" in cert_cmd
        )

        cnpg_cmd = next(
            cmd for cmd in logged_commands if cmd[:4] == ["helm", "upgrade", "--install", "cnpg"]
        )
        assert any(
            str(charts_dir / "cloudnative-pg-0.28.0.tgz") in cmd for cmd in rendered_commands
        )
        assert (
            "image.repository=registry.example.com/nv-config-manager/cloudnative-pg/cloudnative-pg"
            in cnpg_cmd
        )
        assert "image.tag=1.29.0" in cnpg_cmd

        # KEDA comes with observability_enabled (it reconciles the ScaledObjects
        # values-observability.yaml turns on), so it has to resolve from the
        # bundle too -- and must not fall back to `helm repo add`, which has no
        # network to reach in an airgapped install.
        keda_cmd = next(
            cmd for cmd in logged_commands if cmd[:4] == ["helm", "upgrade", "--install", "keda"]
        )
        assert str(charts_dir / "keda-2.20.1.tgz") in keda_cmd
        assert "--version" not in keda_cmd
        assert not any(cmd[:3] == ["helm", "repo", "add"] for cmd in run_commands)
        # All three KEDA images must come from the private registry. The chart
        # composes registry + repository, so the registry has to be blanked too or
        # the rewritten repository would end up appended to ghcr.io.
        for value_prefix, repository in (
            ("image.keda", "kedacore/keda"),
            ("image.metricsApiServer", "kedacore/keda-metrics-apiserver"),
            ("image.webhooks", "kedacore/keda-admission-webhooks"),
        ):
            assert f"{value_prefix}.registry=" in keda_cmd
            assert (
                f"{value_prefix}.repository=registry.example.com/nv-config-manager/{repository}"
                in keda_cmd
            )
            assert f"{value_prefix}.tag=2.20.1" in keda_cmd

        prom_crds_cmd = next(
            cmd
            for cmd in logged_commands
            if cmd[:4] == ["helm", "upgrade", "--install", "nv-config-manager-prom-crds"]
        )
        assert str(charts_dir / "prometheus-operator-crds-28.0.1.tgz") in prom_crds_cmd
        assert run_commands == [
            ["helm", "show", "crds", str(charts_dir / "gateway-helm-v1.6.5.tgz")]
        ]
        assert not any("github.com/cert-manager" in cmd for cmd in rendered_commands)


class TestGatewayPatching:
    def test_kgateway_skips_envoy_host_port_patch(self):
        config = _make_config()
        config.infrastructure.gateway = GatewayType.KGATEWAY
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)

        deployer._patch_gateway()

        statuses = dict(callback.step_updates)
        assert statuses["patch-gateway"] == StepStatus.SKIPPED
        assert "GatewayParameters" in deployer._get_step("patch-gateway").output[0]


class TestDeployOptions:
    def test_defaults(self):
        opts = DeployOptions()
        assert opts.chart_dir == "deploy/helm"
        assert opts.build_images is False
        assert opts.load_kind is False
        assert opts.helm_timeout == "15m"
        assert opts.helm_debug is False
        assert opts.watch_pods is False
        assert opts.dry_run is False
        assert opts.populate_vault is True

    def test_custom_options(self):
        opts = DeployOptions(
            build_images=True,
            load_kind=True,
            kind_cluster="test-cluster",
            helm_debug=True,
            watch_pods=True,
            dry_run=True,
            populate_vault=False,
        )
        assert opts.build_images is True
        assert opts.kind_cluster == "test-cluster"
        assert opts.helm_debug is True
        assert opts.watch_pods is True
        assert opts.populate_vault is False


def test_vault_population_can_use_preprovisioned_eso_paths() -> None:
    config = _make_config()
    config.secrets = SecretsConfig(method=SecretsMethod.ESO)
    callback = RecordingCallback()
    deployer = Deployer(config, DeployOptions(populate_vault=False), callback)

    deployer._populate_vault()

    step = deployer._get_step("populate-vault")
    assert step.status == StepStatus.SKIPPED
    assert step.output == ["Vault population disabled; using pre-provisioned ESO paths"]


class TestImageBuilds:
    def test_parallel_build_limit_defaults_and_env_override(self, monkeypatch):
        monkeypatch.delenv("NVCM_DOCKER_BUILD_PARALLELISM", raising=False)
        assert _parallel_build_limit(6) >= 1
        assert _parallel_build_limit(6) <= 4

        monkeypatch.setenv("NVCM_DOCKER_BUILD_PARALLELISM", "2")
        assert _parallel_build_limit(6) == 2

        monkeypatch.setenv("NVCM_DOCKER_BUILD_PARALLELISM", "99")
        assert _parallel_build_limit(6) == 6

        monkeypatch.setenv("NVCM_DOCKER_BUILD_PARALLELISM", "not-an-int")
        assert _parallel_build_limit(6) >= 1

    def test_run_logged_parallel_prefixes_logs_and_reports_progress(self):
        step = DeployStep("build-images", "Build local images")
        callback = RecordingCallback()
        commands = [
            _ParallelCommand(
                "one",
                [
                    sys.executable,
                    "-c",
                    (
                        "import time; "
                        "print('one start', flush=True); "
                        "time.sleep(0.4); "
                        "print('one done', flush=True)"
                    ),
                ],
                timeout=5,
            ),
            _ParallelCommand(
                "two",
                [
                    sys.executable,
                    "-c",
                    (
                        "import time; "
                        "print('two start', flush=True); "
                        "time.sleep(0.4); "
                        "print('two done', flush=True)"
                    ),
                ],
                timeout=5,
            ),
        ]

        _run_logged_parallel(commands, step, callback, max_parallel=2, progress_interval=0.05)

        one_start = callback.logs.index("[one] one start")
        two_start = callback.logs.index("[two] two start")
        one_done = callback.logs.index("[one] one done")

        assert one_start < one_done
        assert two_start < one_done
        assert any(
            "[one] running" in line and "latest: one start" in line for line in callback.logs
        )
        assert any(line.startswith("[one] completed in ") for line in callback.logs)
        assert any(line.startswith("[two] completed in ") for line in callback.logs)

    def test_build_images_runs_parallel_builds_and_tags(self, monkeypatch):
        parallel_calls: list[tuple[list[_ParallelCommand], int]] = []
        run_commands: list[list[str]] = []

        def fake_run_logged_parallel(commands, step, callback, *, max_parallel, **kwargs):
            parallel_calls.append((commands, max_parallel))
            for command in commands:
                callback.on_log(f"[{command.label}] completed in 0s")

        def fake_digest(image: str) -> str:
            return f"sha-{image.removeprefix('nv-config-manager-')[:8]}"

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setenv("NVCM_DOCKER_BUILD_PARALLELISM", "2")
        monkeypatch.delenv("BUILDX_BUILDER", raising=False)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_parallel",
            fake_run_logged_parallel,
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._get_image_digest_tag",
            fake_digest,
        )
        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)

        deployer = Deployer(
            _make_config(),
            DeployOptions(build_images=True),
            RecordingCallback(),
        )
        deployer._build_images()

        commands, max_parallel = parallel_calls[0]
        assert max_parallel == 2
        assert len(commands) == 9
        assert all(
            command.cmd[:4] == ["docker", "build", "--provenance=false", "--progress=plain"]
            for command in commands
        )
        assert all("--load" not in command.cmd for command in commands)
        assert all(command.timeout == 900 for command in commands)
        assert all(command.env and command.env["DOCKER_BUILDKIT"] == "1" for command in commands)
        assert len(run_commands) == 9
        assert all(cmd[:2] == ["docker", "tag"] for cmd in run_commands)
        assert deployer._local_image_tags["nv-config-manager-ui"].startswith("sha-")
        targets = {
            command.label: command.cmd[command.cmd.index("--target") + 1]
            for command in commands
            if "--target" in command.cmd
        }
        assert targets == {
            "nv-config-manager-temporal": "server",
            "nv-config-manager-temporal-bootstrap": "bootstrap",
            "nv-config-manager-temporal-ui": "ui",
        }

    def test_build_images_loads_buildx_container_outputs(self, monkeypatch):
        parallel_calls: list[list[_ParallelCommand]] = []

        def fake_run_logged_parallel(commands, step, callback, *, max_parallel, **kwargs):
            parallel_calls.append(commands)
            for command in commands:
                callback.on_log(f"[{command.label}] completed in 0s")

        monkeypatch.setenv("BUILDX_BUILDER", "ci-builder")
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_parallel",
            fake_run_logged_parallel,
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._get_image_digest_tag",
            lambda image: "",
        )

        deployer = Deployer(
            _make_config(),
            DeployOptions(build_images=True),
            RecordingCallback(),
        )
        deployer._build_images()

        commands = parallel_calls[0]
        assert len(commands) == 9
        assert all("--load" in command.cmd for command in commands)
        assert all(command.cmd[:3] == ["docker", "buildx", "build"] for command in commands)
        assert all(
            command.env and command.env["BUILDX_BUILDER"] == "ci-builder" for command in commands
        )

    def test_build_images_forwards_numpy_source_build_args_to_service_image(self, monkeypatch):
        parallel_calls: list[list[_ParallelCommand]] = []

        def fake_run_logged_parallel(commands, step, callback, *, max_parallel, **kwargs):
            parallel_calls.append(commands)
            for command in commands:
                callback.on_log(f"[{command.label}] completed in 0s")

        monkeypatch.setenv("NVCM_NUMPY_FROM_SOURCE", "true")
        monkeypatch.setenv("NVCM_NUMPY_CPU_BASELINE", "min")
        monkeypatch.setenv("NVCM_NUMPY_CPU_DISPATCH", "max")
        monkeypatch.setenv("NVCM_NUMPY_ALLOW_NOBLAS", "true")
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_parallel",
            fake_run_logged_parallel,
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._get_image_digest_tag",
            lambda image: "",
        )

        deployer = Deployer(
            _make_config(),
            DeployOptions(build_images=True),
            RecordingCallback(),
        )
        deployer._build_images()

        commands = parallel_calls[0]
        service_cmd = next(
            command.cmd for command in commands if command.label == "nv-config-manager"
        )
        nautobot_cmd = next(
            command.cmd for command in commands if command.label == "nv-config-manager-nautobot"
        )
        assert "--build-arg" in service_cmd
        assert "NVCM_NUMPY_FROM_SOURCE=true" in service_cmd
        assert "NVCM_NUMPY_CPU_BASELINE=min" in service_cmd
        assert "NVCM_NUMPY_CPU_DISPATCH=max" in service_cmd
        assert "NVCM_NUMPY_ALLOW_NOBLAS=true" in service_cmd
        assert "NVCM_NUMPY_FROM_SOURCE=true" not in nautobot_cmd


class TestKindImageLoading:
    def test_external_dcim_excludes_nautobot_from_local_images(self):
        config = _make_config()
        config.services.nautobot = False
        config.dcim = DCIMConfig(provider="synthetic", server="https://synthetic.example")
        deployer = Deployer(config, DeployOptions(), RecordingCallback())

        image_names = {build[0] for build in deployer._local_image_builds()}

        assert "nv-config-manager-nautobot" not in image_names
        assert len(image_names) == 8

    def test_external_dcim_does_not_load_nautobot_image(self, monkeypatch):
        logged_commands: list[list[str]] = []
        config = _make_config()
        config.services.nautobot = False
        config.dcim = DCIMConfig(provider="synthetic", server="https://synthetic.example")
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged",
            lambda cmd, *_args, **_kwargs: logged_commands.append(cmd),
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._docker_server_platform",
            lambda: "linux/amd64",
        )
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._kind_preload_images", lambda _config: []
        )
        deployer = Deployer(
            config,
            DeployOptions(load_kind=True, kind_cluster="test-cluster"),
            RecordingCallback(),
        )

        deployer._load_kind()

        loaded_images = {
            command[3]
            for command in logged_commands
            if command[:3] == ["kind", "load", "docker-image"]
        }
        assert "nv-config-manager-nautobot:local" not in loaded_images
        assert len(loaded_images) == 8

    def test_kind_preload_images_include_defaults_config_and_env(self, monkeypatch):
        config = _make_config()
        config.images.kind_preload_images = [
            "docker.io/library/redis:7-alpine",
            "docker.io/library/busybox:1.36",
        ]
        monkeypatch.setenv(
            "NVCM_KIND_PRELOAD_IMAGES",
            "docker.io/library/nats:2.14-alpine,docker.io/library/redis:7-alpine",
        )

        assert _kind_preload_images(config) == [
            "docker.io/library/busybox:1.36",
            "docker.io/library/redis:7-alpine",
            "docker.io/library/nats:2.14-alpine",
        ]

    def test_load_kind_tags_arch_specific_loader_image_as_canonical(self, monkeypatch):
        run_commands: list[list[str]] = []
        logged_commands: list[list[str]] = []
        pipe_commands: list[tuple[list[str], list[str]]] = []

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            if cmd[:3] == ["docker", "version", "--format"]:
                return MagicMock(returncode=0, stdout="linux/amd64\n", stderr="")
            if cmd[:4] == ["docker", "image", "inspect", "--format"]:
                return MagicMock(returncode=0, stdout="sha256:busyboxid\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged_pipe(source_cmd, sink_cmd, step, callback, **kwargs):
            pipe_commands.append((source_cmd, sink_cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_pipe",
            fake_run_logged_pipe,
        )

        deployer = Deployer(
            _make_config(),
            DeployOptions(load_kind=True, kind_cluster="test-cluster"),
            RecordingCallback(),
        )
        deployer._load_kind()

        assert {
            command[3]
            for command in logged_commands
            if command[:3] == ["kind", "load", "docker-image"]
        } == {
            "nv-config-manager-nautobot:local",
            "nv-config-manager-nats-ready:local",
            "nv-config-manager:local",
            "nv-config-manager-ui:local",
            "nv-config-manager-kea:local",
            "nv-config-manager-kea-admin:local",
            "nv-config-manager-temporal:local",
            "nv-config-manager-temporal-bootstrap:local",
            "nv-config-manager-temporal-ui:local",
        }
        assert [
            "docker",
            "version",
            "--format",
            "{{.Server.Os}}/{{.Server.Arch}}",
        ] in run_commands
        assert ["docker", "system", "prune", "-af"] not in logged_commands
        assert [
            "docker",
            "pull",
            "--platform",
            "linux/amd64",
            "docker.io/amd64/busybox:1.36",
        ] in logged_commands
        assert [
            "docker",
            "tag",
            "sha256:busyboxid",
            LOADER_POD_IMAGE,
        ] in logged_commands
        assert ["kind", "get", "nodes", "--name", "test-cluster"] in run_commands
        assert (
            ["docker", "save", "--platform", "linux/amd64", LOADER_POD_IMAGE],
            [
                "docker",
                "exec",
                "--privileged",
                "-i",
                "test-cluster-control-plane",
                "ctr",
                "--namespace=k8s.io",
                "images",
                "import",
                "--platform",
                "linux/amd64",
                "--snapshotter=overlayfs",
                "-",
            ],
        ) in pipe_commands

    def test_load_kind_loads_configured_preload_images(self, monkeypatch):
        run_commands: list[list[str]] = []
        logged_commands: list[list[str]] = []
        pipe_commands: list[tuple[list[str], list[str]]] = []

        def fake_run(cmd, **kwargs):
            run_commands.append(cmd)
            if cmd[:3] == ["docker", "version", "--format"]:
                return MagicMock(returncode=0, stdout="linux/amd64\n", stderr="")
            if cmd[:4] == ["docker", "image", "inspect", "--format"]:
                return MagicMock(returncode=0, stdout="sha256:redisid\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged_pipe(source_cmd, sink_cmd, step, callback, **kwargs):
            pipe_commands.append((source_cmd, sink_cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.delenv("NVCM_KIND_PRELOAD_IMAGES", raising=False)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run", fake_run)
        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_pipe",
            fake_run_logged_pipe,
        )

        monkeypatch.setenv("CI", "true")
        config = _make_config()
        config.images.kind_preload_images = ["docker.io/library/redis:7-alpine"]
        deployer = Deployer(
            config,
            DeployOptions(load_kind=True, kind_cluster="test-cluster"),
            RecordingCallback(),
        )
        deployer._load_kind()

        assert ["docker", "system", "prune", "-af"] in logged_commands
        assert [
            "docker",
            "pull",
            "--platform",
            "linux/amd64",
            "docker.io/library/redis:7-alpine",
        ] in logged_commands
        assert [
            "docker",
            "tag",
            "sha256:redisid",
            "docker.io/library/redis:7-alpine",
        ] in logged_commands
        assert (
            [
                "docker",
                "save",
                "--platform",
                "linux/amd64",
                "docker.io/library/redis:7-alpine",
            ],
            [
                "docker",
                "exec",
                "--privileged",
                "-i",
                "test-cluster-control-plane",
                "ctr",
                "--namespace=k8s.io",
                "images",
                "import",
                "--platform",
                "linux/amd64",
                "--snapshotter=overlayfs",
                "-",
            ],
        ) in pipe_commands


class TestHelmInstall:
    def test_kind_deploy_does_not_enable_helm_debug_without_flag(self, monkeypatch, tmp_path):
        logged_commands: list[list[str]] = []

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        config = _make_config()
        config.cluster.airgapped = True
        deployer = Deployer(
            config,
            DeployOptions(load_kind=True, chart_dir="deploy/helm"),
            RecordingCallback(),
        )
        deployer._values_file = tmp_path / "values-generated.yaml"
        deployer._values_file.write_text("global: {}\n")

        deployer._helm_install()

        helm_cmd = next(
            cmd for cmd in logged_commands if cmd[:3] == ["helm", "upgrade", "--install"]
        )
        assert "--debug" not in helm_cmd

    def test_helm_debug_can_be_enabled_without_kind(self, monkeypatch, tmp_path):
        logged_commands: list[list[str]] = []

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        config = _make_config()
        config.cluster.airgapped = True
        deployer = Deployer(
            config,
            DeployOptions(helm_debug=True, chart_dir="deploy/helm"),
            RecordingCallback(),
        )
        deployer._values_file = tmp_path / "values-generated.yaml"
        deployer._values_file.write_text("global: {}\n")

        deployer._helm_install()

        helm_cmd = next(
            cmd for cmd in logged_commands if cmd[:3] == ["helm", "upgrade", "--install"]
        )
        assert "--debug" in helm_cmd

    def test_helm_debug_stays_off_by_default(self, monkeypatch, tmp_path):
        logged_commands: list[list[str]] = []

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)

        config = _make_config()
        config.cluster.airgapped = True
        deployer = Deployer(config, DeployOptions(chart_dir="deploy/helm"), RecordingCallback())
        deployer._values_file = tmp_path / "values-generated.yaml"
        deployer._values_file.write_text("global: {}\n")

        deployer._helm_install()

        helm_cmd = next(
            cmd for cmd in logged_commands if cmd[:3] == ["helm", "upgrade", "--install"]
        )
        assert "--debug" not in helm_cmd

    def test_watch_pods_without_helm_debug_uses_plain_helm(self, monkeypatch, tmp_path):
        logged_commands: list[list[str]] = []
        watched_commands: list[tuple[list[str], str]] = []

        def fake_run_logged(cmd, step, callback, **kwargs):
            logged_commands.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged_with_pod_summary(cmd, k8s, namespace, step, callback, **kwargs):
            watched_commands.append((cmd, namespace))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_with_pod_summary",
            fake_run_logged_with_pod_summary,
        )

        config = _make_config()
        config.cluster.airgapped = True
        deployer = Deployer(
            config,
            DeployOptions(watch_pods=True, chart_dir="deploy/helm"),
            RecordingCallback(),
        )
        deployer._values_file = tmp_path / "values-generated.yaml"
        deployer._values_file.write_text("global: {}\n")

        deployer._helm_install()

        helm_cmd = next(
            cmd for cmd in logged_commands if cmd[:3] == ["helm", "upgrade", "--install"]
        )
        assert "--debug" not in helm_cmd
        assert watched_commands == []

    def test_helm_debug_summarizes_readiness_during_helm_install(self, monkeypatch, tmp_path):
        watched_commands: list[tuple[list[str], str]] = []

        def fake_run_logged(cmd, step, callback, **kwargs):
            return MagicMock(returncode=0, stdout="", stderr="")

        def fake_run_logged_with_pod_summary(cmd, k8s, namespace, step, callback, **kwargs):
            watched_commands.append((cmd, namespace))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("nv_config_manager_installer.deployer._run_logged", fake_run_logged)
        monkeypatch.setattr(
            "nv_config_manager_installer.deployer._run_logged_with_pod_summary",
            fake_run_logged_with_pod_summary,
        )

        config = _make_config()
        config.cluster.airgapped = True
        deployer = Deployer(
            config,
            DeployOptions(helm_debug=True, watch_pods=True, chart_dir="deploy/helm"),
            RecordingCallback(),
        )
        deployer._values_file = tmp_path / "values-generated.yaml"
        deployer._values_file.write_text("global: {}\n")

        deployer._helm_install()

        helm_cmd, namespace = watched_commands[0]
        assert helm_cmd[:3] == ["helm", "upgrade", "--install"]
        assert "--debug" in helm_cmd
        assert namespace == "nv-config-manager"

    def test_unready_pod_summary_includes_waiting_reasons(self):
        waiting_state = SimpleNamespace(
            waiting=SimpleNamespace(reason="ImagePullBackOff", message="pull access denied"),
            running=None,
            terminated=None,
        )
        running_state = SimpleNamespace(waiting=None, running=object(), terminated=None)
        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="api-123"),
            status=SimpleNamespace(
                phase="Pending",
                reason=None,
                message=None,
                conditions=[
                    SimpleNamespace(type="Ready", status="False", reason=None, message=None),
                    SimpleNamespace(
                        type="PodScheduled",
                        status="False",
                        reason="Unschedulable",
                        message="0/1 nodes are available",
                    ),
                ],
                init_container_statuses=[],
                container_statuses=[
                    SimpleNamespace(
                        name="api",
                        ready=False,
                        restart_count=2,
                        state=waiting_state,
                    ),
                    SimpleNamespace(
                        name="sidecar",
                        ready=False,
                        restart_count=0,
                        state=running_state,
                    ),
                ],
            ),
        )
        k8s = SimpleNamespace(
            v1=SimpleNamespace(list_namespaced_pod=lambda namespace: SimpleNamespace(items=[pod]))
        )

        lines = _unready_pod_summary_lines(k8s, "nv-config-manager")

        assert lines[0] == "1 pod(s) not ready"
        assert "api-123 ready=0/2 phase=Pending restarts=2" in lines[1]
        assert "PodScheduled=False (Unschedulable)" in lines[1]
        assert "container api: ImagePullBackOff (pull access denied)" in lines[1]

    def test_poll_pod_summary_skips_emit_when_stop_event_set_during_list(self):
        # list_namespaced_pod() can block for seconds; if shutdown sets
        # stop_event while it's in-flight, the poller must not emit one final
        # batch after _run_logged_with_pod_summary() has already returned.
        stop_event = threading.Event()

        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="api-1"),
            status=SimpleNamespace(
                phase="Pending",
                reason=None,
                message=None,
                conditions=[
                    SimpleNamespace(type="Ready", status="False", reason=None, message=None),
                ],
                init_container_statuses=[],
                container_statuses=[],
            ),
        )

        def slow_list(namespace):
            stop_event.set()
            return SimpleNamespace(items=[pod])

        k8s = SimpleNamespace(v1=SimpleNamespace(list_namespaced_pod=slow_list))
        step = DeployStep(id="test", label="test")
        callback = RecordingCallback()

        _poll_pod_summary(
            k8s,
            "ns",
            step,
            callback,
            stop_event,
            interval=0.01,
            heartbeat_interval=60.0,
        )

        assert not any(msg.startswith("[pods]") for msg in step.output)
        assert not any(msg.startswith("[pods]") for msg in callback.logs)


class TestContentHashing:
    def test_deterministic_hash(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d) / "my_job"
            job_dir.mkdir()
            (job_dir / "main.py").write_text("print('hello')")
            (job_dir / "util.py").write_text("x = 1")

            h1 = _hash_content_dir([job_dir])
            h2 = _hash_content_dir([job_dir])
            assert h1 == h2, "Same content should produce the same hash"

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d) / "my_job"
            job_dir.mkdir()
            (job_dir / "main.py").write_text("print('v1')")

            h1 = _hash_content_dir([job_dir])
            (job_dir / "main.py").write_text("print('v2')")
            h2 = _hash_content_dir([job_dir])
            assert h1 != h2, "Changed content should produce a different hash"

    def test_ignores_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d) / "my_job"
            job_dir.mkdir()
            (job_dir / "main.py").write_text("print('hello')")

            h1 = _hash_content_dir([job_dir])
            pycache = job_dir / "__pycache__"
            pycache.mkdir()
            (pycache / "main.cpython-313.pyc").write_bytes(b"\x00\x01\x02")
            h2 = _hash_content_dir([job_dir])
            assert h1 == h2, "Ignored files should not affect the hash"

    def test_empty_paths(self):
        h = _hash_content_dir([])
        assert isinstance(h, str) and len(h) == 64

    def test_missing_path_ignored(self):
        h = _hash_content_dir([Path("/nonexistent/path")])
        assert isinstance(h, str) and len(h) == 64


class TestRerunState:
    def test_defaults(self):
        state = _RerunState()
        assert state.is_rerun is False
        assert state.jobs_changed is True
        assert state.templates_changed is True

    def test_deployer_has_rerun_state(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        assert deployer._rerun.is_rerun is False
        assert deployer._rerun.jobs_changed is True


class TestConditionalRestart:
    """Verify _restart_nautobot and _restart_render_service skip logic."""

    def _make_deployer(self, *, jobs=True, templates=False) -> tuple:
        content_kwargs: dict = {}
        if jobs:
            content_kwargs["jobs"] = [JobPath(path="/fake/jobs")]
        if templates:
            content_kwargs["template_plugins"] = [TemplatePath(path="/fake/tpls")]
        config = _make_config()
        config = NVConfigManagerInstallConfig(
            cluster=config.cluster,
            secrets=config.secrets,
            sites=config.sites,
            content=ContentConfig(**content_kwargs),
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), cb)
        deployer._k8s = _mock_k8s()
        return deployer, cb

    def test_skip_restart_nautobot_on_fresh_install(self):
        deployer, cb = self._make_deployer(jobs=True)
        deployer._rerun = _RerunState(is_rerun=False, jobs_changed=True)
        deployer._restart_nautobot()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-nautobot") == StepStatus.SKIPPED

    def test_skip_restart_nautobot_when_jobs_unchanged(self):
        deployer, cb = self._make_deployer(jobs=True)
        deployer._rerun = _RerunState(is_rerun=True, jobs_changed=False)
        deployer._restart_nautobot()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-nautobot") == StepStatus.SKIPPED

    @patch("nv_config_manager_installer.deployer._run_logged")
    def test_restart_nautobot_on_rerun_with_changed_jobs(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")
        deployer, cb = self._make_deployer(jobs=True)
        deployer._rerun = _RerunState(is_rerun=True, jobs_changed=True)
        deployer._restart_nautobot()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-nautobot") == StepStatus.SUCCESS
        deployer._k8s.restart_deployment.assert_called()

    def test_skip_restart_render_on_fresh_install(self):
        deployer, cb = self._make_deployer(templates=True)
        deployer._rerun = _RerunState(is_rerun=False, templates_changed=True)
        deployer._restart_render_service()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-render") == StepStatus.SKIPPED

    def test_skip_restart_render_when_templates_unchanged(self):
        deployer, cb = self._make_deployer(templates=True)
        deployer._rerun = _RerunState(is_rerun=True, templates_changed=False)
        deployer._restart_render_service()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-render") == StepStatus.SKIPPED

    @patch("nv_config_manager_installer.deployer._run_logged")
    def test_restart_render_on_rerun_with_changed_templates(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")
        deployer, cb = self._make_deployer(templates=True)
        deployer._rerun = _RerunState(is_rerun=True, templates_changed=True)
        deployer._k8s.list_deployment_names.return_value = [
            "nv-config-manager-render-api",
            "nv-config-manager-render-consumer-nautobot",
        ]
        deployer._restart_render_service()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-render") == StepStatus.SUCCESS
        assert deployer._k8s.restart_deployment.call_count == 2

    def test_skip_restart_render_when_no_template_plugins(self):
        deployer, cb = self._make_deployer(templates=False)
        deployer._rerun = _RerunState(is_rerun=True, templates_changed=True)
        deployer._restart_render_service()
        statuses = dict(cb.step_updates)
        assert statuses.get("restart-render") == StepStatus.SKIPPED


class TestPvcContentUpload:
    """Verify PVC content uploads replace prior extracted content."""

    def test_jobs_upload_clears_existing_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        job_dir = tmp_path / "mock_topology"
        job_dir.mkdir()
        (job_dir / "__init__.py").write_text("")
        monkeypatch.chdir(Path(__file__).resolve().parents[2])

        config = _make_config()
        config.content = ContentConfig(
            jobs=[JobPath(path=str(job_dir))],
        )
        deployer = Deployer(config, DeployOptions(), RecordingCallback())
        deployer._k8s = _mock_k8s()
        deployer._rerun = _RerunState(is_rerun=True, jobs_changed=True)
        uploaded_members: list[str] = []

        def capture_tarball(source: str, *_args: object) -> None:
            with tarfile.open(source) as archive:
                uploaded_members.extend(archive.getnames())

        deployer._k8s.copy_to_pod.side_effect = capture_tarball

        deployer._setup_jobs_pvc()

        assert "__init__.py" in uploaded_members
        assert "mock_topology/__init__.py" in uploaded_members
        assert not any(name.startswith("nv_config_manager_jobs/") for name in uploaded_members)
        exec_command = deployer._k8s.exec_command.call_args.args[2]
        assert "find . -mindepth 1 -maxdepth 1 -exec rm -rf {} \\;" in exec_command[2]
        assert "tar xzf /tmp/jobs.tar.gz" in exec_command[2]

    def test_template_plugin_upload_clears_existing_content(self, tmp_path: Path):
        plugin_dir = tmp_path / "network_templates"
        plugin_dir.mkdir()
        (plugin_dir / "pyproject.toml").write_text('[project]\nname = "network-templates"\n')

        config = _make_config()
        config.content = ContentConfig(
            template_plugins=[TemplatePath(path=str(plugin_dir))],
        )
        deployer = Deployer(config, DeployOptions(), RecordingCallback())
        deployer._k8s = _mock_k8s()
        deployer._rerun = _RerunState(is_rerun=True, templates_changed=True)

        deployer._setup_templates_pvc()

        exec_command = deployer._k8s.exec_command.call_args.args[2]
        assert "find . -mindepth 1 -maxdepth 1 -exec rm -rf {} \\;" in exec_command[2]
        assert "tar xzf /tmp/plugins.tar.gz" in exec_command[2]


class TestK8sClientIntegration:
    """Verify the deployer interacts with K8sClient correctly."""

    def test_check_prerequisites_initializes_k8s(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        assert deployer._k8s is None

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_namespace_creation(self, mock_which, mock_k8s_cls, mock_run, mock_run_logged):
        mock_k8s = _mock_k8s()
        mock_k8s.ensure_namespace.return_value = True
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        mock_k8s.ensure_namespace.assert_called_with("nv-config-manager")
        statuses = dict(cb.step_updates)
        assert statuses.get("create-namespace") == StepStatus.SUCCESS

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_secret_creation_uses_k8s_client(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        mock_k8s.apply_secret.assert_called()
        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert "redis-password" in secret_names
        assert "nautobot-token" in secret_names
        assert "nautobot-admin" in secret_names
        nautobot_token_call = next(
            call
            for call in mock_k8s.apply_secret.call_args_list
            if call.args[0] == "nautobot-token"
        )
        assert "read-only-token" not in nautobot_token_call.args[2]

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_external_dcim_creates_the_configured_token_secret(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = _make_config()
        config.dcim = DCIMConfig(
            provider="synthetic",
            server="https://synthetic.example",
            token_secret_name="synthetic-dcim-token",
            token_secret_key="access-token",
        )
        config.services = ServicesConfig(nautobot=False)

        Deployer(config, DeployOptions(dry_run=True), RecordingCallback()).run()

        token_call = next(
            call
            for call in mock_k8s.apply_secret.call_args_list
            if call.args[0] == "synthetic-dcim-token"
        )
        assert token_call.args[2]["access-token"]
        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert "nautobot-admin" not in secret_names
        assert {"nats-sys", "nats-nv-config-manager", "nats-nautobot"}.issubset(secret_names)

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_create_secrets_includes_configured_nautobot_read_only_token(
        self,
        mock_which,
        mock_k8s_class,
        mock_run,
        mock_run_logged,
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_class.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.secrets = SecretsConfig(
            method=SecretsMethod.KUBERNETES,
            k8s=KubernetesSecretsConfig(
                nautobot=K8sSecretGroup(values={"readOnlyToken": "ro-token"}),
            ),
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        nautobot_token_call = next(
            call
            for call in mock_k8s.apply_secret.call_args_list
            if call.args[0] == "nautobot-token"
        )
        assert nautobot_token_call.args[2]["read-only-token"] == "ro-token"

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_create_secrets_includes_ztp_s3_credentials(
        self,
        mock_which,
        mock_k8s_class,
        mock_run,
        mock_run_logged,
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_class.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.infrastructure = InfrastructureConfig(
            ztp_storage=ZTPStorageConfig(type=ZTPStorageType.S3)
        )
        config.secrets = SecretsConfig(
            method=SecretsMethod.KUBERNETES,
            k8s=KubernetesSecretsConfig(
                ztp_s3=K8sSecretGroup(
                    enabled=True,
                    values={
                        "endpoint": "https://minio.example",
                        "accessKeyId": "access",
                        "secretAccessKey": "secret",
                    },
                )
            ),
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        ztp_s3_call = next(
            call
            for call in mock_k8s.apply_secret.call_args_list
            if call.args[0] == "ztp-s3-credentials"
        )
        assert ztp_s3_call.args[2] == {
            "CUSTOM_S3_ENDPOINT": "https://minio.example",
            "CUSTOM_S3_ACCESS_KEY": "access",
            "CUSTOM_S3_SECRET_KEY": "secret",
        }

    def test_api_token_retrieval(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        deployer._k8s = _mock_k8s()
        deployer._k8s.read_secret_data.return_value = {"api_token": "test-token-123"}

        token = NautobotJobRunner(
            deployer._k8s, "nv-config-manager", "nv-config-manager"
        )._get_api_token()
        assert token == "test-token-123"
        deployer._k8s.read_secret_data.assert_called_with("nautobot-admin", "nv-config-manager")

    def test_api_token_fallback_to_nautobot_token(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        deployer._k8s = _mock_k8s()
        deployer._k8s.read_secret_data.side_effect = [
            {},
            {"token": "fallback-token"},
        ]

        token = NautobotJobRunner(
            deployer._k8s, "nv-config-manager", "nv-config-manager"
        )._get_api_token()
        assert token == "fallback-token"

    def test_network_secret_updates_stale_existing_content(self):
        config = _make_config()
        config.cluster.release_name = "nv-config-manager"
        config.sites = [SiteConfig(name="test-site")]
        config.network_secrets = [
            NetworkSecretEntry(name="Root Password", secret_key="root_password"),
        ]
        deployer = Deployer(config, DeployOptions())
        deployer._k8s = _mock_k8s()
        deployer._k8s.secret_exists.return_value = True
        deployer._k8s.read_secret_data.return_value = {
            "config-secrets.ini": "[site.dc01]\nroot_password_r1 = old-root\n"
        }
        step = DeployStep("create-secrets", "Create Kubernetes secrets")

        deployer._create_network_secrets(
            step,
            {"root_password_r1": "new-root", "hash_salt": "new-salt"},
        )

        deployer._k8s.apply_file_secret.assert_called_once()
        name, namespace, data = deployer._k8s.apply_file_secret.call_args.args
        assert name == "nv-config-manager-network-secrets"
        assert namespace == "nv-config-manager"
        rendered = data["config-secrets.ini"].decode()
        assert "[site.test-site]" in rendered
        assert "root_password_r1 = new-root" in rendered
        assert "Updated: nv-config-manager-network-secrets" in step.output

    def test_network_secret_skips_when_existing_content_matches(self):
        config = _make_config()
        config.cluster.release_name = "nv-config-manager"
        config.network_secrets = [
            NetworkSecretEntry(name="Root Password", secret_key="root_password"),
        ]
        existing = build_config_secrets_ini(
            config,
            {"root_password_r1": "existing-root", "hash_salt": "existing-salt"},
        )
        deployer = Deployer(config, DeployOptions())
        deployer._k8s = _mock_k8s()
        deployer._k8s.secret_exists.return_value = True
        deployer._k8s.read_secret_data.return_value = {"config-secrets.ini": existing}
        step = DeployStep("create-secrets", "Create Kubernetes secrets")

        deployer._create_network_secrets(
            step,
            {"root_password_r1": "new-root", "hash_salt": "new-salt"},
        )

        deployer._k8s.apply_file_secret.assert_not_called()
        assert "Secret exists, skipping: nv-config-manager-network-secrets" in step.output

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_pull_secret_from_config_images(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.images = ImagesConfig(
            source=ImageSource.REGISTRY,
            pull_secret=ImagePullSecret(
                name="my-reg-cred",
                server="registry.corp.com",
                username="robot$ci",
                password="my-key",
            ),
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        mock_k8s.apply_docker_registry_secret.assert_called_once_with(
            "my-reg-cred",
            "nv-config-manager",
            server="registry.corp.com",
            username="robot$ci",
            password="my-key",
        )

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_no_pull_secret_when_local_source(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.images = ImagesConfig(
            source=ImageSource.LOCAL,
            pull_secret=ImagePullSecret(password="my-key"),
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        mock_k8s.apply_docker_registry_secret.assert_not_called()

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_git_token_secrets_created(self, mock_which, mock_k8s_cls, mock_run, mock_run_logged):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.git_tokens = [
            GitTokenEntry(name="prismo", token="ghp_abc123", username="bot-user"),
            GitTokenEntry(name="gitlab", token="glpat-xyz"),
        ]
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert "git-token-prismo" in secret_names
        assert "git-token-gitlab" in secret_names

        prismo_call = next(
            c for c in mock_k8s.apply_secret.call_args_list if c.args[0] == "git-token-prismo"
        )
        assert prismo_call.args[2] == {"token": "ghp_abc123", "username": "bot-user"}

        gitlab_call = next(
            c for c in mock_k8s.apply_secret.call_args_list if c.args[0] == "git-token-gitlab"
        )
        assert gitlab_call.args[2] == {"token": "glpat-xyz"}

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_no_git_token_secrets_when_empty(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert not any(n.startswith("git-token-") for n in secret_names)

    def test_jobs_pvc_uses_storage_config_and_node_selector(self):
        config = _make_config()
        config.content = ContentConfig(
            jobs=[JobPath(path="/fake/jobs")],
            jobs_config=JobsConfig(
                storage_class="local-path",
                access_mode="ReadWriteOnce",
                node_selector={"kubernetes.io/hostname": "worker-1"},
            ),
        )
        callback = RecordingCallback()
        deployer = Deployer(config, DeployOptions(), callback)
        deployer._k8s = _mock_k8s()

        deployer._setup_jobs_pvc()

        deployer._k8s.ensure_pvc.assert_called_once_with(
            "nautobot-custom-jobs",
            "nv-config-manager",
            access_mode="ReadWriteOnce",
            storage_class="local-path",
            allow_recreate=False,
        )
        deployer._k8s.create_loader_pod.assert_called_once_with(
            "nv-config-manager-jobs-loader",
            "nv-config-manager",
            "nautobot-custom-jobs",
            mount_path="/jobs",
            node_selector={"kubernetes.io/hostname": "worker-1"},
        )

    def test_jobs_pvc_is_skipped_without_custom_jobs(self):
        callback = RecordingCallback()
        deployer = Deployer(_make_config(), DeployOptions(), callback)
        deployer._k8s = _mock_k8s()

        deployer._setup_jobs_pvc()

        assert dict(callback.step_updates)["setup-jobs-pvc"] == StepStatus.SKIPPED
        deployer._k8s.ensure_pvc.assert_not_called()


class TestContentAddressedTags:
    def test_deployer_initializes_empty_tags(self):
        config = _make_config()
        deployer = Deployer(config, DeployOptions())
        assert deployer._local_image_tags == {}

    @patch("nv_config_manager_installer.deployer.subprocess.run")
    def test_get_image_digest_tag(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="sha256:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6\n"
        )
        tag = _get_image_digest_tag("nv-config-manager:local")
        assert tag == "sha-a1b2c3d4e5f6"
        mock_run.assert_called_once()

    @patch("nv_config_manager_installer.deployer.subprocess.run")
    def test_get_image_digest_tag_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker")
        tag = _get_image_digest_tag("nv-config-manager:local")
        assert tag == ""

    @patch("nv_config_manager_installer.deployer.subprocess.run")
    def test_get_image_digest_tag_no_docker(self, mock_run):
        mock_run.side_effect = FileNotFoundError("docker not found")
        tag = _get_image_digest_tag("nv-config-manager:local")
        assert tag == ""

    @patch("nv_config_manager_installer.deployer.subprocess.run")
    def test_get_image_digest_tag_empty_id(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="\n")
        tag = _get_image_digest_tag("nv-config-manager:local")
        assert tag == ""


class TestRedfishSecrets:
    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_redfish_creds_created_when_enabled(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.redfish = RedfishConfig(
            enabled=True,
            vendors={
                "lenovo": RedfishVendorCreds(default_user="admin"),
                "bluefield": RedfishVendorCreds(),
            },
        )
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert "redfish-creds" in secret_names
        redfish_call = next(
            c for c in mock_k8s.apply_secret.call_args_list if c.args[0] == "redfish-creds"
        )
        data = redfish_call.args[2]
        assert "lenovo-default-user" in data
        assert "bluefield-default-user" in data

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_no_redfish_creds_when_disabled(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert "redfish-creds" not in secret_names

    @patch("nv_config_manager_installer.deployer._run_logged")
    @patch("nv_config_manager_installer.deployer._run")
    @patch("nv_config_manager_installer.deployer.K8sClient")
    @patch("nv_config_manager_installer.deployer.shutil.which", return_value="/usr/bin/kubectl")
    def test_device_creds_skipped_when_temporal_disabled(
        self, mock_which, mock_k8s_cls, mock_run, mock_run_logged
    ):
        mock_k8s = _mock_k8s()
        mock_k8s_cls.return_value = mock_k8s
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_run_logged.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = _make_config()
        config.services = ServicesConfig(temporal=False)
        cb = RecordingCallback()
        deployer = Deployer(config, DeployOptions(dry_run=True), cb)
        deployer.run()

        secret_names = [call.args[0] for call in mock_k8s.apply_secret.call_args_list]
        assert not any("device-creds" in n for n in secret_names)
