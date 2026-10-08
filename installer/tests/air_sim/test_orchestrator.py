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

import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nv_config_manager_installer.air_sim.constants import DEFAULT_MOCK_TOPOLOGY_PATH
from nv_config_manager_installer.air_sim.orchestrator import (
    SimOrchestrator,
    StepStatus,
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


def test_resolve_topology_uses_installer_checkout_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Topology parsing must use the same checkout fallback as content staging."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    topology = checkout / "fabric.yaml"
    topology.write_text("devices: []\n")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr("nv_config_manager_installer.air_sim.orchestrator.PROJECT_ROOT", checkout)
    cfg = SimConfig(topology_path="fabric.yaml")
    orchestrator = SimOrchestrator(cfg, _Callback())

    assert orchestrator._resolve_topology_path(cfg) == str(topology)

    # An explicit path in the invoking directory takes precedence over bundled content.
    local_topology = workdir / "fabric.yaml"
    local_topology.write_text("devices: []\n")
    assert orchestrator._resolve_topology_path(cfg) == str(local_topology)


def test_resolve_topology_generates_from_mock_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_write_site_design_from_mock_context(
        blueprint: str, deployment_name: str, *, context_root: Path
    ) -> str:
        calls.append((blueprint, deployment_name, context_root))
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
    assert calls == [("air_trial", "demo", Path(cfg.mock_topology_path) / "context")]


def test_resolve_topology_generation_is_independent_from_dcim_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nv_config_manager_installer.air_sim.orchestrator.write_site_design_from_mock_context",
        lambda blueprint, deployment_name, **_kwargs: f"/tmp/{blueprint}-{deployment_name}.yaml",
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


def test_local_repository_path_accepts_checkout_and_file_url(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    assert SimOrchestrator._local_repository_path(str(checkout)) == checkout.resolve()
    assert SimOrchestrator._local_repository_path(checkout.as_uri()) == checkout.resolve()
    assert SimOrchestrator._local_repository_path("https://example.com/repo.git") is None
    assert SimOrchestrator._is_local_repository_reference(str(checkout)) is True
    assert SimOrchestrator._is_local_repository_reference(checkout.as_uri()) is True
    assert SimOrchestrator._is_local_repository_reference("https://example.com/repo.git") is False


@pytest.mark.parametrize(
    "repo",
    [
        "deploy@gitlab.example.com:group/repo.git",
        "git@gitlab.example.com:group/repo.git",
        "gitlab.example.com:group/repo.git",
        "ssh://deploy@gitlab.example.com/group/repo.git",
        "https://example.com/group/repo.git",
    ],
)
def test_repository_helpers_recognize_ssh_and_https_remotes(repo: str) -> None:
    """Remote application repositories must reach cloud-init's clone path."""
    assert SimOrchestrator._local_repository_path(repo) is None
    assert SimOrchestrator._is_local_repository_reference(repo) is False


@pytest.mark.parametrize(
    "repo", ["checkout", "./checkout", "./checkout:local", "nested/repo:local"]
)
def test_repository_helpers_preserve_relative_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo: str,
) -> None:
    """An explicit local path can contain a colon after its directory separator."""
    monkeypatch.chdir(tmp_path)
    checkout = tmp_path / repo
    checkout.mkdir(parents=True)
    assert SimOrchestrator._local_repository_path(repo) == checkout.resolve()
    assert SimOrchestrator._is_local_repository_reference(repo) is True


def test_stage_local_sources_uploads_repo_and_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "source.py").write_text("# unpublished source\n")
    topology = tmp_path / "fabric.yaml"
    topology.write_text("devices: []\n")
    mock_topology = tmp_path / "custom_mock_content"
    (mock_topology / "context" / "demo").mkdir(parents=True)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    job = tmp_path / "myjobs"
    job.mkdir()
    (job / "__init__.py").write_text("")
    (job / "helper.py").write_text("VALUE = 42\n")
    (job / "jobs.py").write_text("from custom.myjobs.helper import VALUE\nclass Foo: pass\n")

    uploads: list[tuple[str, str, set[str]]] = []

    class Manager:
        def upload_to_server(self, host, port, local_path, remote_path):
            local = Path(local_path)
            contents = {str(path.relative_to(local)) for path in local.rglob("*")}
            uploads.append((local_path, remote_path, contents))
            if remote_path == "/home/nvcm/air-content":
                shutil.copytree(local / "jobs" / "myjobs", tmp_path / "custom" / "myjobs")
            return True

    cfg = SimConfig(
        config_manager_repo=str(checkout),
        topology_path=str(topology),
        mock_blueprint="custom",
        mock_topology_path=str(mock_topology),
        template_plugin_paths=[str(plugin)],
        extra_job_paths=[str(job)],
        extra_run_after_deploy=[{"job": "custom.myjobs.jobs.Foo"}],
    )
    orchestrator = SimOrchestrator(cfg, _Callback())

    staged = orchestrator._stage_local_sources(
        Manager(), "worker.example", 17117, cfg, str(topology)
    )

    assert uploads[0][1] == "/home/nvcm/nv-config-manager"
    assert uploads[0][2] == {"source.py"}
    assert uploads[1][1] == "/home/nvcm/air-content"
    assert "topologies/fabric.yaml" in uploads[1][2]
    assert "mock-topology/mock_topology" in uploads[1][2]
    assert "template-plugins/00-plugin" in uploads[1][2]
    assert "jobs/myjobs/jobs.py" in uploads[1][2]
    assert staged.topology_path == "/home/nvcm/air-content/topologies/fabric.yaml"
    assert staged.mock_topology_path == "/home/nvcm/air-content/mock-topology/mock_topology"
    assert staged.template_plugin_paths == ["/home/nvcm/air-content/template-plugins/00-plugin"]
    assert staged.extra_job_paths == ["/home/nvcm/air-content/jobs/myjobs"]
    assert staged.extra_run_after_deploy == [{"job": "custom.myjobs.jobs.Foo"}]
    (tmp_path / "custom" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        module = importlib.import_module("custom.myjobs.jobs")
        assert module.VALUE == 42
        assert module.Foo.__module__ == "custom.myjobs.jobs"
    finally:
        for name in list(sys.modules):
            if name == "custom" or name.startswith("custom.myjobs"):
                sys.modules.pop(name)


def test_repository_upload_preserves_worktree_and_excludes_ignored_files(tmp_path: Path) -> None:
    """Unpublished changes survive upload, while ignored local files and deletions do not."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / ".gitignore").write_text(".env\n*.local.yaml\nbuild-output/\n")
    (checkout / "tracked.py").write_text("original\n")
    (checkout / "deleted.py").write_text("deleted\n")
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    (checkout / "tracked.py").write_text("unpublished change\n")
    (checkout / "deleted.py").unlink()
    (checkout / "new source.py").write_text("new source\n")
    (checkout / ".env").write_text("PLACEHOLDER=example\n")
    (checkout / "install.local.yaml").write_text("placeholder: example\n")
    (checkout / "build-output").mkdir()
    (checkout / "build-output" / "artifact").write_text("build output\n")
    (checkout / "source-link").symlink_to("tracked.py")
    topology = tmp_path / "fabric.yaml"
    topology.write_text("devices: []\n")
    cfg = SimConfig(
        config_manager_repo=str(checkout),
        topology_path=str(topology),
        mock_blueprint="custom",
        run_mock_topology_job=False,
    )
    uploads = []

    def upload(host, port, local_path, remote_path):
        if remote_path == "/home/nvcm/nv-config-manager":
            staged = Path(local_path)
            uploads.append({str(p.relative_to(staged)) for p in staged.rglob("*")})
            assert (staged / "tracked.py").read_text() == "unpublished change\n"
            assert (staged / "source-link").is_symlink()
        return True

    manager = Mock(spec=AirSimulationManager)
    manager.upload_to_server.side_effect = upload
    SimOrchestrator(cfg, _Callback())._stage_local_sources(
        manager, "worker.example", 17117, cfg, str(topology)
    )
    assert uploads == [{".gitignore", "tracked.py", "new source.py", "source-link"}]


@pytest.mark.parametrize(
    "failure", ["missing-plugin", "missing-job", "job-file", "duplicate-job", "mock-collision"]
)
def test_content_validation_fails_before_air_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Invalid job and plugin inputs fail without creating an AIR client or simulation."""
    topology = tmp_path / "fabric.yaml"
    topology.write_text("devices: []\n")
    cfg = SimConfig(
        topology_path=str(topology), mock_blueprint="custom", run_mock_topology_job=False
    )
    if failure == "missing-plugin":
        cfg.template_plugin_paths = [str(tmp_path / "missing")]
    elif failure == "missing-job":
        cfg.extra_job_paths = [str(tmp_path / "missing")]
    elif failure == "job-file":
        job = tmp_path / "job.py"
        job.write_text("# job\n")
        cfg.extra_job_paths = [str(job)]
    elif failure == "duplicate-job":
        for parent in ("one", "two"):
            (tmp_path / parent / "myjobs").mkdir(parents=True)
        cfg.extra_job_paths = [str(tmp_path / parent / "myjobs") for parent in ("one", "two")]
    else:
        job = tmp_path / "mock_topology"
        job.mkdir()
        cfg.run_mock_topology_job = True
        cfg.mock_topology_path = str(tmp_path)
        cfg.extra_job_paths = [str(job)]
    orchestrator = SimOrchestrator(cfg, _Callback())
    factory = Mock()
    monkeypatch.setattr(orchestrator, "_create_simulation_manager", factory)
    with pytest.raises((FileNotFoundError, ValueError)):
        orchestrator._run_impl()
    factory.assert_not_called()


@pytest.mark.parametrize("wait_timeout", [0, -1])
def test_local_repository_cannot_skip_upload_with_zero_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_timeout: int
) -> None:
    """A local checkout cannot produce an apparently usable empty remote workspace."""
    cfg = SimConfig(config_manager_repo=str(tmp_path), wait_timeout=wait_timeout)
    orchestrator = SimOrchestrator(cfg, _Callback())
    factory = Mock()
    monkeypatch.setattr(orchestrator, "_create_simulation_manager", factory)
    with pytest.raises(ValueError, match="requires wait_timeout greater than zero"):
        orchestrator._run_impl()
    factory.assert_not_called()


