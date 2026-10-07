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
"""Deploy-time steps owned by the bundled Nautobot provider."""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from nv_config_manager_installer.deployer import DeployStep, StepStatus
from nv_config_manager_installer.nautobot_jobs import NautobotJobRunner
from nv_config_manager_installer.providers.api import (
    DeployStepsContribution,
    ProviderDeployContext,
)
from nv_config_manager_installer.providers.nautobot.settings import (
    JOBS_PVC_NAME,
    NautobotSettings,
    nautobot_settings,
)
from nv_config_manager_installer.pvc_updater import (
    CONTENT_HASH_ANNOTATION,
    CUSTOM_JOBS_PACKAGE_MARKER,
)

_IGNORE_COMMON = (".venv", "__pycache__", ".git", "*.pyc")
_JOBS_CHANGED = "jobs_changed"
_LOADER_POD = "nv-config-manager-jobs-loader"

STEPS = DeployStepsContribution(
    before={"setup-templates-pvc": (("setup-jobs-pvc", "Setup custom jobs PVC"),)},
    after={
        "patch-gateway": (("restart-nautobot", "Restart Nautobot"),),
        "restart-render": (("run-jobs", "Run post-deploy jobs"),),
    },
)


def _job_paths(settings: NautobotSettings) -> list[Path]:
    """Return custom job paths; bootstrap jobs remain available from the image."""
    return [Path(j.path) for j in settings.jobs]


def _jobs_changed(ctx: ProviderDeployContext) -> bool:
    return bool(ctx.provider_state.get(_JOBS_CHANGED, True))


def detect_existing_state(ctx: ProviderDeployContext) -> None:
    """Record whether custom job content differs from the deployed PVC."""
    settings = nautobot_settings(ctx.config)
    if settings.jobs:
        ctx.provider_state[_JOBS_CHANGED] = ctx.check_content_diff(
            _job_paths(settings), JOBS_PVC_NAME, ctx.config.cluster.namespace, "Jobs content"
        )


def create_secrets(ctx: ProviderDeployContext, step: DeployStep) -> None:
    """Create the admin and Django Secrets for a local Nautobot."""
    if not ctx.config.dcim.local:
        return
    s = ctx.secrets_state
    ctx.apply_secret(
        step,
        "nautobot-admin",
        {
            "password": s.get("nautobot_admin_password", ""),
            "api_token": s.get("dcim_token", ""),
        },
    )
    ctx.apply_secret(step, "nautobot-django-secret", {"secret_key": s.get("django_secret_key", "")})


def setup_jobs_pvc(ctx: ProviderDeployContext) -> None:
    """Upload custom jobs into the jobs PVC before the Helm release."""
    settings = nautobot_settings(ctx.config)
    if not settings.jobs:
        ctx.skip_step("setup-jobs-pvc", "No custom jobs configured")
        return

    if not ctx.config.dcim.local:
        ctx.skip_step(
            "setup-jobs-pvc",
            "Skipped: custom jobs require local Nautobot (external target in use)",
        )
        ctx.callback.on_log(
            "WARNING: Custom jobs cannot be loaded into an external Nautobot instance"
        )
        return

    step = ctx.start_step("setup-jobs-pvc")
    ns = ctx.config.cluster.namespace
    jobs_config = settings.jobs_config

    if ctx.is_rerun and not _jobs_changed(ctx):
        step.output.append("Content unchanged, skipping upload")
        ctx.finish_step(step)
        return

    # Kill any leftover loader pod before touching the PVC so the
    # pvc-protection finalizer is released and ensure_pvc can proceed.
    ctx.k8s.delete_pod(_LOADER_POD, ns)
    ctx.k8s.wait_for_pod_gone(_LOADER_POD, ns)

    ctx.k8s.ensure_pvc(
        JOBS_PVC_NAME,
        ns,
        access_mode=jobs_config.access_mode or "ReadWriteOnce",
        storage_class=jobs_config.storage_class or None,
        allow_recreate=False,
    )
    step.output.append(f"PVC {JOBS_PVC_NAME}: ready")

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir) / "jobs"
        staging.mkdir()
        # The PVC is mounted as JOBS_ROOT/custom, which must be a package for
        # Nautobot's recursive module discovery to descend into it.
        (staging / "__init__.py").write_text(CUSTOM_JOBS_PACKAGE_MARKER)

        for job_entry in settings.jobs:
            src = Path(job_entry.path)
            if src.is_dir():
                shutil.copytree(
                    src,
                    staging / src.name,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*_IGNORE_COMMON),
                )

        tarball = Path(tmpdir) / "jobs.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            for item in staging.iterdir():
                tf.add(item, arcname=item.name)

        ctx.k8s.create_loader_pod(
            _LOADER_POD,
            ns,
            JOBS_PVC_NAME,
            mount_path="/jobs",
            node_selector=jobs_config.node_selector or None,
        )
        ctx.k8s.wait_for_pod_ready(_LOADER_POD, ns)
        ctx.callback.on_log("Loader pod ready, copying jobs tarball...")

        ctx.k8s.copy_to_pod(str(tarball), _LOADER_POD, ns, "/tmp/jobs.tar.gz")
        ctx.callback.on_log("Extracting jobs into PVC...")
        ctx.k8s.exec_command(
            _LOADER_POD,
            ns,
            [
                "sh",
                "-c",
                "cd /jobs && find . -mindepth 1 -maxdepth 1 -exec rm -rf {} \\; "
                "&& tar xzf /tmp/jobs.tar.gz && chown -R 1000:1000 /jobs",
            ],
        )
        ctx.k8s.delete_pod(_LOADER_POD, ns)
        step.output.append("Jobs uploaded to PVC")

    content_hash = ctx.content_hash(_job_paths(settings))
    ctx.k8s.annotate_pvc(JOBS_PVC_NAME, ns, CONTENT_HASH_ANNOTATION, content_hash)

    ctx.finish_step(step)


