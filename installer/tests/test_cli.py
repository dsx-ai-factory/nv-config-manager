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
"""Tests for nv_config_manager_installer.cli -- Click command validation."""

from __future__ import annotations

import tempfile
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from nv_config_manager_installer.cli import _collect_validation_errors, _run_pvc_updater, main
from nv_config_manager_installer.schema import (
    ClusterConfig,
    ContentConfig,
    NVConfigManagerInstallConfig,
    ServicesConfig,
    SiteConfig,
)


class TestValidateCommand:
    def test_validate_valid_config(self):
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.example.com"),
            sites=[SiteConfig(name="dc01")],
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = Path(f.name)
            config.to_yaml(path)

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["validate", str(path)])
            assert result.exit_code == 0
            assert "valid" in result.output.lower()
        finally:
            path.unlink(missing_ok=True)

    def test_validate_missing_hostname(self):
        config = NVConfigManagerInstallConfig(
            sites=[SiteConfig(name="dc01")],
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = Path(f.name)
            config.to_yaml(path)

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["validate", str(path)])
            assert result.exit_code != 0
            assert "hostname" in result.output.lower()
        finally:
            path.unlink(missing_ok=True)

    def test_validate_missing_sites(self):
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.example.com"),
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            path = Path(f.name)
            config.to_yaml(path)

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["validate", str(path)])
            assert result.exit_code != 0
            assert "site" in result.output.lower()
        finally:
            path.unlink(missing_ok=True)

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"], prog_name="nv-config-manager-installer")
        assert result.exit_code == 0
        assert "nv-config-manager-installer" in result.output

    def test_short_alias_script_is_registered(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        scripts = data["project"]["scripts"]
        assert scripts["nvcm-installer"] == scripts["nv-config-manager-installer"]

    def test_generate_values_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["generate-values", "--help"])
        assert result.exit_code == 0
        assert "--chart-dir" in result.output

    def test_air_sim_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["air-sim", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "deploy" in result.output

    def test_air_sim_deploy_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["air-sim", "deploy", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output

    def test_external_nautobot_rejects_post_deploy_jobs(self):
        config = NVConfigManagerInstallConfig()
        config.services = ServicesConfig(
            nautobot=False,
            external_nautobot_url="https://nb.example.com",
        )
        config.content = ContentConfig(run_after_deploy=[{"job": "jobs.bootstrap.SiteBootstrap"}])

        errors = _collect_validation_errors(config)

        assert any("post-deploy jobs require a local Nautobot" in error for error in errors)

    @pytest.mark.parametrize(
        "field",
        [
            "queue",
            "device_consumer",
            "archive_consumer",
            "archive_delivery",
            "nautobot_consumer",
        ],
    )
    def test_nats_placeholders_fail_readiness(self, field: str) -> None:
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.example.com"),
            sites=[SiteConfig(name="dc01")],
        )
        nats = config.external_services.nats
        if field == "queue":
            nats.queue = "replace-me-queue"
        elif field == "device_consumer":
            nats.streams.config_manager.consumer.name = "replace-me-device"
        elif field == "archive_consumer":
            nats.streams.config_manager.archive_consumer.name = "replace-me-archive"
        elif field == "archive_delivery":
            nats.streams.config_manager.archive_consumer.delivery_subject = (
                "replace-me.archive.delivery"
            )
        else:
            nats.streams.nautobot.consumer.name = "replace-me-nautobot"

        errors = _collect_validation_errors(config)

        assert any("replace-me" in error and "NATS" in error for error in errors)

    def test_external_nats_placeholder_server_fails_readiness(self) -> None:
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.example.com"),
            sites=[SiteConfig(name="dc01")],
            external_services={
                "nats": {
                    "enabled": True,
                    "server": "tls://replace-me.invalid:4222",
                }
            },
        )

        errors = _collect_validation_errors(config)

        assert any("replace-me.invalid" in error for error in errors)

    def test_completed_nats_topology_passes_readiness(self) -> None:
        config = NVConfigManagerInstallConfig(
            cluster=ClusterConfig(hostname="test.example.com"),
            sites=[SiteConfig(name="dc01")],
            external_services={
                "nats": {
                    "enabled": True,
                    "server": "tls://nats.example.com:4222",
                    "streams": {
                        "config_manager": {
                            "consumer": {"name": "cell-device"},
                            "archive_consumer": {
                                "name": "cell-archive",
                                "delivery_subject": "cell.archive.delivery",
                            },
                        },
                        "nautobot": {"consumer": {"name": "cell-nautobot"}},
                    },
                }
            },
        )

        errors = _collect_validation_errors(config)

        assert not any("NATS" in error for error in errors)


