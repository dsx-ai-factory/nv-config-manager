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
"""NVIDIA Config Manager Installer CLI -- Click entry point.

Commands:
    init              Launch the TUI wizard (or load an existing config to edit)
    validate          Validate a nv-config-manager-install.yaml config file
    generate-values   Generate Helm values and config-secrets.ini
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import click

from nv_config_manager_installer import __version__
from nv_config_manager_installer.air_sim.cli import air_sim
from nv_config_manager_installer.deployer import (
    DeployCallback,
    Deployer,
    DeployOptions,
    DeployStep,
)
from nv_config_manager_installer.helm_values import generate_helm_values
from nv_config_manager_installer.k8s import K8sClient
from nv_config_manager_installer.pvc_updater import (
    JOBS_PVC_NAME,
    TEMPLATES_PVC_NAME,
    ZTP_PVC_NAME,
    PVCUpdater,
    ZTPImageSource,
)
from nv_config_manager_installer.schema import (
    ImageSource,
    NVConfigManagerInstallConfig,
)
from nv_config_manager_installer.secrets import generate_secrets
from nv_config_manager_installer.tui.app import NVConfigManagerInstallerApp


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """NVIDIA Config Manager Install Wizard."""


main.add_command(air_sim)


@main.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("nv-config-manager-install.yaml"),
    help="Path to nv-config-manager-install.yaml (created if it doesn't exist, pre-populated if it does).",
)
def init(config_path: Path) -> None:
    """Launch the interactive TUI wizard."""
    config = NVConfigManagerInstallConfig()
    if config_path.exists():
        click.echo(f"Loading existing config: {config_path}")
        try:
            config = NVConfigManagerInstallConfig.from_yaml(config_path)
        except Exception as exc:
            raise click.ClickException(f"Failed to load config: {exc}") from exc

    app = NVConfigManagerInstallerApp(config=config, config_path=config_path)
    app.run()


@main.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def validate(config_path: Path) -> None:
    """Validate a nv-config-manager-install.yaml config file."""
    try:
        config = NVConfigManagerInstallConfig.from_yaml(config_path)
    except Exception as exc:
        click.echo(f"ERROR: Failed to parse config: {exc}", err=True)
        sys.exit(1)

    errors = _collect_validation_errors(config)
    if errors:
        click.echo("Validation errors:", err=True)
        for e in errors:
            click.echo(f"  - {e}", err=True)
        sys.exit(1)

    click.echo(f"Config is valid: {config_path}")


def _collect_validation_errors(config: NVConfigManagerInstallConfig) -> list[str]:
    """Return a list of validation error messages for a NVConfigManagerInstallConfig."""
    errors: list[str] = []
    if not config.cluster.hostname:
        errors.append("cluster.hostname is required")
    if not config.sites:
        errors.append("At least one site is required")
    if config.sso.enabled and not config.sso.issuer_url:
        errors.append("sso.issuer_url is required when SSO is enabled")
    if not config.services.nautobot and config.content.requires_local_nautobot:
        errors.append(
            "Custom jobs and post-deploy jobs require a local Nautobot deployment "
            "(services.nautobot must be true, or remove content.jobs and "
            "content.run_after_deploy)"
        )
    return errors


@main.command("generate-values")
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    help="Directory to write generated files to.",
)
@click.option(
    "--local-images",
    is_flag=True,
    default=False,
    help="Generate values for locally-built images (repository:tag=local, pullPolicy=IfNotPresent).",
)
@click.option(
    "--chart-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Path to the Helm chart directory used for size profile overrides.",
)
def generate_values(
    config_path: Path,
    output_dir: Path,
    local_images: bool,
    chart_dir: Path | None,
) -> None:
    """Generate Helm values from config."""
    config = NVConfigManagerInstallConfig.from_yaml(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    secrets_state = generate_secrets(config)

    values_path = output_dir / "values-generated.yaml"
    generate_helm_values(
        config,
        secrets_state,
        values_path,
        local_images=local_images,
        chart_dir=chart_dir,
    )
    click.echo(f"  Helm values: {values_path}")

    click.echo("Done.")


@main.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Repository root containing deployment charts and local image build contexts.",
)
@click.option("--chart-dir", default="deploy/helm", help="Path to the Helm chart directory.")
@click.option(
    "--image-source",
    type=click.Choice(["local", "registry"]),
    default="local",
    help="Image source: 'local' to build locally, 'registry' to pull from the configured registry.",
)
@click.option("--ngc-api-key", default="", help="NGC API key for NVCR registry authentication.")
@click.option("--build-images", is_flag=True, help="Build local Docker images before deploying.")
@click.option("--load-kind", is_flag=True, help="Load images into a Kind cluster.")
@click.option("--kind-cluster", default="nv-config-manager", help="Kind cluster name.")
@click.option(
    "--install-envoy-gateway",
    is_flag=True,
    help="Install Envoy Gateway CRDs/operator (requires gateway=envoyGateway).",
)
@click.option("--install-cert-manager", is_flag=True, help="Install cert-manager.")
@click.option("--install-cnpg-operator", is_flag=True, help="Install CNPG operator.")
@click.option("--helm-timeout", default="15m", help="Helm install/upgrade timeout.")
@click.option("--helm-debug", is_flag=True, help="Enable Helm debug output during install/upgrade.")
@click.option(
    "--watch-pods/--no-watch-pods",
    default=True,
    help="Stream pod readiness summaries while Helm waits when --helm-debug is set.",
)
@click.option("--recreate-secrets", is_flag=True, help="Recreate existing K8s secrets.")
@click.option(
    "--vault-token-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="File containing the Vault provisioning token (ESO deployments).",
)
@click.option(
    "--populate-vault/--skip-vault-population",
    default=True,
    help="Populate missing ESO values, or use already provisioned Vault paths.",
)
@click.option("--dry-run", is_flag=True, help="Generate values only, skip helm install.")
def deploy(
    config_path: Path,
    project_root: Path | None,
    chart_dir: str,
    image_source: str,
    ngc_api_key: str,
    build_images: bool,
    load_kind: bool,
    kind_cluster: str,
    install_envoy_gateway: bool,
    install_cert_manager: bool,
    install_cnpg_operator: bool,
    helm_timeout: str,
    helm_debug: bool,
    watch_pods: bool,
    recreate_secrets: bool,
    vault_token_file: Path | None,
    populate_vault: bool,
    dry_run: bool,
) -> None:
    """Deploy NVIDIA Config Manager from a config file (headless, for CI/CD)."""
    config = NVConfigManagerInstallConfig.from_yaml(config_path)

    if image_source:
        config.images.source = ImageSource(image_source)
    if ngc_api_key:
        config.images.pull_secret.password = ngc_api_key

    options = DeployOptions(
        project_root=project_root,
        chart_dir=chart_dir,
        build_images=build_images,
        load_kind=load_kind,
        kind_cluster=kind_cluster,
        install_envoy_gateway=install_envoy_gateway,
        install_cert_manager=install_cert_manager,
        install_cnpg_operator=install_cnpg_operator,
        helm_timeout=helm_timeout,
        helm_debug=helm_debug,
        watch_pods=watch_pods,
        recreate_secrets=recreate_secrets,
        vault_token_file=vault_token_file,
        populate_vault=populate_vault,
        dry_run=dry_run,
    )

    _run_deployer(config, options, operation="Deployment")


@main.group("pvc-updater")
def pvc_updater_command() -> None:
    """Populate GitOps-managed NVCM content PVCs and reload consumers as needed."""


def _run_pvc_updater(
    *,
    namespace: str,
    release_name: str = "",
    rollout_timeout: int = 600,
    update: Callable[[PVCUpdater], bool] | None,
    after_update: Callable[[PVCUpdater], bool] | None = None,
    kubeconfig: Path | None = None,
) -> None:
    """Run one PVC content update with a connected Kubernetes client."""
    try:
        k8s = K8sClient(kubeconfig=kubeconfig)
        if not k8s.check_connectivity():
            raise RuntimeError("Unable to connect to the current Kubernetes cluster")
        updater = PVCUpdater(
            k8s,
            namespace,
            release_name,
            rollout_timeout=rollout_timeout,
            on_log=click.echo,
        )
        changed = update(updater) if update is not None else False
        job_ran = False
        if after_update is not None:
            if not after_update(updater):
                raise RuntimeError("Nautobot job did not complete successfully")
            job_ran = True
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("PVC content updated successfully." if changed else "PVC content unchanged.")
    if job_ran:
        click.echo("Nautobot job completed successfully.")


def _pvc_cluster_options(command: Callable[..., None]) -> Callable[..., None]:
    """Apply cluster connection options shared by PVC updater subcommands."""
    command = click.option(
        "--kubeconfig",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Kubeconfig file to use. Defaults to KUBECONFIG, then the standard kubeconfig.",
    )(command)
    command = click.option(
        "--namespace",
        required=True,
        help="Namespace containing the NVCM release and its PVCs.",
    )(command)
    return command


def _pvc_common_options(command: Callable[..., None]) -> Callable[..., None]:
    """Apply options shared by PVC updates that restart consumers."""
    command = _pvc_cluster_options(command)
    command = click.option(
        "--release-name",
        required=True,
        help="Helm release name of NVCM (used to target the workloads to restart).",
    )(command)
    return click.option(
        "--rollout-timeout",
        type=click.IntRange(min=1),
        default=600,
        show_default=True,
        help="Seconds to wait for each restarted Deployment rollout.",
    )(command)


@pvc_updater_command.command("jobs")
@click.option(
    "--source",
    "sources",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    help="Custom-job directory or tar archive. May be supplied more than once.",
)
@click.option("--pvc-name", default=JOBS_PVC_NAME, show_default=True)
@click.option(
    "--run-job",
    metavar="MODULE.CLASS",
    help="Run this Nautobot job after any requested PVC update and worker rollouts.",
)
@click.option(
    "--job-input",
    default="{}",
    show_default=True,
    help="JSON object supplied as the Nautobot job input. Requires --run-job.",
)
@click.option(
    "--job-timeout",
    type=click.IntRange(min=1),
    default=1_800,
    show_default=True,
    help="Seconds to wait for the requested Nautobot job to complete.",
)
@_pvc_common_options
def pvc_updater_jobs(
    sources: tuple[Path, ...],
    pvc_name: str,
    run_job: str | None,
    job_input: str,
    job_timeout: int,
    namespace: str,
    release_name: str,
    rollout_timeout: int,
    kubeconfig: Path | None,
) -> None:
    """Update custom Nautobot jobs and optionally run one."""
    try:
        parsed_job_input = json.loads(job_input)
    except json.JSONDecodeError as exc:
        raise click.UsageError("--job-input must be valid JSON") from exc
    if not isinstance(parsed_job_input, dict):
        raise click.UsageError("--job-input must be a JSON object")
    if parsed_job_input and not run_job:
        raise click.UsageError("--job-input requires --run-job")
    if not sources and not run_job:
        raise click.UsageError("Provide at least one --source or --run-job")
    _run_pvc_updater(
        namespace=namespace,
        release_name=release_name,
        rollout_timeout=rollout_timeout,
        kubeconfig=kubeconfig,
        update=(lambda updater: updater.update_jobs(sources, pvc_name=pvc_name))
        if sources
        else None,
        after_update=(
            lambda updater: updater.run_nautobot_job(
                run_job,
                parsed_job_input,
                timeout=job_timeout,
            )
        )
        if run_job
        else None,
    )


@pvc_updater_command.command("templates")
@click.option(
    "--source",
    "sources",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    required=True,
    help="Template-plugin directory or tar archive. May be supplied more than once.",
)
@click.option("--pvc-name", default=TEMPLATES_PVC_NAME, show_default=True)
@_pvc_common_options
def pvc_updater_templates(
    sources: tuple[Path, ...],
    pvc_name: str,
    namespace: str,
    release_name: str,
    rollout_timeout: int,
    kubeconfig: Path | None,
) -> None:
    """Update Render Service template plugins."""
    _run_pvc_updater(
        namespace=namespace,
        release_name=release_name,
        rollout_timeout=rollout_timeout,
        kubeconfig=kubeconfig,
        update=lambda updater: updater.update_templates(sources, pvc_name=pvc_name),
    )


@pvc_updater_command.command("ztp")
@click.option(
    "--image",
    "images",
    type=click.Tuple((str, str, click.Path(exists=True, dir_okay=False, path_type=Path))),
    multiple=True,
    required=True,
    metavar="PLATFORM VERSION PATH",
    help="OS image metadata and local file. May be supplied more than once.",
)
@click.option("--pvc-name", default=ZTP_PVC_NAME, show_default=True)
@_pvc_cluster_options
def pvc_updater_ztp(
    images: tuple[tuple[str, str, Path], ...],
    pvc_name: str,
    namespace: str,
    kubeconfig: Path | None,
) -> None:
    """Update ZTP OS images and manifest.json."""
    sources = [
        ZTPImageSource(platform=platform, version=version, path=path)
        for platform, version, path in images
    ]
    _run_pvc_updater(
        namespace=namespace,
        kubeconfig=kubeconfig,
        update=lambda updater: updater.update_ztp(sources, pvc_name=pvc_name),
    )


class _CliCallback(DeployCallback):
    """Render deployer progress for headless CLI commands."""

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def on_step_update(self, step: DeployStep) -> None:
        icon = {
            "pending": "[ ]",
            "running": "[>]",
            "success": "[*]",
            "failed": "[!]",
            "skipped": "[-]",
        }
        click.echo(f"{icon.get(step.status, '[ ]')}  {step.label}")

    def on_log(self, message: str) -> None:
        click.echo(f"  {message}")

    def on_complete(self, success: bool, endpoints: list[str]) -> None:
        if success:
            click.echo(f"\n{self.operation} completed successfully!")
            for endpoint in endpoints:
                click.echo(f"  {endpoint}")
        else:
            click.echo(f"\n{self.operation} failed.", err=True)


def _run_deployer(
    config: NVConfigManagerInstallConfig,
    options: DeployOptions,
    *,
    operation: str,
) -> None:
    """Run a deployment pipeline and map failure to a Click exit status."""
    deployer = Deployer(config, options, _CliCallback(operation))
    if not deployer.run():
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