def test_disabled_oob_server_fails_before_air_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled management server gets a direct configuration error before provisioning."""
    topology = tmp_path / "fabric.yaml"
    topology.write_text("devices:\n- name: oob-mgmt-server\n  _air:\n    enabled: false\n")
    cfg = SimConfig(
        topology_path=str(topology), run_mock_topology_job=False, mock_blueprint="custom"
    )
    orchestrator = SimOrchestrator(cfg, _Callback())
    factory = Mock()
    monkeypatch.setattr(orchestrator, "_create_simulation_manager", factory)
    with pytest.raises(ValueError, match="OOB server 'oob-mgmt-server' is disabled for AIR"):
        orchestrator._run_impl()
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("field", "missing_path"),
    [
        ("mock_topology_path", ""),
        ("mock_topology_path", str(DEFAULT_MOCK_TOPOLOGY_PATH)),
        ("template_plugin_paths", "missing/plugin"),
        ("template_plugin_paths", "/remote-only/plugin"),
        ("extra_job_paths", "missing/job.py"),
        ("extra_job_paths", "/remote-only/job.py"),
        ("topology_path", "/remote-only/fabric.yaml"),
    ],
)
def test_staging_rejects_content_missing_locally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    missing_path: str,
) -> None:
    topology = tmp_path / "fabric.yaml"
    topology.write_text("devices: []\n")
    cfg = SimConfig(
        topology_path=str(topology),
        mock_topology_path=str(tmp_path),
        mock_blueprint="custom",
        template_plugin_paths=[],
        extra_job_paths=[],
    )
    setattr(cfg, field, [missing_path] if field.endswith("_paths") else missing_path)
    resolved_missing = missing_path or str(DEFAULT_MOCK_TOPOLOGY_PATH)
    orchestrator = SimOrchestrator(cfg, _Callback())
    monkeypatch.setattr(
        orchestrator,
        "_local_content_path",
        lambda path: (
            None if path == resolved_missing else (topology if path == str(topology) else tmp_path)
        ),
    )
    manager = Mock(spec=AirSimulationManager)
    with pytest.raises(FileNotFoundError, match="remote-only paths are not supported") as exc:
        orchestrator._stage_local_sources(manager, "worker.example", 17117, cfg, cfg.topology_path)
    assert resolved_missing in str(exc.value)
    manager.upload_to_server.assert_not_called()


@pytest.mark.parametrize("error_type", [FileNotFoundError, RuntimeError])
def test_staging_failure_marks_upload_step_failed(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    """Missing content and failed transfers must not leave the UI step running."""
    cfg = SimConfig(topology_path="fabric.yaml", no_aggressive_dhcp=True)
    callback = Mock(spec=_Callback)
    orchestrator = SimOrchestrator(cfg, callback)
    manager = Mock(spec=AirSimulationManager)
    manager.create_ssh_service.return_value = ("worker.example", 17117)
    builder = Mock(
        devices={
            cfg.oob_server_name: SimpleNamespace(
                interface_macs={"eth1": "00:11:22:33:44:55"}, air_enabled=True
            )
        },
        lb_allowed_prefixes=[],
        relay_return_prefixes=[],
    )
    builder.cumulus_firmware_versions.return_value = []
    builder.build_topology.return_value = {"nodes": {}, "links": []}
    module = "nv_config_manager_installer.air_sim.orchestrator"
    monkeypatch.setattr(f"{module}.AirTopologyBuilder", Mock(return_value=builder))
    monkeypatch.setattr(
        f"{module}._resolve_oob_server_ips_from_topology",
        Mock(return_value=("192.0.2.2", "192.0.2.1")),
    )
    monkeypatch.setattr(f"{module}.generate_server_cloud_init", Mock(return_value="cloud-init"))
    monkeypatch.setattr(f"{module}.shutil.which", Mock(return_value="/usr/bin/sshpass"))
    monkeypatch.setattr(orchestrator, "_create_simulation_manager", Mock(return_value=manager))
    monkeypatch.setattr(orchestrator, "_local_content_sources", Mock(return_value=[]))
    error = error_type("staging failed")
    monkeypatch.setattr(orchestrator, "_stage_local_sources", Mock(side_effect=error))

    with pytest.raises(error_type) as exc:
        orchestrator._run_impl()

    assert exc.value is error
    upload_calls = [
        call.args for call in callback.on_step.call_args_list if call.args[0] == "upload-files"
    ]
    assert upload_calls == [
        ("upload-files", StepStatus.RUNNING, ""),
        ("upload-files", StepStatus.FAILED, "staging failed"),
    ]
    manager.run_deploy.assert_not_called()


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


def test_post_deploy_does_not_reset_inventory_only_switches() -> None:
    cfg = SimConfig()
    orchestrator = SimOrchestrator(cfg, _Callback())
    manager = Mock(spec=AirSimulationManager)
    builder = SimpleNamespace(
        relay_return_prefixes=[],
        devices={
            name: SimpleNamespace(name=name, platform="Cumulus Linux", air_enabled=enabled)
            for name, enabled in [("simulated", True), ("inventory-only", False)]
        },
    )

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

    manager.reset_cumulus_nodes.assert_called_once_with("simulation-id", ["simulated"])
    manager.wait_for_intended_configs.assert_called_once_with(
        "worker.example", 17117, expected_total=1
    )