class TestInitCommand:
    def test_init_rejects_unsupported_ztp_platform(self, tmp_path: Path):
        config_path = tmp_path / "installer.yaml"
        config_path.write_text(
            """infrastructure:
  ztp_storage:
    os_images:
      - platform: sonic
        version: 4.0.0
        path: /images/sonic.bin
"""
        )

        result = CliRunner().invoke(main, ["init", "--config", str(config_path)])

        assert result.exit_code != 0
        assert "Unsupported ZTP platform 'sonic'" in result.output


class TestDeployCommand:
    def test_deploy_missing_config(self):
        runner = CliRunner()
        result = runner.invoke(main, ["deploy", "/nonexistent/config.yaml"])
        assert result.exit_code != 0

    def test_deploy_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["deploy", "--help"])
        assert result.exit_code == 0
        assert "--chart-dir" in result.output
        assert "--build-images" in result.output
        assert "--load-kind" in result.output
        assert "--dry-run" in result.output
        assert "--helm-timeout" in result.output
        assert "--helm-debug" in result.output
        assert "--watch-pods" in result.output
        assert "--no-watch-pods" in result.output
        assert "--vault-token-file" in result.output
        assert "--skip-vault-population" in result.output


class TestPVCUpdaterCommand:
    def test_pvc_updater_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["pvc-updater", "--help"])
        assert result.exit_code == 0
        assert "jobs" in result.output
        assert "templates" in result.output
        assert "ztp" in result.output

    def test_pvc_updater_ztp_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["pvc-updater", "ztp", "--help"])
        assert result.exit_code == 0
        assert "--image" in result.output
        assert "--kubeconfig" in result.output
        assert "KUBECONFIG" in result.output
        assert "--namespace" in result.output
        assert "--release-name" not in result.output
        assert "--rollout-timeout" not in result.output

    @patch("nv_config_manager_installer.cli._run_pvc_updater")
    def test_pvc_updater_passes_explicit_kubeconfig(
        self,
        mock_run_pvc_updater,
        tmp_path: Path,
    ):
        source = tmp_path / "templates"
        source.mkdir()
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text("apiVersion: v1\n")
        runner = CliRunner()

        result = runner.invoke(
            main,
            [
                "pvc-updater",
                "templates",
                "--source",
                str(source),
                "--namespace",
                "nv-config-manager",
                "--release-name",
                "nv-config-manager",
                "--kubeconfig",
                str(kubeconfig),
            ],
        )

        assert result.exit_code == 0
        assert mock_run_pvc_updater.call_args.kwargs["kubeconfig"] == kubeconfig

    def test_pvc_updater_jobs_help_lists_job_execution_options(self):
        runner = CliRunner()
        result = runner.invoke(main, ["pvc-updater", "jobs", "--help"])
        assert result.exit_code == 0
        assert "--run-job" in result.output
        assert "--job-input" in result.output

    def test_pvc_updater_jobs_rejects_non_object_job_input(self, tmp_path: Path):
        source = tmp_path / "jobs"
        source.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "pvc-updater",
                "jobs",
                "--source",
                str(source),
                "--run-job",
                "custom_jobs.bootstrap.SiteBootstrap",
                "--job-input",
                "[]",
                "--namespace",
                "nv-config-manager",
                "--release-name",
                "nv-config-manager",
            ],
        )
        assert result.exit_code != 0
        assert "must be a JSON object" in result.output

    @patch("nv_config_manager_installer.cli._run_pvc_updater")
    def test_pvc_updater_jobs_passes_requested_job_to_updater(
        self,
        mock_run_pvc_updater,
        tmp_path: Path,
    ):
        source = tmp_path / "jobs"
        source.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "pvc-updater",
                "jobs",
                "--source",
                str(source),
                "--run-job",
                "custom_jobs.bootstrap.SiteBootstrap",
                "--job-input",
                '{"site":"site-1"}',
                "--namespace",
                "nv-config-manager",
                "--release-name",
                "nv-config-manager",
            ],
        )
        assert result.exit_code == 0
        after_update = mock_run_pvc_updater.call_args.kwargs["after_update"]
        updater = MagicMock()
        updater.run_nautobot_job.return_value = True
        assert after_update(updater) is True
        updater.run_nautobot_job.assert_called_once_with(
            "custom_jobs.bootstrap.SiteBootstrap",
            {"site": "site-1"},
            timeout=1_800,
        )

    @patch("nv_config_manager_installer.cli._run_pvc_updater")
    def test_pvc_updater_runs_existing_job_without_content_source(
        self,
        mock_run_pvc_updater,
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "pvc-updater",
                "jobs",
                "--run-job",
                "nv_config_manager_jobs.bootstrap.load_bootstrap_data.LoadBootstrapData",
                "--job-input",
                "{}",
                "--namespace",
                "nv-config-manager",
                "--release-name",
                "nv-config-manager",
            ],
        )

        assert result.exit_code == 0
        assert mock_run_pvc_updater.call_args.kwargs["update"] is None
        after_update = mock_run_pvc_updater.call_args.kwargs["after_update"]
        updater = MagicMock()
        updater.run_nautobot_job.return_value = True
        assert after_update(updater) is True
        updater.run_nautobot_job.assert_called_once_with(
            "nv_config_manager_jobs.bootstrap.load_bootstrap_data.LoadBootstrapData",
            {},
            timeout=1_800,
        )

    def test_pvc_updater_jobs_requires_content_source_or_job(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "pvc-updater",
                "jobs",
                "--namespace",
                "nv-config-manager",
                "--release-name",
                "nv-config-manager",
            ],
        )

        assert result.exit_code != 0
        assert "--source or --run-job" in result.output

    @patch(
        "nv_config_manager_installer.cli.K8sClient",
        side_effect=RuntimeError("Unable to load kubeconfig"),
    )
    def test_pvc_updater_wraps_kubernetes_client_initialization(self, _mock_k8s):
        with pytest.raises(click.ClickException, match="Unable to load kubeconfig"):
            _run_pvc_updater(
                namespace="nv-config-manager",
                release_name="nv-config-manager",
                rollout_timeout=60,
                update=lambda _updater: False,
            )

    @patch("nv_config_manager_installer.cli.K8sClient")
    def test_pvc_updater_wraps_connectivity_failure(self, mock_k8s):
        mock_k8s.return_value.check_connectivity.return_value = False

        with pytest.raises(click.ClickException, match="Unable to connect"):
            _run_pvc_updater(
                namespace="nv-config-manager",
                release_name="nv-config-manager",
                rollout_timeout=60,
                update=lambda _updater: False,
            )

    @patch("nv_config_manager_installer.cli.K8sClient")
    def test_pvc_updater_initializes_client_with_kubeconfig(self, mock_k8s, tmp_path: Path):
        kubeconfig = tmp_path / "config"
        mock_k8s.return_value.check_connectivity.return_value = True

        _run_pvc_updater(
            namespace="nv-config-manager",
            release_name="nv-config-manager",
            rollout_timeout=60,
            update=lambda _updater: False,
            kubeconfig=kubeconfig,
        )

        mock_k8s.assert_called_once_with(kubeconfig=kubeconfig)