def restart_nautobot(ctx: ProviderDeployContext) -> None:
    """Roll the Nautobot deployments so changed custom jobs are picked up."""
    settings = nautobot_settings(ctx.config)
    if not settings.jobs:
        ctx.skip_step("restart-nautobot", "No custom jobs to reload")
        return

    if not ctx.is_rerun:
        ctx.skip_step("restart-nautobot", "Fresh install, Helm starts pods automatically")
        return

    if not _jobs_changed(ctx):
        ctx.skip_step("restart-nautobot", "Jobs unchanged, no restart needed")
        return

    step = ctx.start_step("restart-nautobot")
    ns = ctx.config.cluster.namespace
    release = ctx.config.cluster.release_name

    nautobot_deploys = [
        f"{release}-nautobot",
        f"{release}-nautobot-celery",
        f"{release}-nautobot-celery-beat",
    ]
    generations = {}
    for deploy in nautobot_deploys:
        generations[deploy] = ctx.k8s.restart_deployment(deploy, ns)
        ctx.callback.on_log(f"Triggered rollout restart for {deploy}")

    for deploy in nautobot_deploys:
        ctx.callback.on_log(f"Waiting for {deploy} rollout to complete...")
        try:
            ctx.k8s.wait_for_rollout(
                deploy,
                ns,
                timeout=360,
                on_message=ctx.callback.on_log,
                min_generation=generations[deploy],
            )
        except TimeoutError:
            ctx.callback.on_log(f"Rollout wait timed out for {deploy} (non-fatal)")

    step.output.append("Nautobot restarted to pick up new jobs")
    ctx.finish_step(step)


def run_post_deploy_jobs(ctx: ProviderDeployContext) -> None:
    """Run the configured post-deploy jobs through the Nautobot API."""
    settings = nautobot_settings(ctx.config)
    if not settings.run_after_deploy:
        ctx.skip_step("run-jobs", "No post-deploy jobs configured")
        return

    step = ctx.start_step("run-jobs")

    def log(message: str) -> None:
        line = f"  {message}"
        ctx.callback.on_log(line)
        step.output.append(line)

    runner = NautobotJobRunner(
        ctx.k8s,
        ctx.config.cluster.namespace,
        ctx.config.cluster.release_name,
        on_log=log,
    )
    total = len(settings.run_after_deploy)
    failed = 0
    for i, job_spec in enumerate(settings.run_after_deploy, 1):
        try:
            job_input = json.loads(job_spec.input) if job_spec.input else {}
            if not isinstance(job_input, dict):
                raise ValueError("Job input must be a JSON object")
            ok = runner.run(job_spec.job, job_input)
            label = f"Job {i}/{total}: {job_spec.job}"
            if ok:
                step.output.append(f"Completed: {label}")
            else:
                step.output.append(f"Failed: {label}")
                failed += 1
        except Exception as exc:
            step.output.append(f"Failed to run {job_spec.job}: {exc}")
            ctx.callback.on_log(f"  Error: {exc}")
            failed += 1

    if failed > 0:
        step.error = f"{failed} of {total} job(s) failed"
        ctx.finish_step(step, StepStatus.FAILED)
        raise RuntimeError(step.error)
    step.output.append(f"All {total} job(s) completed successfully")
    ctx.finish_step(step)


def integration_test_env(ctx: ProviderDeployContext) -> dict[str, str]:
    """Return the Nautobot URL and admin token for the integration test run."""
    ns = ctx.config.cluster.namespace
    release = ctx.config.cluster.release_name
    token = ""
    try:
        token = ctx.k8s.read_secret_data("nautobot-admin", ns).get("api_token", "")
    except Exception:
        ctx.callback.on_log("Could not retrieve nautobot-admin token; tests that need it will fail")
    return {"NAUTOBOT_URL": f"http://{release}-nautobot", "NAUTOBOT_TOKEN": token}


def endpoints(local: bool, server: str, hostname: str, proto: str) -> list[str]:
    """Return the Nautobot endpoint lines for the deployment summary."""
    if local:
        return [f"Nautobot:         {proto}://nautobot.{hostname}"]
    if server:
        return [f"Nautobot (ext):   {server}"]
    return []
