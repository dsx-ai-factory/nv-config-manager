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
"""NVIDIA Config Manager deployment engine.

Python-native deployment pipeline for NVIDIA Config Manager. Most Kubernetes
operations use the ``kubernetes`` Python client directly.  Subprocess calls
are reserved for tools without clean Python equivalents: ``helm``, ``docker``,
``kind``, and ``kubectl apply/wait`` for CRD installation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import selectors
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

import yaml

from nv_config_manager_installer.accounts import build_config_secrets_ini
from nv_config_manager_installer.helm_values import generate_helm_values
from nv_config_manager_installer.k8s import (
    LOADER_POD_IMAGE,
    K8sClient,
    kubectl_current_context,
    pin_kubeconfig_to_current_context,
)
from nv_config_manager_installer.nautobot_jobs import NautobotJobRunner
from nv_config_manager_installer.openbao import OpenBaoClient, OpenBaoPopulator
from nv_config_manager_installer.operator_versions import load_operator_versions
from nv_config_manager_installer.pvc_updater import (
    CUSTOM_JOBS_PACKAGE_MARKER,
    normalize_ztp_platform,
    validate_ztp_path_component,
)
from nv_config_manager_installer.schema import (
    BUILT_IN_NAUTOBOT_PROVIDER,
    GatewayType,
    ImageSource,
    LBProvider,
    NVConfigManagerInstallConfig,
    SecretsMethod,
    ZTPOSImage,
    ZTPStorageType,
)
from nv_config_manager_installer.secrets import generate_secrets

_DEFAULT_GATEWAY_CLASS_NAME = "envoy-gateway"
_HELM_RELEASE_NAME_ANNOTATION = "meta.helm.sh/release-name"
_HELM_RELEASE_NAMESPACE_ANNOTATION = "meta.helm.sh/release-namespace"


def find_project_root(start: Path | None = None) -> Path:
    """Return an explicit root or the source tree containing this installer package."""
    if start is not None:
        candidate = start.expanduser().resolve()
        if not candidate.is_dir():
            raise RuntimeError(f"Installer project root is not a directory: {candidate}")
        return candidate

    # deployer.py lives at installer/src/nv_config_manager_installer/deployer.py.
    # Resolving from the package prevents an unrelated wrapper repository in cwd
    # from being mistaken for the NVCM source tree.
    return Path(__file__).resolve().parents[3]


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DeployStep:
    id: str
    label: str
    status: StepStatus = StepStatus.PENDING
    output: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class _ParallelCommand:
    """A subprocess command that can run alongside other commands."""

    label: str
    cmd: list[str]
    timeout: int | None = 600
    env: dict[str, str] | None = None


@dataclass
class _RunningCommand:
    """Runtime state for a command managed by _run_logged_parallel."""

    command: _ParallelCommand
    proc: subprocess.Popen[str]
    started_at: float
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    latest_line: str = "waiting for output"
    open_streams: int = 0


class DeployCallback(Protocol):
    """Protocol for deployment progress callbacks."""

    def on_step_update(self, step: DeployStep) -> None: ...
    def on_log(self, message: str) -> None: ...
    def on_complete(self, success: bool, endpoints: list[str]) -> None: ...


class _NoopCallback:
    """Silent callback used when no UI is attached (CLI headless mode)."""

    def on_step_update(self, step: DeployStep) -> None: ...  # intentionally empty

    def on_log(self, message: str) -> None: ...  # intentionally empty

    def on_complete(self, success: bool, endpoints: list[str]) -> None: ...  # intentionally empty


@dataclass
class DeployOptions:
    """Options that influence the deployment pipeline but aren't in the config file."""

    chart_dir: str = "deploy/helm"
    project_root: Path | None = None
    build_images: bool = False
    load_kind: bool = False
    kind_cluster: str = "nv-config-manager"
    install_envoy_gateway: bool = False
    install_cert_manager: bool = False
    install_cnpg_operator: bool = False
    helm_timeout: str = "15m"
    helm_debug: bool = False
    watch_pods: bool = False
    recreate_secrets: bool = False
    run_tests: bool = False
    dry_run: bool = False
    vault_token_file: Path | None = None
    populate_vault: bool = True


@dataclass
class _RerunState:
    """Tracks what changed between runs so the deployer can skip unnecessary work."""

    is_rerun: bool = False
    jobs_changed: bool = True
    templates_changed: bool = True


_CONTENT_HASH_ANNOTATION = "nv-config-manager.nvidia.com/content-sha256"
_IGNORE_COMMON = (".venv", "__pycache__", ".git", "*.pyc")
_IGNORE_TEMPLATES = (".venv", "__pycache__", ".git", "tests")
_SKIP_REASON = "Not requested"
_CI_ENV_VAR = "CI"
_DOCKER_SYSTEM_PRUNE_COMMAND = ("docker", "system", "prune", "-af")
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_LOCAL_IMAGE_BUILDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("nv-config-manager-nautobot", "build/nautobot.Dockerfile", "components/nautobot", None),
    ("nv-config-manager-nats-ready", "build/nats-ready.Dockerfile", "components/nats-ready", None),
    ("nv-config-manager", "build/nv-config-manager.Dockerfile", ".", None),
    ("nv-config-manager-ui", "build/ui.Dockerfile", "ui", None),
    ("nv-config-manager-kea", "build/kea.Dockerfile", ".", None),
    ("nv-config-manager-kea-admin", "build/kea-admin.Dockerfile", ".", None),
    ("nv-config-manager-temporal", "build/temporal.Dockerfile", ".", "server"),
    (
        "nv-config-manager-temporal-bootstrap",
        "build/temporal.Dockerfile",
        ".",
        "bootstrap",
    ),
    ("nv-config-manager-temporal-ui", "build/temporal.Dockerfile", ".", "ui"),
)


def _is_ci_environment() -> bool:
    """Return whether installer-side CI-only behavior should be enabled."""
    return os.environ.get(_CI_ENV_VAR, "").strip().lower() in _TRUTHY_ENV_VALUES


def _build_job_paths(config: NVConfigManagerInstallConfig) -> list[Path]:
    """Return custom job paths; bootstrap jobs remain available from the image."""
    return [Path(j.path) for j in config.content.jobs]


def _hash_content_dir(
    paths: list[Path],
    ignore_patterns: tuple[str, ...] = _IGNORE_COMMON,
) -> str:
    """Produce a deterministic SHA-256 of the content that would be uploaded to a PVC.

    Stages files into memory with sorted names and a fixed mtime so the hash is
    reproducible across runs regardless of filesystem metadata.
    """
    h = hashlib.sha256()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tf:
        for src in sorted(paths):
            if not src.exists():
                continue
            if src.is_dir():
                ignore = shutil.ignore_patterns(*ignore_patterns)
                with tempfile.TemporaryDirectory() as tmp:
                    staged = Path(tmp) / src.name
                    shutil.copytree(src, staged, dirs_exist_ok=True, ignore=ignore)
                    for f in sorted(staged.rglob("*")):
                        if f.is_file():
                            info = tarfile.TarInfo(name=str(f.relative_to(tmp)))
                            info.size = f.stat().st_size
                            info.mtime = 0
                            with f.open("rb") as fh:
                                tf.addfile(info, fh)
            elif src.is_file():
                info = tarfile.TarInfo(name=src.name)
                info.size = src.stat().st_size
                info.mtime = 0
                with src.open("rb") as fh:
                    tf.addfile(info, fh)
    h.update(buf.getvalue())
    return h.hexdigest()


def _get_image_digest_tag(image: str) -> str:
    """Extract a short content-addressed tag from a local Docker image.

    Returns the first 12 hex characters of the image ID. Returns an empty string if the inspect fails or the image ID is empty.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        image_id = result.stdout.strip()
        hex_part = image_id.split(":")[-1]
        if not hex_part:
            return ""
        return f"sha-{hex_part[:12]}"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int | None = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return the result."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _gateway_class_helm_owner(name: str = _DEFAULT_GATEWAY_CLASS_NAME) -> tuple[str, str] | None:
    """Return the Helm release owner for an existing GatewayClass, if one exists."""
    result = _run(["kubectl", "get", "gatewayclass", name, "-o", "json"], check=False)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ("", "")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return ("", "")
    annotations = metadata.get("annotations") or {}
    if not isinstance(annotations, dict):
        return ("", "")
    return (
        str(annotations.get(_HELM_RELEASE_NAME_ANNOTATION, "")),
        str(annotations.get(_HELM_RELEASE_NAMESPACE_ANNOTATION, "")),
    )


def _docker_server_platform() -> str:
    """Return Docker server OS/architecture for platform-specific image pulls."""
    result = _run(
        ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"],
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode == 0 and "/" in value:
        return value

    arch = platform.machine().lower()
    arch = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(arch, arch)
    return f"linux/{arch}"


def _docker_image_id(image: str) -> str:
    """Return Docker's local image ID for an image reference."""
    result = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], check=True)
    return result.stdout.strip()


_DEFAULT_IMAGE_REGISTRY = "nvcr.io/nvidian/cfa"
_CNPG_OPERATOR_IMAGE_TAG = "1.29.0"
_PROMETHEUS_OPERATOR_CRDS_CHART_VERSION = "28.0.1"
_KEDA_CHART_VERSION = "2.20.1"
# Tracked separately from the chart version, as with _CNPG_OPERATOR_IMAGE_TAG:
# KEDA happens to keep the two in lockstep today, but they are independent knobs
# and the chart would otherwise default the tag from its own appVersion.
_KEDA_IMAGE_TAG = "2.20.1"
# Must stay in sync with networkPolicy.kedaNamespace in deploy/helm/values.yaml,
# which allows KEDA's trigger queries through the namespace-isolation policy.
_KEDA_NAMESPACE = "keda"
_KIND_PRELOAD_IMAGES_ENV = "NVCM_KIND_PRELOAD_IMAGES"
_KIND_PRELOAD_IMAGES = (LOADER_POD_IMAGE,)
_KIND_PRELOAD_PLATFORM_IMAGES: dict[str, dict[str, str]] = {
    LOADER_POD_IMAGE: {
        "linux/amd64": "docker.io/amd64/busybox:1.36",
        "linux/arm64": "docker.io/arm64v8/busybox:1.36",
    },
}


def _kind_preload_source_image(image: str, platform_name: str) -> str:
    """Return a single-platform source image to tag as *image* before Kind import."""
    return _KIND_PRELOAD_PLATFORM_IMAGES.get(image, {}).get(platform_name, image)


def _env_kind_preload_images() -> list[str]:
    """Return additional Kind preload images from the environment."""
    value = os.environ.get(_KIND_PRELOAD_IMAGES_ENV, "")
    return [part for part in value.replace(",", " ").split() if part]


def _kind_preload_images(config: NVConfigManagerInstallConfig) -> list[str]:
    """Return the effective ordered list of images to preload into Kind."""
    images: list[str] = []
    seen: set[str] = set()
    for image in (
        *_KIND_PRELOAD_IMAGES,
        *config.images.kind_preload_images,
        *_env_kind_preload_images(),
    ):
        image = image.strip()
        if not image or image in seen:
            continue
        seen.add(image)
        images.append(image)
    return images


def _kind_node_names(cluster: str) -> list[str]:
    """Return Docker container names for nodes in a Kind cluster."""
    result = _run(["kind", "get", "nodes", "--name", cluster], check=True)
    nodes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return nodes or [f"{cluster}-control-plane"]


def _strip_image_registry(repository: str) -> str:
    """Return repository path with any registry host removed."""
    first, sep, rest = repository.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return rest
    return repository


def _registry_repository(registry: str, source_repository: str) -> str:
    """Map a source image repository the same way upload-to-registry.sh does."""
    return f"{registry.rstrip('/')}/{_strip_image_registry(source_repository)}"


def _use_airgap_registry_defaults(config: NVConfigManagerInstallConfig) -> bool:
    """Return true when airgap mode should map bundled source paths automatically."""
    return bool(config.cluster.airgapped and config.images.registry != _DEFAULT_IMAGE_REGISTRY)


def _image_override_parts(
    config: NVConfigManagerInstallConfig,
    key: str,
    source_repository: str,
    default_tag: str,
) -> tuple[str, str] | None:
    """Return repository/tag override for installer-managed charts, if one is needed."""
    override = config.images.overrides.get(key)
    if not override and not _use_airgap_registry_defaults(config):
        return None

    if override and override.repository:
        repository = override.repository
    elif config.images.registry:
        repository = _registry_repository(config.images.registry, source_repository)
    else:
        repository = source_repository

    tag = override.tag if override and override.tag else default_tag
    return repository, tag


def _append_set_string(args: list[str], key: str, value: str) -> None:
    args.extend(["--set-string", f"{key}={value}"])


def _append_full_image_set_string(
    args: list[str],
    config: NVConfigManagerInstallConfig,
    key: str,
    value_key: str,
    source_repository: str,
    default_tag: str,
) -> None:
    image = _image_override_parts(config, key, source_repository, default_tag)
    if image is None:
        return
    repository, tag = image
    _append_set_string(args, value_key, f"{repository}:{tag}")


def _append_split_image_set_strings(
    args: list[str],
    config: NVConfigManagerInstallConfig,
    key: str,
    value_prefix: str,
    source_repository: str,
    default_tag: str,
) -> None:
    image = _image_override_parts(config, key, source_repository, default_tag)
    if image is None:
        return
    repository, tag = image
    _append_set_string(args, f"{value_prefix}.repository", repository)
    _append_set_string(args, f"{value_prefix}.tag", tag)


def _append_registry_split_image_set_strings(
    args: list[str],
    config: NVConfigManagerInstallConfig,
    key: str,
    value_prefix: str,
    source_repository: str,
    default_tag: str,
) -> None:
    """Override an image on a chart that keeps registry and repository separate.

    KEDA composes ``<registry>/<repository>:<tag>``, so overriding only the
    repository with a rewritten path would yield ``ghcr.io/<private-registry>/…``
    and still pull upstream. Blanking the registry is only correct when an
    override actually applies: the chart's own default repository is
    registry-relative (``kedacore/keda``), so clearing the registry
    unconditionally would silently redirect the default install to Docker Hub.
    Hence the same early return as ``_append_split_image_set_strings``.
    """
    image = _image_override_parts(config, key, source_repository, default_tag)
    if image is None:
        return
    repository, tag = image
    _append_set_string(args, f"{value_prefix}.registry", "")
    _append_set_string(args, f"{value_prefix}.repository", repository)
    _append_set_string(args, f"{value_prefix}.tag", tag)


def _check_deadline(
    deadline: float | None,
    proc: subprocess.Popen[str],
    cmd: list[str],
    timeout: int | None,
) -> float:
    """Return remaining seconds until *deadline*, killing *proc* if expired."""
    if deadline is None:
        return 1.0
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        proc.kill()
        raise subprocess.TimeoutExpired(cmd, timeout or 0)
    return min(remaining, 1.0)


def _stream_process(
    proc: subprocess.Popen[str],
    step: DeployStep,
    callback: DeployCallback,
    cmd: list[str],
    timeout: int | None,
) -> tuple[list[str], list[str]]:
    """Read stdout/stderr from *proc* via selectors, streaming to the callback.

    Returns ``(stdout_lines, stderr_lines)``.
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    deadline = time.monotonic() + timeout if timeout else None
    sel = selectors.DefaultSelector()
    try:
        if proc.stdout:
            sel.register(proc.stdout, selectors.EVENT_READ)
        if proc.stderr:
            sel.register(proc.stderr, selectors.EVENT_READ)

        while sel.get_map():
            wait = _check_deadline(deadline, proc, cmd, timeout)
            for key, _ in sel.select(timeout=wait):
                line = key.fileobj.readline()  # type: ignore[union-attr]
                if not line:
                    sel.unregister(key.fileobj)
                    continue
                line = line.rstrip("\n")
                step.output.append(line)
                callback.on_log(line)
                (stdout_lines if key.fileobj is proc.stdout else stderr_lines).append(line)
        proc.wait()
    finally:
        sel.close()
    return stdout_lines, stderr_lines


def _run_logged(
    cmd: list[str],
    step: DeployStep,
    callback: DeployCallback,
    *,
    check: bool = True,
    timeout: int | None = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming stdout/stderr to callback line-by-line."""
    cmd_str = " ".join(cmd[:4]) + ("..." if len(cmd) > 4 else "")
    callback.on_log(f"$ {cmd_str}")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout_lines, stderr_lines = _stream_process(proc, step, callback, cmd, timeout)
    except BaseException:
        proc.kill()
        proc.wait()
        raise

    stdout_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stdout_text, stderr_text)

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout_text, stderr_text)


def _run_logged_pipe(
    source_cmd: list[str],
    sink_cmd: list[str],
    step: DeployStep,
    callback: DeployCallback,
    *,
    check: bool = True,
    timeout: int | None = 600,
) -> subprocess.CompletedProcess[str]:
    """Run source_cmd with stdout piped to sink_cmd, streaming command output."""
    source_str = " ".join(source_cmd[:4]) + ("..." if len(source_cmd) > 4 else "")
    sink_str = " ".join(sink_cmd[:4]) + ("..." if len(sink_cmd) > 4 else "")
    callback.on_log(f"$ {source_str} | {sink_str}")

    source = subprocess.Popen(source_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sink = subprocess.Popen(
        sink_cmd,
        stdin=source.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if source.stdout:
        source.stdout.close()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    source_stderr_lines: list[str] = []
    deadline = time.monotonic() + timeout if timeout else None
    sel = selectors.DefaultSelector()
    try:
        if source.stderr:
            sel.register(source.stderr, selectors.EVENT_READ, "source-stderr")
        if sink.stdout:
            sel.register(sink.stdout, selectors.EVENT_READ, "sink-stdout")
        if sink.stderr:
            sel.register(sink.stderr, selectors.EVENT_READ, "sink-stderr")

        while sel.get_map():
            for proc, cmd in ((source, source_cmd), (sink, sink_cmd)):
                _check_deadline(deadline, proc, cmd, timeout)
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            for key, _ in sel.select(timeout=wait):
                line_bytes = key.fileobj.readline()  # type: ignore[union-attr]
                if not line_bytes:
                    sel.unregister(key.fileobj)
                    continue
                line = line_bytes.decode(errors="replace").rstrip("\n")
                step.output.append(line)
                callback.on_log(line)
                if key.data == "sink-stdout":
                    stdout_lines.append(line)
                elif key.data == "sink-stderr":
                    stderr_lines.append(line)
                else:
                    source_stderr_lines.append(line)

        source.wait()
        sink.wait()
    except BaseException:
        source.kill()
        sink.kill()
        source.wait()
        sink.wait()
        raise
    finally:
        sel.close()

    stdout_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)
    source_stderr_text = "\n".join(source_stderr_lines)

    if check and sink.returncode != 0:
        raise subprocess.CalledProcessError(sink.returncode, sink_cmd, stdout_text, stderr_text)
    if check and source.returncode != 0:
        raise subprocess.CalledProcessError(
            source.returncode,
            source_cmd,
            "",
            source_stderr_text,
        )

    return subprocess.CompletedProcess(sink_cmd, sink.returncode, stdout_text, stderr_text)


def _filter_envoy_gateway_crds(crd_stream: str) -> str:
    """Keep only Envoy Gateway CRDs from a Helm chart CRD stream."""
    documents = [
        document
        for document in yaml.safe_load_all(crd_stream)
        if isinstance(document, dict)
        and _document_metadata_name(document).endswith(".gateway.envoyproxy.io")
    ]
    if not documents:
        raise RuntimeError("Envoy Gateway chart did not include gateway.envoyproxy.io CRDs")
    return (
        "\n---\n".join(yaml.safe_dump(document, sort_keys=False).strip() for document in documents)
        + "\n"
    )


def _document_metadata_name(document: dict[str, Any]) -> str:
    """Return metadata.name from a parsed Kubernetes document."""
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("name", ""))


def _apply_envoy_gateway_crds(
    chart_ref: str,
    version: str | None,
    step: DeployStep,
    callback: DeployCallback,
) -> None:
    """Apply only Envoy Gateway CRDs from the Envoy chart."""
    helm_args = ["helm", "show", "crds", chart_ref]
    if version is not None:
        helm_args.extend(["--version", version])
    callback.on_log("$ helm show crds...")
    result = _run(helm_args, timeout=300)

    with tempfile.TemporaryDirectory() as tmpdir:
        crd_path = Path(tmpdir) / "envoy-gateway-crds.yaml"
        crd_path.write_text(_filter_envoy_gateway_crds(result.stdout))
        _run_logged(
            [
                "kubectl",
                "apply",
                "--server-side",
                "--force-conflicts",
                "-f",
                str(crd_path),
            ],
            step,
            callback,
            timeout=300,
        )


def _short_pod_text(value: str, *, limit: int = 140) -> str:
    """Return a compact single-line pod status detail."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _pod_ready_counts(pod: Any) -> tuple[int, int]:
    statuses = (pod.status.container_statuses if pod.status else None) or []
    total = len(statuses)
    ready = sum(1 for status in statuses if status.ready)
    return ready, total


def _pod_restart_count(pod: Any) -> int:
    statuses = []
    if pod.status:
        statuses.extend(pod.status.init_container_statuses or [])
        statuses.extend(pod.status.container_statuses or [])
    return sum(status.restart_count or 0 for status in statuses)


def _pod_is_ready(pod: Any) -> bool:
    if pod.status and pod.status.phase == "Succeeded":
        return True
    conditions = (pod.status.conditions if pod.status else None) or []
    for condition in conditions:
        if condition.type == "Ready":
            return condition.status == "True"
    ready, total = _pod_ready_counts(pod)
    return total > 0 and ready == total


def _container_waiting_details(statuses: list[Any] | None, *, prefix: str) -> list[str]:
    details: list[str] = []
    for status in statuses or []:
        if status.ready:
            continue
        name = status.name or "<unknown>"
        state = status.state
        if state and state.waiting:
            reason = state.waiting.reason or "Waiting"
            message = state.waiting.message or ""
            text = f"{prefix} {name}: {reason}"
            if message:
                text = f"{text} ({_short_pod_text(message)})"
            details.append(text)
        elif state and state.terminated:
            reason = state.terminated.reason or "Terminated"
            details.append(f"{prefix} {name}: {reason} exitCode={state.terminated.exit_code}")
        elif state and state.running:
            details.append(f"{prefix} {name}: Running but not ready")
        else:
            details.append(f"{prefix} {name}: status unknown")
    return details


def _pod_waiting_details(pod: Any) -> list[str]:
    details: list[str] = []
    if pod.status:
        for condition in pod.status.conditions or []:
            if condition.type in {"Ready", "ContainersReady"} or condition.status == "True":
                continue
            text = f"condition {condition.type}={condition.status}"
            if condition.reason:
                text = f"{text} ({condition.reason})"
            if condition.message:
                text = f"{text}: {_short_pod_text(condition.message)}"
            details.append(text)

        details.extend(
            _container_waiting_details(
                pod.status.init_container_statuses,
                prefix="init",
            )
        )
        details.extend(
            _container_waiting_details(
                pod.status.container_statuses,
                prefix="container",
            )
        )

        if not details and (pod.status.reason or pod.status.message):
            text = pod.status.reason or "Waiting"
            if pod.status.message:
                text = f"{text}: {_short_pod_text(pod.status.message)}"
            details.append(text)
    return details or ["waiting for readiness"]


def _unready_pod_summary_lines(k8s: K8sClient, namespace: str, *, limit: int = 8) -> list[str]:
    """Return concise status lines for pods that are not ready."""
    try:
        pods = k8s.v1.list_namespaced_pod(namespace).items
    except Exception as exc:
        return [f"pod summary unavailable: {exc}"]

    if not pods:
        return ["no pods found yet"]

    unready = [pod for pod in pods if not _pod_is_ready(pod)]
    unready.sort(key=lambda pod: pod.metadata.name or "")
    if not unready:
        return ["all pods ready"]

    visible = unready[:limit]
    lines = [
        f"{len(unready)} pod(s) not ready"
        + (f"; showing first {limit}" if len(unready) > limit else "")
    ]
    for pod in visible:
        name = pod.metadata.name or "<unknown>"
        ready, total = _pod_ready_counts(pod)
        phase = pod.status.phase if pod.status and pod.status.phase else "Unknown"
        restarts = _pod_restart_count(pod)
        details = "; ".join(_pod_waiting_details(pod)[:3])
        lines.append(f"{name} ready={ready}/{total} phase={phase} restarts={restarts}: {details}")
    return lines


def _emit_pod_summary(lines: list[str], step: DeployStep, callback: DeployCallback) -> None:
    for line in lines:
        msg = f"[pods] {line}"
        step.output.append(msg)
        callback.on_log(msg)


def _poll_pod_summary(
    k8s: K8sClient,
    namespace: str,
    step: DeployStep,
    callback: DeployCallback,
    stop_event: threading.Event,
    *,
    interval: float,
    heartbeat_interval: float,
) -> None:
    """Poll and emit changed pod readiness summaries until stopped."""
    last_signature = ""
    last_emit = 0.0
    while not stop_event.is_set():
        lines = _unready_pod_summary_lines(k8s, namespace)
        # list_namespaced_pod() can take seconds; re-check stop_event before
        # emitting so a shutdown that races the in-flight list doesn't produce
        # one last batch after _run_logged_with_pod_summary() has returned.
        if stop_event.is_set():
            break
        signature = "\n".join(lines)
        now = time.monotonic()
        has_blockers = lines != ["all pods ready"]
        if signature != last_signature or (has_blockers and now - last_emit >= heartbeat_interval):
            _emit_pod_summary(lines, step, callback)
            last_signature = signature
            last_emit = now
        if stop_event.wait(interval):
            break


def _run_logged_with_pod_summary(
    cmd: list[str],
    k8s: K8sClient | None,
    namespace: str,
    step: DeployStep,
    callback: DeployCallback,
    *,
    check: bool = True,
    timeout: int | None = 600,
    poll_interval: float = 10.0,
    heartbeat_interval: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run a command while periodically logging unready pod summaries."""
    if k8s is None:
        callback.on_log("Pod readiness summaries unavailable: Kubernetes client is not initialized")
        return _run_logged(cmd, step, callback, check=check, timeout=timeout)

    callback.on_log("Streaming pod readiness summaries while Helm waits...")
    stop_event = threading.Event()
    poll_thread = threading.Thread(
        target=_poll_pod_summary,
        args=(k8s, namespace, step, callback, stop_event),
        kwargs={"interval": poll_interval, "heartbeat_interval": heartbeat_interval},
        daemon=True,
    )
    poll_thread.start()
    try:
        return _run_logged(cmd, step, callback, check=check, timeout=timeout)
    finally:
        stop_event.set()
        poll_thread.join(timeout=2)


def _format_elapsed(seconds: float) -> str:
    """Return a compact elapsed-time string for long-running command status."""
    seconds_int = max(0, int(seconds))
    minutes, sec = divmod(seconds_int, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def _command_summary(cmd: list[str]) -> str:
    """Return a concise shell-style command summary for logs."""
    return " ".join(cmd[:4]) + ("..." if len(cmd) > 4 else "")


def _parallel_build_limit(command_count: int) -> int:
    """Return how many Docker builds to run at once."""
    configured = os.environ.get("NVCM_DOCKER_BUILD_PARALLELISM", "").strip()
    if configured:
        try:
            return max(1, min(command_count, int(configured)))
        except ValueError:
            pass
    cpu_count = os.cpu_count() or 2
    return max(1, min(command_count, 4, cpu_count))


def _append_command_line(
    state: _RunningCommand,
    stream: str,
    line: str,
    step: DeployStep,
    callback: DeployCallback,
) -> None:
    """Record and emit one line from a parallel subprocess."""
    for part in line.replace("\r", "\n").splitlines():
        text = part.strip()
        if not text:
            continue
        if stream == "stdout":
            state.stdout_lines.append(text)
        else:
            state.stderr_lines.append(text)
        state.latest_line = text
        msg = f"[{state.command.label}] {text}"
        step.output.append(msg)
        callback.on_log(msg)


def _kill_running_commands(states: list[_RunningCommand]) -> None:
    """Terminate all still-running commands."""
    for state in states:
        if state.proc.poll() is None:
            state.proc.kill()
    for state in states:
        if state.proc.poll() is None:
            state.proc.wait()


def _emit_parallel_status(
    states: list[_RunningCommand],
    step: DeployStep,
    callback: DeployCallback,
) -> None:
    """Emit elapsed-time status for each currently running command."""
    now = time.monotonic()
    for state in states:
        elapsed = _format_elapsed(now - state.started_at)
        msg = f"[{state.command.label}] running {elapsed} | latest: {state.latest_line}"
        step.output.append(msg)
        callback.on_log(msg)


def _run_logged_parallel(
    commands: list[_ParallelCommand],
    step: DeployStep,
    callback: DeployCallback,
    *,
    max_parallel: int,
    progress_interval: float = 15.0,
) -> None:
    """Run subprocess commands concurrently, streaming prefixed output to callback."""
    if not commands:
        return

    pending = list(commands)
    running: dict[subprocess.Popen[str], _RunningCommand] = {}
    selector = selectors.DefaultSelector()
    next_progress = time.monotonic() + progress_interval

    def start_available() -> None:
        while pending and len(running) < max_parallel:
            command = pending.pop(0)
            callback.on_log(f"[{command.label}] $ {_command_summary(command.cmd)}")
            proc = subprocess.Popen(
                command.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=command.env,
            )
            state = _RunningCommand(command=command, proc=proc, started_at=time.monotonic())
            if proc.stdout:
                selector.register(proc.stdout, selectors.EVENT_READ, (state, "stdout"))
                state.open_streams += 1
            if proc.stderr:
                selector.register(proc.stderr, selectors.EVENT_READ, (state, "stderr"))
                state.open_streams += 1
            running[proc] = state

    def finish_completed() -> None:
        completed: list[subprocess.Popen[str]] = []
        for proc, state in running.items():
            if proc.poll() is not None and state.open_streams == 0:
                completed.append(proc)

        for proc in completed:
            state = running.pop(proc)
            elapsed = _format_elapsed(time.monotonic() - state.started_at)
            if proc.returncode != 0:
                _kill_running_commands(list(running.values()))
                raise subprocess.CalledProcessError(
                    proc.returncode,
                    state.command.cmd,
                    "\n".join(state.stdout_lines),
                    "\n".join(state.stderr_lines),
                )
            msg = f"[{state.command.label}] completed in {elapsed}"
            step.output.append(msg)
            callback.on_log(msg)

    try:
        start_available()
        while running or pending:
            now = time.monotonic()
            for state in list(running.values()):
                if state.command.timeout is not None and (
                    now - state.started_at > state.command.timeout
                ):
                    _kill_running_commands(list(running.values()))
                    raise subprocess.TimeoutExpired(state.command.cmd, state.command.timeout)

            if now >= next_progress and running:
                _emit_parallel_status(list(running.values()), step, callback)
                next_progress = now + progress_interval

            timeout = max(0.1, min(1.0, next_progress - now))
            for key, _ in selector.select(timeout=timeout):
                state, stream = key.data
                line = key.fileobj.readline()  # type: ignore[union-attr]
                if line:
                    _append_command_line(state, stream, line.rstrip("\n"), step, callback)
                    continue
                selector.unregister(key.fileobj)
                state.open_streams -= 1

            finish_completed()
            start_available()
    except BaseException:
        _kill_running_commands(list(running.values()))
        raise
    finally:
        selector.close()


class Deployer:
    """Orchestrates the full NVIDIA Config Manager deployment pipeline."""

    CONFIG_MODEL: ClassVar[type[NVConfigManagerInstallConfig]] = NVConfigManagerInstallConfig

    def __init__(
        self,
        config: NVConfigManagerInstallConfig,
        options: DeployOptions,
        callback: DeployCallback | None = None,
    ) -> None:
        self.config = self.CONFIG_MODEL.model_validate(config.model_dump())
        self.options = options
        self.callback = callback or _NoopCallback()
        self._secrets_state: dict[str, str] = {}
        self._rerun = _RerunState()
        self._k8s: K8sClient | None = None
        self._local_image_tags: dict[str, str] = {}

        self.steps = self.create_steps()
        self._step_map = {s.id: s for s in self.steps}

    @classmethod
    def create_steps(cls) -> list[DeployStep]:
        """Return deployment steps shown by CLI and TUI frontends."""
        return [
            DeployStep("prereqs", "Check prerequisites"),
            DeployStep("build-images", "Build local images"),
            DeployStep("load-kind", "Load images to Kind"),
            DeployStep("install-crds", "Install CRDs / operators"),
            DeployStep("create-namespace", "Create namespace"),
            DeployStep("create-secrets", "Create Kubernetes secrets"),
            DeployStep("populate-vault", "Populate Vault secrets"),
            DeployStep("setup-jobs-pvc", "Setup custom jobs PVC"),
            DeployStep("setup-templates-pvc", "Setup template plugins PVC"),
            DeployStep("setup-ztp-pvc", "Setup ZTP images PVC"),
            DeployStep("generate-values", "Generate Helm values"),
            DeployStep("helm-install", "Helm install / upgrade"),
            DeployStep("patch-gateway", "Configure local Gateway access"),
            DeployStep("restart-nautobot", "Restart Nautobot"),
            DeployStep("restart-render", "Restart Render Service"),
            DeployStep("run-jobs", "Run post-deploy jobs"),
            DeployStep("refresh-cache", "Refresh caches"),
            DeployStep("run-tests", "Run integration tests"),
            DeployStep("endpoints", "Collect endpoints"),
        ]

    def _get_step(self, step_id: str) -> DeployStep:
        return self._step_map[step_id]

    def _start_step(self, step_id: str) -> DeployStep:
        step = self._get_step(step_id)
        step.status = StepStatus.RUNNING
        self.callback.on_step_update(step)
        return step

    def _finish_step(self, step: DeployStep, status: StepStatus = StepStatus.SUCCESS) -> None:
        step.status = status
        self.callback.on_step_update(step)

    def _skip_step(self, step_id: str, reason: str = "") -> None:
        step = self._get_step(step_id)
        step.status = StepStatus.SKIPPED
        if reason:
            step.output.append(reason)
        self.callback.on_step_update(step)

    def _validate_gateway_options(self) -> None:
        """Reject installer actions that apply only to Envoy Gateway."""
        if (
            self.options.install_envoy_gateway
            and self.config.infrastructure.gateway != GatewayType.ENVOY_GATEWAY
        ):
            raise RuntimeError(
                "--install-envoy-gateway can be used only with gateway=envoyGateway; "
                "install kgateway and its Gateway API CRDs before deploying Config Manager"
            )

    def _local_image_builds(self) -> tuple[tuple[str, str, str, str | None], ...]:
        """Return local images required by this deployment.

        Derived installers may override this hook to add or replace provider-owned
        images without reimplementing the build and Kind loading pipeline.
        """
        if self.config.services.nautobot:
            return _LOCAL_IMAGE_BUILDS
        return tuple(
            build for build in _LOCAL_IMAGE_BUILDS if build[0] != "nv-config-manager-nautobot"
        )

    def _validate_project_paths(self, project_root: Path) -> None:
        """Fail early when repository-owned deployment inputs are unavailable."""
        chart_dir = Path(self.options.chart_dir)
        chart_path = chart_dir if chart_dir.is_absolute() else project_root / chart_dir
        if not chart_path.is_dir():
            raise RuntimeError(
                f"Helm chart directory not found: {chart_path}. "
                "Pass --project-root when running from a separate installer repository."
            )

        if not self.options.build_images:
            return
        for name, dockerfile, context, _target in self._local_image_builds():
            missing = [
                path
                for path in (project_root / dockerfile, project_root / context)
                if not path.exists()
            ]
            if missing:
                raise RuntimeError(
                    f"Local image inputs for {name} were not found under {project_root}: "
                    + ", ".join(str(path) for path in missing)
                )

    def run(self) -> bool:
        """Execute the full deployment pipeline. Returns True on success."""
        project_root = find_project_root(self.options.project_root)
        self._validate_project_paths(project_root)
        original_cwd = Path.cwd()
        os.chdir(project_root)
        self.callback.on_log(f"Project root: {project_root}")

        # Pin every subprocess (helm, kubectl, kind, ...) and the embedded
        # Python kubernetes client to the exact context kubectl reports as
        # current. Without this, KUBECONFIG-merge ambiguity has caused the
        # installer to silently target a different cluster than the user
        # selected, creating namespaces and secrets in the wrong place.
        original_kubeconfig = os.environ.get("KUBECONFIG")
        pinned: tuple[Path, str] | None = None
        try:
            pinned = pin_kubeconfig_to_current_context()
        except Exception as exc:
            self.callback.on_log(f"Warning: could not pin kubeconfig context: {exc}")
        if pinned is not None:
            pin_path, pin_ctx = pinned
            self.callback.on_log(f"Pinned kubeconfig context: {pin_ctx} ({pin_path})")
        else:
            self.callback.on_log(
                "Warning: kubectl unavailable or no current-context — "
                "context may drift between Python client and helm/kubectl"
            )

        try:
            self._check_prerequisites()
            self._detect_existing_state()
            self._build_images()
            self._load_kind()
            self._install_crds()
            self._create_namespace()
            self._create_secrets()
            self._populate_vault()
            self._setup_jobs_pvc()
            self._setup_templates_pvc()
            self._setup_ztp_pvc()
            self._generate_values()
            self._run_provider_pre_helm_install()
            self._helm_install()
            self._run_provider_post_helm_install()
            self._patch_gateway()
            self._restart_nautobot()
            self._restart_render_service()
            self._run_post_deploy_jobs()
            self._refresh_caches()
            self._run_integration_tests()
            endpoints = self._collect_endpoints()
            self.callback.on_complete(True, endpoints)
            return True
        except Exception as exc:
            for step in self.steps:
                if step.status == StepStatus.RUNNING:
                    step.status = StepStatus.FAILED
                    step.error = str(exc)
                    self.callback.on_step_update(step)
            self.callback.on_log(f"Deployment failed: {exc}")
            for tb_line in traceback.format_exception(exc):
                for line in tb_line.rstrip().splitlines():
                    self.callback.on_log(line)
            self.callback.on_complete(False, [])
            return False
        finally:
            os.chdir(original_cwd)
            if pinned is not None:
                pin_path, _ = pinned
                try:
                    pin_path.unlink()
                except OSError:
                    pass
                if original_kubeconfig is not None:
                    os.environ["KUBECONFIG"] = original_kubeconfig
                else:
                    os.environ.pop("KUBECONFIG", None)

    # -- Step implementations ------------------------------------------------

    def _run_provider_pre_helm_install(self) -> None:
        """Run provider-owned deployment steps before the umbrella Helm release.

        Derived installers can insert provider-specific ``DeployStep`` entries
        through :meth:`create_steps` and execute them here without replacing the
        common deployment sequence.
        """

    def _run_provider_post_helm_install(self) -> None:
        """Run provider-owned deployment steps after the umbrella Helm release.

        This is intended for provider readiness checks and bootstrap work that
        depends on NVIDIA Config Manager services being installed.
        """

    def _check_prerequisites(self) -> None:
        step = self._start_step("prereqs")
        self._validate_gateway_options()
        for tool in ["kubectl", "helm"]:
            if not shutil.which(tool):
                raise RuntimeError(f"Required tool not found: {tool}")
            step.output.append(f"{tool}: found")

        try:
            self._k8s = K8sClient()
        except Exception as exc:
            raise RuntimeError(f"Cannot load kubeconfig: {exc}") from exc
        if not self._k8s.check_connectivity():
            raise RuntimeError("Cannot connect to Kubernetes cluster")
        step.output.append("Cluster connectivity: OK")

        # Surface which cluster we're actually talking to. Helpful when
        # KUBECONFIG merges several files or the user has multiple kind
        # clusters — silent context drift here was the cause of the
        # `helm install` "namespaces not found" failure.
        ctx = self._k8s.active_context or "<unknown>"
        server = self._k8s.api_server or "<unknown>"
        ctx_msg = f"Kubeconfig context: {ctx}  (server: {server})"
        step.output.append(ctx_msg)
        self.callback.on_log(ctx_msg)

        # Belt-and-braces: even after pinning, abort if the Python client and
        # kubectl disagree on the active context. A mismatch here means the
        # rest of the run would target two different clusters.
        kubectl_ctx = kubectl_current_context()
        if kubectl_ctx and ctx != "<unknown>" and kubectl_ctx != ctx:
            raise RuntimeError(
                f"Kube context mismatch: kubectl reports '{kubectl_ctx}' but the "
                f"Python kubernetes client bound to '{ctx}'. Refusing to deploy "
                "into an unintended cluster. Run `kubectx` (or `kubectl config "
                "use-context <name>`) to align them and retry."
            )

        needs_docker = self.options.build_images or self.options.load_kind
        if needs_docker:
            flags = " and ".join(
                flag
                for enabled, flag in (
                    (self.options.build_images, "--build-images"),
                    (self.options.load_kind, "--load-kind"),
                )
                if enabled
            )
            if not shutil.which("docker"):
                raise RuntimeError(f"docker is required for {flags}")
            # `docker info` exits non-zero if the CLI is installed but the daemon
            # is unreachable. Catching that here turns later subprocess errors
            # from `docker pull` / `docker save` into a clear prereq message.
            probe = _run(["docker", "info"], check=False)
            if probe.returncode != 0:
                stderr = (probe.stderr or "").strip().splitlines()
                detail = stderr[-1] if stderr else "docker info failed"
                raise RuntimeError(
                    f"docker daemon is required for {flags} but is not reachable: {detail}"
                )
        if self.options.load_kind and not shutil.which("kind"):
            raise RuntimeError("kind is required for --load-kind")

        self._validate_required_config(step)
        if self.config.secrets.method == SecretsMethod.ESO:
            self._validate_vault_config(step)

        if sys.platform == "linux":
            self._check_inotify_limits(step)

        self._finish_step(step)

    def _validate_vault_config(self, step: DeployStep) -> None:
        """Validate the Vault connection required for ESO population."""
        vault = self.config.secrets.vault
        missing = [
            name
            for name, value in (
                ("secrets.vault.server", vault.server),
                ("secrets.vault.secrets_path", vault.secrets_path),
            )
            if not value.strip()
        ]
        if missing:
            raise RuntimeError("Vault population requires ESO configuration: " + ", ".join(missing))
        step.output.append(f"Vault server: {vault.server}")

    def _validate_required_config(self, step: Any) -> None:
        """Fail fast on config that would crash mid-deploy with a confusing error.

        These checks belong here (not in the schema) because they are deploy-time
        invariants — config can be saved and re-loaded with empty fields, but
        running the pipeline against an empty hostname would render
        ``dnsNames: [null, "*."]`` into the cert-manager Certificate and fail
        deep inside `helm upgrade` after docker builds, image loads, and
        operator installs have already run.
        """
        hostname = (self.config.cluster.hostname or "").strip()
        if not hostname:
            raise RuntimeError(
                "Cluster hostname is empty. Set Cluster → Hostname in the TUI "
                "(e.g. 'config-manager.local' for kind, or your real DNS for prod) "
                "and retry. Without it the gateway TLS Certificate would be "
                "rendered with dnsNames=[null] and rejected by cert-manager."
            )
        step.output.append(f"Cluster hostname: {hostname}")

    _INOTIFY_MIN_INSTANCES = 512
    _INOTIFY_MIN_WATCHES = 524288

    def _check_inotify_limits(self, step: Any) -> None:
        """Warn if inotify kernel limits are too low for a kind cluster on Linux."""
        inotify_dir = Path("/proc/sys/fs/inotify")
        try:
            instances = int((inotify_dir / "max_user_instances").read_text())
            watches = int((inotify_dir / "max_user_watches").read_text())
        except OSError:
            return

        low: list[str] = []
        if instances < self._INOTIFY_MIN_INSTANCES:
            low.append(
                f"fs.inotify.max_user_instances={instances} (need >={self._INOTIFY_MIN_INSTANCES})"
            )
        if watches < self._INOTIFY_MIN_WATCHES:
            low.append(
                f"fs.inotify.max_user_watches={watches} (need >={self._INOTIFY_MIN_WATCHES})"
            )

        if low:
            raise RuntimeError(
                "inotify limits too low for kind cluster — pods will fail with "
                "'too many open files'.\n"
                f"  Current: {', '.join(low)}\n"
                "  Fix (temporary):  sudo sysctl "
                f"fs.inotify.max_user_instances={self._INOTIFY_MIN_INSTANCES} "
                f"fs.inotify.max_user_watches={self._INOTIFY_MIN_WATCHES}\n"
                "  Fix (persistent): echo 'fs.inotify.max_user_instances="
                f"{self._INOTIFY_MIN_INSTANCES}\\n"
                f"fs.inotify.max_user_watches={self._INOTIFY_MIN_WATCHES}' "
                "| sudo tee /etc/sysctl.d/99-inotify.conf && sudo sysctl --system"
            )

        step.output.append(f"inotify limits OK (instances={instances}, watches={watches})")

    def _check_content_diff(self, paths: list[Path], pvc_name: str, ns: str, label: str) -> bool:
        """Compare local content hash with the PVC annotation. Returns True if changed."""
        assert self._k8s is not None
        local_hash = _hash_content_dir(paths)
        remote_hash = self._k8s.get_pvc_annotation(pvc_name, ns, _CONTENT_HASH_ANNOTATION)
        changed = local_hash != remote_hash
        if self._rerun.is_rerun:
            self.callback.on_log(
                f"{label} {'changed since last deploy' if changed else 'unchanged'}"
            )
        return changed

    def _detect_existing_state(self) -> None:
        """Probe the cluster to determine fresh install vs. re-run and content diffs."""
        assert self._k8s is not None
        release = self.config.cluster.release_name
        ns = self.config.cluster.namespace

        result = _run(["helm", "status", release, "-n", ns], check=False)
        self._rerun.is_rerun = result.returncode == 0
        if self._rerun.is_rerun:
            self.callback.on_log(f"Re-run detected: Helm release '{release}' already exists")
        else:
            self.callback.on_log("Fresh install: no existing Helm release found")

        if self.config.content.jobs:
            self._rerun.jobs_changed = self._check_content_diff(
                _build_job_paths(self.config), "nautobot-custom-jobs", ns, "Jobs content"
            )

        if self.config.content.template_plugins:
            tpl_paths = [Path(t.path) for t in self.config.content.template_plugins]
            self._rerun.templates_changed = self._check_content_diff(
                tpl_paths, "render-service-template-plugins", ns, "Template plugins"
            )

    def _build_images(self) -> None:
        if not self.options.build_images:
            self._skip_step("build-images", _SKIP_REASON)
            return

        step = self._start_step("build-images")
        apt_mirror_args: list[str] = []
        for env_var in ("APT_MIRROR", "APT_MIRROR_DEBIAN", "APT_MIRROR_GPG_KEY_URL"):
            val = os.environ.get(env_var, "")
            if val:
                apt_mirror_args += ["--build-arg", f"{env_var}={val}"]
        nv_config_manager_build_args: list[str] = []
        for env_var in (
            "NVCM_NUMPY_FROM_SOURCE",
            "NVCM_NUMPY_CPU_BASELINE",
            "NVCM_NUMPY_CPU_DISPATCH",
            "NVCM_NUMPY_ALLOW_NOBLAS",
        ):
            val = os.environ.get(env_var, "")
            if val:
                nv_config_manager_build_args += ["--build-arg", f"{env_var}={val}"]

        build_env = {**os.environ, "DOCKER_BUILDKIT": "1"}
        use_buildx = bool(build_env.get("BUILDX_BUILDER"))
        build_cmd = ["docker", "buildx", "build"] if use_buildx else ["docker", "build"]
        build_output_args = ["--load"] if use_buildx else []
        build_commands: list[_ParallelCommand] = []
        image_builds = self._local_image_builds()
        for name, dockerfile, context, target in image_builds:
            build_tag = f"{name}:local"
            image_build_args = [*apt_mirror_args]
            if name == "nv-config-manager":
                image_build_args += nv_config_manager_build_args
            if target:
                image_build_args += ["--target", target]
            build_commands.append(
                _ParallelCommand(
                    label=name,
                    cmd=[
                        *build_cmd,
                        "--provenance=false",
                        "--progress=plain",
                        *build_output_args,
                        "--build-context",
                        "scripts=build/",
                        *image_build_args,
                        "-t",
                        build_tag,
                        "-f",
                        dockerfile,
                        context,
                    ],
                    timeout=900,
                    env=build_env,
                )
            )

        max_parallel = _parallel_build_limit(len(build_commands))
        self.callback.on_log(
            f"Building {len(build_commands)} local image(s) with up to "
            f"{max_parallel} parallel Docker build(s)..."
        )
        _run_logged_parallel(
            build_commands,
            step,
            self.callback,
            max_parallel=max_parallel,
        )

        for name, _, _, _ in image_builds:
            build_tag = f"{name}:local"
            digest_tag = _get_image_digest_tag(build_tag)
            if digest_tag:
                content_tag = f"{name}:{digest_tag}"
                _run(["docker", "tag", build_tag, content_tag], check=True)
                self._local_image_tags[name] = digest_tag
                self.callback.on_log(f"Tagged {content_tag}")
            else:
                self._local_image_tags[name] = "local"

        self._finish_step(step)

    def _load_kind(self) -> None:
        if not self.options.load_kind:
            self._skip_step("load-kind", _SKIP_REASON)
            return

        step = self._start_step("load-kind")
        cluster = self.options.kind_cluster
        for name, _, _, _ in self._local_image_builds():
            tag = self._local_image_tags.get(name, "local")
            img = f"{name}:{tag}"
            self.callback.on_log(f"Loading {img} into Kind cluster {cluster}...")
            _run_logged(
                ["kind", "load", "docker-image", img, "--name", cluster],
                step,
                self.callback,
                timeout=300,
            )
        platform_name = _docker_server_platform()
        for img in _kind_preload_images(self.config):
            # Pull arch-scoped official images when available. Docker can keep
            # multi-platform tags as manifest lists even after --platform,
            # which makes kind's default --all-platforms import fail on missing
            # platform content. Import helper images directly into node
            # containerd with the selected platform instead. Avoid ctr's
            # digest refs here; the tag is what Kubernetes needs to resolve.
            #
            # CI has hit docker/cli#6457-like failures where Docker's
            # containerd image store refuses to `docker save --platform <p>` a
            # freshly pulled multi-arch tag after related helper images were
            # pulled earlier in the loop. The prune is intentionally CI-only:
            # it is too broad for local developer machines.
            if _is_ci_environment():
                self.callback.on_log("Pruning host docker content store before next helper pull...")
                _run_logged(
                    list(_DOCKER_SYSTEM_PRUNE_COMMAND),
                    step,
                    self.callback,
                    timeout=120,
                )
            source_img = _kind_preload_source_image(img, platform_name)
            self.callback.on_log(
                f"Pulling helper image {source_img} for platform {platform_name}..."
            )
            _run_logged(
                ["docker", "pull", "--platform", platform_name, source_img],
                step,
                self.callback,
                timeout=300,
            )
            image_id = _docker_image_id(source_img)
            self.callback.on_log(f"Tagging selected-platform helper image {image_id} as {img}...")
            _run_logged(
                ["docker", "tag", image_id, img],
                step,
                self.callback,
                timeout=300,
            )
            self._load_kind_helper_image(img, cluster, platform_name, step)
        self._finish_step(step)

    def _load_kind_helper_image(
        self,
        image: str,
        cluster: str,
        platform_name: str,
        step: DeployStep,
    ) -> None:
        """Load a helper image into Kind node containerd for one platform."""
        # Pass --platform to `docker save` so the OCI archive only contains the
        # selected platform's index/manifest/config/layers. With Docker's
        # containerd image store, a plain `docker save` of a multi-arch tag
        # exports an index that still references the other platforms (and may
        # even drop the selected platform's config blob), causing
        # `ctr images import` to later fail with
        # `failed to resolve rootfs: content digest ...: not found`.
        nodes = _kind_node_names(cluster)
        for node in nodes:
            self.callback.on_log(f"Loading helper image {image} into Kind node {node}...")
            _run_logged_pipe(
                ["docker", "save", "--platform", platform_name, image],
                [
                    "docker",
                    "exec",
                    "--privileged",
                    "-i",
                    node,
                    "ctr",
                    "--namespace=k8s.io",
                    "images",
                    "import",
                    "--platform",
                    platform_name,
                    "--snapshotter=overlayfs",
                    "-",
                ],
                step,
                self.callback,
                timeout=300,
            )

    def _operator_bundle_root(self) -> Path | None:
        """Return the airgap bundle root if local charts/manifests are available."""
        chart_dir = Path(self.options.chart_dir).expanduser()
        if not chart_dir.is_absolute():
            chart_dir = Path.cwd() / chart_dir

        candidates = [chart_dir.resolve().parent, Path.cwd().resolve()]
        for candidate in candidates:
            if (candidate / "charts").is_dir() or (candidate / "manifests").is_dir():
                return candidate
        return None

    def _local_operator_chart(
        self,
        root: Path | None,
        chart_name: str,
        version: str,
    ) -> Path | None:
        """Find a packaged operator chart in an airgap bundle."""
        if root is None:
            return None
        charts_dir = root / "charts"
        candidates = [charts_dir / f"{chart_name}-{version}.tgz"]
        if version.startswith("v"):
            candidates.append(charts_dir / f"{chart_name}-{version[1:]}.tgz")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _local_operator_manifest(
        self,
        root: Path | None,
        manifest_name: str,
        version: str,
    ) -> Path | None:
        """Find a packaged dependency manifest in an airgap bundle."""
        if root is None:
            return None
        manifests_dir = root / "manifests"
        candidates = [manifests_dir / f"{manifest_name}-{version}.yaml"]
        if version.startswith("v"):
            candidates.append(manifests_dir / f"{manifest_name}-{version[1:]}.yaml")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _require_airgap_artifact(self, artifact: Path | None, description: str) -> Path | None:
        if artifact is None and self.config.cluster.airgapped:
            raise RuntimeError(f"Airgapped deployment requested but {description} was not found")
        return artifact

    def _install_crds(self) -> None:
        opts = self.options
        self._validate_gateway_options()
        observability_on = self.config.infrastructure.monitoring.observability_enabled
        if not any(
            [
                opts.install_envoy_gateway,
                opts.install_cert_manager,
                opts.install_cnpg_operator,
                observability_on,
            ]
        ):
            self._skip_step("install-crds", "No CRDs/operators requested")
            return

        step = self._start_step("install-crds")
        versions = load_operator_versions(Path(opts.chart_dir))
        bundle_root = self._operator_bundle_root()

        if opts.install_envoy_gateway:
            self.callback.on_log("Installing Envoy Gateway...")
            gateway_api_manifest = self._require_airgap_artifact(
                self._local_operator_manifest(
                    bundle_root,
                    "gateway-api",
                    versions.gateway_api_version,
                ),
                f"Gateway API manifest {versions.gateway_api_version}",
            )
            gateway_api_ref = (
                str(gateway_api_manifest)
                if gateway_api_manifest is not None
                else "https://github.com/kubernetes-sigs/gateway-api/releases/download/"
                f"{versions.gateway_api_version}/standard-install.yaml"
            )
            self.callback.on_log(f"Installing Gateway API {versions.gateway_api_version} CRDs...")
            _run_logged(
                [
                    "kubectl",
                    "apply",
                    "--server-side",
                    "--force-conflicts",
                    "-f",
                    gateway_api_ref,
                ],
                step,
                self.callback,
                timeout=300,
            )

            envoy_chart = self._require_airgap_artifact(
                self._local_operator_chart(
                    bundle_root,
                    "gateway-helm",
                    versions.envoy_gateway_version,
                ),
                f"Envoy Gateway chart {versions.envoy_gateway_version}",
            )
            envoy_chart_ref = (
                str(envoy_chart)
                if envoy_chart is not None
                else "oci://docker.io/envoyproxy/gateway-helm"
            )
            envoy_args = [
                "helm",
                "upgrade",
                "--install",
                "eg",
                envoy_chart_ref,
                "-n",
                "envoy-gateway-system",
                "--create-namespace",
                "--wait",
                "--timeout",
                "120s",
            ]
            if envoy_chart is not None:
                self.callback.on_log(f"Using local chart: {envoy_chart}")
            else:
                envoy_args.extend(["--version", versions.envoy_gateway_version])
            _apply_envoy_gateway_crds(
                envoy_chart_ref,
                None if envoy_chart is not None else versions.envoy_gateway_version,
                step,
                self.callback,
            )
            envoy_args.append("--skip-crds")
            _append_full_image_set_string(
                envoy_args,
                self.config,
                "envoyGateway",
                "global.images.envoyGateway.image",
                "docker.io/envoyproxy/gateway",
                versions.envoy_gateway_version,
            )
            _append_full_image_set_string(
                envoy_args,
                self.config,
                "envoyRatelimit",
                "global.images.ratelimit.image",
                "docker.io/envoyproxy/ratelimit",
                "c8765e89",
            )
            _run_logged(
                envoy_args,
                step,
                self.callback,
            )
            _run_logged(
                [
                    "kubectl",
                    "wait",
                    "--timeout=120s",
                    "-n",
                    "envoy-gateway-system",
                    "deployment/envoy-gateway",
                    "--for=condition=Available",
                ],
                step,
                self.callback,
                check=False,
            )

        if opts.install_cert_manager:
            self.callback.on_log("Installing cert-manager...")
            cert_manager_chart = self._require_airgap_artifact(
                self._local_operator_chart(
                    bundle_root,
                    "cert-manager",
                    versions.cert_manager_version,
                ),
                f"cert-manager chart {versions.cert_manager_version}",
            )
            cert_manager_chart_ref = (
                str(cert_manager_chart)
                if cert_manager_chart is not None
                else "jetstack/cert-manager"
            )
            cert_manager_args = [
                "helm",
                "upgrade",
                "--install",
                "cert-manager",
                cert_manager_chart_ref,
                "-n",
                "cert-manager",
                "--create-namespace",
                "--wait",
                "--timeout",
                "120s",
            ]
            if cert_manager_chart is not None:
                self.callback.on_log(f"Using local chart: {cert_manager_chart}")
                cert_manager_args.extend(["--set", "crds.enabled=true"])
            else:
                _run_logged(
                    [
                        "kubectl",
                        "apply",
                        "-f",
                        "https://github.com/cert-manager/cert-manager/releases/download/"
                        f"{versions.cert_manager_version}/cert-manager.crds.yaml",
                    ],
                    step,
                    self.callback,
                    check=False,
                )
                _run(
                    [
                        "helm",
                        "repo",
                        "add",
                        "jetstack",
                        "https://charts.jetstack.io",
                        "--force-update",
                    ],
                    check=False,
                )
                cert_manager_args.extend(["--version", versions.cert_manager_version])
            _append_split_image_set_strings(
                cert_manager_args,
                self.config,
                "certManagerController",
                "image",
                "quay.io/jetstack/cert-manager-controller",
                versions.cert_manager_version,
            )
            _append_split_image_set_strings(
                cert_manager_args,
                self.config,
                "certManagerWebhook",
                "webhook.image",
                "quay.io/jetstack/cert-manager-webhook",
                versions.cert_manager_version,
            )
            _append_split_image_set_strings(
                cert_manager_args,
                self.config,
                "certManagerCainjector",
                "cainjector.image",
                "quay.io/jetstack/cert-manager-cainjector",
                versions.cert_manager_version,
            )
            _append_split_image_set_strings(
                cert_manager_args,
                self.config,
                "certManagerStartupApiCheck",
                "startupapicheck.image",
                "quay.io/jetstack/cert-manager-startupapicheck",
                versions.cert_manager_version,
            )
            _append_split_image_set_strings(
                cert_manager_args,
                self.config,
                "certManagerAcmesolver",
                "acmesolver.image",
                "quay.io/jetstack/cert-manager-acmesolver",
                versions.cert_manager_version,
            )
            _run_logged(
                cert_manager_args,
                step,
                self.callback,
            )

        if opts.install_cnpg_operator:
            self.callback.on_log("Installing CNPG operator...")
            cnpg_chart = self._require_airgap_artifact(
                self._local_operator_chart(
                    bundle_root,
                    "cloudnative-pg",
                    versions.cnpg_operator_version,
                ),
                f"CNPG operator chart {versions.cnpg_operator_version}",
            )
            cnpg_chart_ref = str(cnpg_chart) if cnpg_chart is not None else "cnpg/cloudnative-pg"
            cnpg_args = [
                "helm",
                "upgrade",
                "--install",
                "cnpg",
                cnpg_chart_ref,
                "-n",
                "cnpg-system",
                "--create-namespace",
                "--wait",
                "--timeout",
                "120s",
            ]
            if cnpg_chart is not None:
                self.callback.on_log(f"Using local chart: {cnpg_chart}")
            else:
                _run(
                    [
                        "helm",
                        "repo",
                        "add",
                        "cnpg",
                        "https://cloudnative-pg.github.io/charts",
                        "--force-update",
                    ],
                    check=False,
                )
                cnpg_args.extend(["--version", versions.cnpg_operator_version])
            _append_split_image_set_strings(
                cnpg_args,
                self.config,
                "cnpgOperator",
                "image",
                "ghcr.io/cloudnative-pg/cloudnative-pg",
                _CNPG_OPERATOR_IMAGE_TAG,
            )
            _run_logged(
                cnpg_args,
                step,
                self.callback,
            )

        if observability_on:
            # Install prometheus-operator-crds as its own Helm release before
            # the main chart. The nv-config-manager chart's templates/monitoring.yaml
            # references PodMonitor / Probe (monitoring.coreos.com/v1), and if
            # we shipped the CRDs as a subchart helm would render both into a
            # single manifest and fail validation against API discovery on a
            # fresh cluster: "no matches for kind PodMonitor". Pre-installing
            # the CRDs as a separate release registers them cluster-wide so
            # the parent chart applies cleanly. See the long note in
            # deploy/helm/Chart.yaml for the full reasoning.
            self.callback.on_log("Installing prometheus-operator CRDs...")
            prom_crds_chart = self._require_airgap_artifact(
                self._local_operator_chart(
                    bundle_root,
                    "prometheus-operator-crds",
                    _PROMETHEUS_OPERATOR_CRDS_CHART_VERSION,
                ),
                f"prometheus-operator-crds chart {_PROMETHEUS_OPERATOR_CRDS_CHART_VERSION}",
            )
            prom_crds_chart_ref = (
                str(prom_crds_chart)
                if prom_crds_chart is not None
                else "prometheus-community/prometheus-operator-crds"
            )
            prom_crds_args = [
                "helm",
                "upgrade",
                "--install",
                "nv-config-manager-prom-crds",
                prom_crds_chart_ref,
                "-n",
                "nv-config-manager-monitoring",
                "--create-namespace",
                "--wait",
                "--timeout",
                "120s",
            ]
            if prom_crds_chart is not None:
                self.callback.on_log(f"Using local chart: {prom_crds_chart}")
            else:
                _run(
                    [
                        "helm",
                        "repo",
                        "add",
                        "prometheus-community",
                        "https://prometheus-community.github.io/helm-charts",
                        "--force-update",
                    ],
                    check=False,
                )
                prom_crds_args.extend(["--version", _PROMETHEUS_OPERATOR_CRDS_CHART_VERSION])
            _run_logged(
                prom_crds_args,
                step,
                self.callback,
            )

            # values-observability.yaml also turns on render-service
            # autoscaling, whose ScaledObjects are keda.sh/v1alpha1 — so KEDA
            # has the same ordering constraint as the CRDs above and must be a
            # separate release installed first, or the parent chart fails API
            # discovery with "no matches for kind ScaledObject".
            #
            # Installing the operator rather than only its CRDs is deliberate:
            # KEDA marks a ScaledObject Ready only after it resolves the
            # trigger against Prometheus, so the operator is what turns the
            # rendered PromQL into something CI can actually verify. CRDs alone
            # would admit a ScaledObject whose query matches no series.
            self.callback.on_log("Installing KEDA...")
            keda_chart = self._require_airgap_artifact(
                self._local_operator_chart(bundle_root, "keda", _KEDA_CHART_VERSION),
                f"keda chart {_KEDA_CHART_VERSION}",
            )
            keda_chart_ref = str(keda_chart) if keda_chart is not None else "kedacore/keda"
            keda_args = [
                "helm",
                "upgrade",
                "--install",
                "keda",
                keda_chart_ref,
                "-n",
                _KEDA_NAMESPACE,
                "--create-namespace",
                "--wait",
                # Longer than the CRD-only release above: this one waits on the
                # operator, metrics-apiserver, and admission-webhooks
                # deployments, each pulling from ghcr.io.
                "--timeout",
                "300s",
            ]
            if keda_chart is not None:
                self.callback.on_log(f"Using local chart: {keda_chart}")
            else:
                _run(
                    [
                        "helm",
                        "repo",
                        "add",
                        "kedacore",
                        "https://kedacore.github.io/charts",
                        "--force-update",
                    ],
                    check=False,
                )
                keda_args.extend(["--version", _KEDA_CHART_VERSION])
            # Swapping in the bundled chart is not enough for a private registry
            # or airgapped install: the chart's image defaults still point at
            # ghcr.io, so without these the operator would pull upstream and the
            # ScaledObjects would never be reconciled.
            for image_key, value_prefix, source_repository in (
                ("kedaOperator", "image.keda", "ghcr.io/kedacore/keda"),
                (
                    "kedaMetricsApiServer",
                    "image.metricsApiServer",
                    "ghcr.io/kedacore/keda-metrics-apiserver",
                ),
                (
                    "kedaAdmissionWebhooks",
                    "image.webhooks",
                    "ghcr.io/kedacore/keda-admission-webhooks",
                ),
            ):
                _append_registry_split_image_set_strings(
                    keda_args,
                    self.config,
                    image_key,
                    value_prefix,
                    source_repository,
                    _KEDA_IMAGE_TAG,
                )
            _run_logged(
                keda_args,
                step,
                self.callback,
            )

        self._finish_step(step)

    def _create_namespace(self) -> None:
        step = self._start_step("create-namespace")
        ns = self.config.cluster.namespace
        assert self._k8s is not None
        created = self._k8s.ensure_namespace(ns)
        # Verify the namespace ended up Active in the same cluster we're
        # about to install into. This catches stuck-Terminating namespaces
        # and silent context drift (where ensure_namespace appeared to
        # succeed but against a different cluster than `helm` will use).
        phase = self._k8s.namespace_phase(ns)
        ctx = self._k8s.active_context or "<unknown>"
        if phase != "Active":
            raise RuntimeError(
                f"Namespace '{ns}' is not Active (phase={phase}) in context '{ctx}'. "
                "Delete it (kubectl delete ns) or recreate the cluster and retry."
            )
        action = "Created" if created else "Namespace exists"
        msg = f"{action}: {ns} (context={ctx})"
        step.output.append(msg)
        self.callback.on_log(msg)
        self._finish_step(step)

    def _apply_secret(self, step: DeployStep, name: str, string_data: dict[str, str]) -> None:
        """Create or update a generic Opaque secret via the Python client."""
        assert self._k8s is not None
        ns = self.config.cluster.namespace
        exists = self._k8s.secret_exists(name, ns)
        if exists and not self.options.recreate_secrets:
            msg = f"Secret exists, skipping: {name}"
            step.output.append(msg)
            self.callback.on_log(msg)
            return
        self._k8s.apply_secret(name, ns, string_data)
        msg = f"{'Recreated' if exists else 'Created'}: {name}"
        step.output.append(msg)
        self.callback.on_log(msg)

    def _create_secrets(self) -> None:
        if self.config.secrets.method == SecretsMethod.ESO:
            self._skip_step("create-secrets", "Using ESO (Vault)")
            return

        assert self._k8s is not None
        step = self._start_step("create-secrets")
        self._secrets_state = generate_secrets(self.config)
        s = self._secrets_state

        self._create_core_secrets(step, s)
        self._create_network_secrets(step, s)
        self._create_optional_integration_secrets(step, s)
        self._create_git_token_secrets(step)
        self._create_image_pull_secret(step)

        if self.config.sso.enabled and self.config.sso.client_secret:
            self._apply_secret(
                step,
                "oidc-client-secret",
                {
                    "client-secret": self.config.sso.client_secret,
                    "cookie-secret": s["oidc_cookie_secret"],
                },
            )

        self._finish_step(step)

    def _resolve_vault_token(self) -> str:
        """Resolve a Vault provisioning token without storing it in the config."""
        if self.options.vault_token_file is not None:
            try:
                token = self.options.vault_token_file.expanduser().read_text().strip()
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot read Vault token file '{self.options.vault_token_file}': {exc}"
                ) from exc
            if token:
                return token

        token = (
            os.environ.get("VAULT_TOKEN", "").strip() or os.environ.get("OPENBAO_TOKEN", "").strip()
        )
        if token:
            return token

        vault = self.config.secrets.vault
        if vault.auth.method.value == "token" and vault.auth.token_secret_name:
            assert self._k8s is not None
            data = self._k8s.read_secret_data(
                vault.auth.token_secret_name,
                self.config.cluster.namespace,
            )
            if token := data.get("token", "").strip():
                return token

        raise RuntimeError(
            "Vault population requires a provisioning token. Pass --vault-token-file, "
            "set VAULT_TOKEN, or configure token auth with a Kubernetes Secret containing a "
            "'token' key."
        )

    def _populate_vault(self) -> None:
        """Create configured KV v2 mounts and populate missing ESO secret values."""
        if self.config.secrets.method != SecretsMethod.ESO:
            self._skip_step("populate-vault", "ESO is not selected")
            return
        if not self.options.populate_vault:
            self._skip_step(
                "populate-vault",
                "Vault population disabled; using pre-provisioned ESO paths",
            )
            return

        assert self._k8s is not None
        step = self._start_step("populate-vault")
        vault = self.config.secrets.vault
        client = OpenBaoClient(
            vault.server,
            self._resolve_vault_token(),
            namespace=vault.namespace,
        )
        result = OpenBaoPopulator(self.config, client).populate()
        for mount in result.mounts_created:
            message = f"Created Vault KV v2 mount: {mount}"
            step.output.append(message)
            self.callback.on_log(message)
        if result.paths_updated:
            message = (
                f"Populated {result.keys_added} missing key(s) across "
                f"{len(result.paths_updated)} Vault path(s)"
            )
        else:
            message = "Vault secrets already contain all configured values"
        step.output.append(message)
        self.callback.on_log(message)
        self._finish_step(step)

    def _create_core_secrets(self, step: DeployStep, s: dict[str, str]) -> None:
        """Create Redis, DCIM, DB, NATS, and device credential secrets."""
        self._apply_secret(step, "redis-password", {"password": s.get("redis_password", "")})
        dcim = self.config.dcim
        token_state_key = (
            "nautobot_token" if dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER else "dcim_token"
        )
        dcim_token_data = {dcim.token_secret_key: s.get(token_state_key, "")}
        if dcim.provider == BUILT_IN_NAUTOBOT_PROVIDER:
            if ro_token := s.get("nautobot_read_only_token"):
                dcim_token_data["read-only-token"] = ro_token
        self._apply_secret(step, dcim.token_secret_name, dcim_token_data)

        for db in ["temporal", "temporal_visibility", "config_store", "dhcp", "nautobot"]:
            self._apply_secret(
                step,
                f"cluster-{db.replace('_', '-')}-app",
                {
                    "username": s.get(f"{db}_db_user", ""),
                    "password": s.get(f"{db}_db_password", ""),
                },
            )

        nats_pw = s.get("nats_password", "")
        for nats_name in ("nats-sys", "nats-nv-config-manager", "nats-nautobot"):
            self._apply_secret(step, nats_name, {"password": nats_pw})

        if self.config.services.nautobot:
            self._apply_secret(
                step,
                "nautobot-admin",
                {
                    "password": s.get("nautobot_admin_password", ""),
                    "api_token": s.get("nautobot_token", ""),
                },
            )
            self._apply_secret(
                step, "nautobot-django-secret", {"secret_key": s.get("django_secret_key", "")}
            )

        if self.config.services.temporal:
            svc_user = self.config.secrets.config_manager_service_username or "nv-config-manager"
            self._apply_secret(
                step,
                "device-creds",
                {"username": svc_user},
            )

        if self.config.redfish.enabled:
            self._create_redfish_secret(step, s)

    def _create_redfish_secret(self, step: DeployStep, s: dict[str, str]) -> None:
        """Create ``redfish-creds`` K8s secret from per-vendor credentials."""
        data: dict[str, str] = {}
        for vendor, creds in self.config.redfish.vendors.items():
            data[f"{vendor}-default-user"] = (
                s.get(f"redfish_{vendor}_default_user") or creds.default_user or ""
            )
            data[f"{vendor}-default-password"] = (
                s.get(f"redfish_{vendor}_default_password") or creds.default_password or ""
            )
            data[f"{vendor}-nv-config-manager-password"] = (
                s.get(f"redfish_{vendor}_config_manager_password")
                or creds.config_manager_password
                or ""
            )
        if data:
            self._apply_secret(step, "redfish-creds", data)

    def _create_network_secrets(self, step: DeployStep, s: dict[str, str]) -> None:
        """Create the config-secrets.ini secret for the render service."""
        assert self._k8s is not None
        if not self.config.services.render:
            msg = "Skipping network secrets: render service disabled"
            step.output.append(msg)
            self.callback.on_log(msg)
            return
        if not self.config.sites:
            msg = "Skipping network secrets: no sites configured"
            step.output.append(msg)
            self.callback.on_log(msg)
            return
        ns = self.config.cluster.namespace
        release = self.config.cluster.release_name
        secret_name = f"{release}-network-secrets"
        exists = self._k8s.secret_exists(secret_name, ns)

        existing_data = {}
        if exists and not self.options.recreate_secrets:
            existing_data = self._k8s.read_secret_data(secret_name, ns)

        existing_ini = existing_data.get("config-secrets.ini", "")
        ini_content = build_config_secrets_ini(
            self.config,
            s,
            existing_content=existing_ini if exists and not self.options.recreate_secrets else None,
        )

        if exists and not self.options.recreate_secrets and existing_ini == ini_content:
            msg = f"Secret exists, skipping: {secret_name}"
            step.output.append(msg)
            self.callback.on_log(msg)
        else:
            self._k8s.apply_file_secret(
                secret_name, ns, {"config-secrets.ini": ini_content.encode()}
            )
            action = (
                "Recreated"
                if exists and self.options.recreate_secrets
                else "Updated"
                if exists
                else "Created"
            )
            msg = f"{action}: {secret_name}"
            step.output.append(msg)
            self.callback.on_log(msg)

    def _create_optional_integration_secrets(self, step: DeployStep, s: dict[str, str]) -> None:
        """Create Kubernetes secrets for optional integrations (Slack, Jira, CNPG backup)."""
        k8s = self.config.secrets.k8s

        if k8s.slack.enabled:
            token = s.get("slack_token", "")
            if not token:
                raise ValueError("Slack is enabled but slack_token is empty")
            self._apply_secret(step, "slack-token", {"token": token})

        if k8s.jira.enabled:
            api_token = s.get("jira_api_token", "")
            if not api_token:
                raise ValueError("Jira is enabled but jira_api_token is empty")
            self._apply_secret(step, "jira-creds", {"api-token": api_token})

        if k8s.cnpg_backup.enabled:
            access_key_id = s.get("cnpg_access_key_id", "")
            access_secret_key = s.get("cnpg_access_secret_key", "")
            if not all([access_key_id, access_secret_key]):
                raise ValueError("CNPG S3 backup is enabled but access credentials are empty")
            self._apply_secret(
                step,
                "cnpg-backup-credentials",
                {"ACCESS_KEY_ID": access_key_id, "ACCESS_SECRET_KEY": access_secret_key},
            )

        ztp_storage = self.config.infrastructure.ztp_storage
        if (
            ztp_storage.type == ZTPStorageType.S3
            and not ztp_storage.s3_ceph.enabled
            and k8s.ztp_s3.enabled
        ):
            access_key_id = s.get("ztp_s3_access_key_id", "")
            secret_access_key = s.get("ztp_s3_secret_access_key", "")
            if access_key_id or secret_access_key:
                if not all([access_key_id, secret_access_key]):
                    raise ValueError(
                        "ZTP S3 credentials require both accessKeyId and secretAccessKey"
                    )
                data = {
                    "CUSTOM_S3_ACCESS_KEY": access_key_id,
                    "CUSTOM_S3_SECRET_KEY": secret_access_key,
                }
                if endpoint := s.get("ztp_s3_endpoint", ""):
                    data["CUSTOM_S3_ENDPOINT"] = endpoint
                self._apply_secret(step, "ztp-s3-credentials", data)
            elif s.get("ztp_s3_endpoint", ""):
                raise ValueError(
                    "ZTP S3 endpoint without credentials should be configured as "
                    "infrastructure.ztp_storage.s3_endpoint"
                )

    def _create_git_token_secrets(self, step: DeployStep) -> None:
        """Create K8s secrets for configured git tokens."""
        for gt in self.config.git_tokens:
            if not gt.name or not gt.token:
                continue
            data: dict[str, str] = {"token": gt.token}
            if gt.username:
                data["username"] = gt.username
            self._apply_secret(step, f"git-token-{gt.name.lower()}", data)

    def _create_image_pull_secret(self, step: DeployStep) -> None:
        """Create the Docker registry pull secret if needed."""
        assert self._k8s is not None
        ps = self.config.images.pull_secret
        if not (self.config.images.source == ImageSource.REGISTRY and ps.password):
            return
        ns = self.config.cluster.namespace
        exists = self._k8s.secret_exists(ps.name, ns)
        if exists and not self.options.recreate_secrets:
            msg = f"Secret exists, skipping: {ps.name}"
            step.output.append(msg)
            self.callback.on_log(msg)
        else:
            self._k8s.apply_docker_registry_secret(
                ps.name, ns, server=ps.server, username=ps.username, password=ps.password
            )
            msg = f"{'Recreated' if exists else 'Created'}: {ps.name}"
            step.output.append(msg)
            self.callback.on_log(msg)

    def _setup_jobs_pvc(self) -> None:
        if not self.config.content.jobs:
            self._skip_step("setup-jobs-pvc", "No custom jobs configured")
            return

        if not self.config.services.nautobot:
            self._skip_step(
                "setup-jobs-pvc",
                "Skipped: custom jobs require local Nautobot (external target in use)",
            )
            self.callback.on_log(
                "WARNING: Custom jobs cannot be loaded into an external Nautobot instance"
            )
            return

        assert self._k8s is not None
        step = self._start_step("setup-jobs-pvc")
        ns = self.config.cluster.namespace
        pvc_name = "nautobot-custom-jobs"
        jobs_config = self.config.content.jobs_config

        if self._rerun.is_rerun and not self._rerun.jobs_changed:
            step.output.append("Content unchanged, skipping upload")
            self._finish_step(step)
            return

        # Kill any leftover loader pod before touching the PVC so the
        # pvc-protection finalizer is released and ensure_pvc can proceed.
        pod_name = "nv-config-manager-jobs-loader"
        self._k8s.delete_pod(pod_name, ns)
        self._k8s.wait_for_pod_gone(pod_name, ns)

        self._k8s.ensure_pvc(
            pvc_name,
            ns,
            access_mode=jobs_config.access_mode or "ReadWriteOnce",
            storage_class=jobs_config.storage_class or None,
            allow_recreate=False,
        )
        step.output.append(f"PVC {pvc_name}: ready")

        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir) / "jobs"
            staging.mkdir()
            # The PVC is mounted as JOBS_ROOT/custom, which must be a package for
            # Nautobot's recursive module discovery to descend into it.
            (staging / "__init__.py").write_text(CUSTOM_JOBS_PACKAGE_MARKER)

            for job_entry in self.config.content.jobs:
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

            self._k8s.create_loader_pod(
                pod_name,
                ns,
                pvc_name,
                mount_path="/jobs",
                node_selector=jobs_config.node_selector or None,
            )
            self._k8s.wait_for_pod_ready(pod_name, ns)
            self.callback.on_log("Loader pod ready, copying jobs tarball...")

            self._k8s.copy_to_pod(str(tarball), pod_name, ns, "/tmp/jobs.tar.gz")
            self.callback.on_log("Extracting jobs into PVC...")
            self._k8s.exec_command(
                pod_name,
                ns,
                [
                    "sh",
                    "-c",
                    "cd /jobs && find . -mindepth 1 -maxdepth 1 -exec rm -rf {} \\; "
                    "&& tar xzf /tmp/jobs.tar.gz && chown -R 1000:1000 /jobs",
                ],
            )
            self._k8s.delete_pod(pod_name, ns)
            step.output.append("Jobs uploaded to PVC")

        content_hash = _hash_content_dir(_build_job_paths(self.config))
        self._k8s.annotate_pvc(pvc_name, ns, _CONTENT_HASH_ANNOTATION, content_hash)

        self._finish_step(step)

    def _setup_templates_pvc(self) -> None:
        if not self.config.content.template_plugins:
            self._skip_step("setup-templates-pvc", "No template plugins configured")
            return

        assert self._k8s is not None
        step = self._start_step("setup-templates-pvc")
        ns = self.config.cluster.namespace
        pvc_name = "render-service-template-plugins"
        tpc = self.config.content.template_plugins_config

        # Kill any leftover loader pod before touching the PVC.
        pod_name = "nv-config-manager-tpl-loader"
        self._k8s.delete_pod(pod_name, ns)
        self._k8s.wait_for_pod_gone(pod_name, ns)

        pvc_recreated = self._k8s.ensure_pvc(
            pvc_name,
            ns,
            access_mode=tpc.access_mode or "ReadWriteOnce",
            storage_class=tpc.storage_class or None,
        )
        if pvc_recreated and self._rerun.is_rerun:
            step.output.append(
                "PVC spec changed (storage class or access mode); re-uploading content"
            )

        if self._rerun.is_rerun and not self._rerun.templates_changed and not pvc_recreated:
            step.output.append("Content unchanged, skipping upload")
            self._finish_step(step)
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir) / "plugins"
            staging.mkdir()

            for tpl in self.config.content.template_plugins:
                src = Path(tpl.path)
                if src.is_dir():
                    shutil.copytree(
                        src,
                        staging / src.name,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*_IGNORE_TEMPLATES),
                    )

            tarball = Path(tmpdir) / "plugins.tar.gz"
            with tarfile.open(tarball, "w:gz") as tf:
                for item in staging.iterdir():
                    tf.add(item, arcname=item.name)

            self._k8s.create_loader_pod(
                pod_name,
                ns,
                pvc_name,
                mount_path="/plugins",
                node_selector=tpc.node_selector or None,
            )
            self._k8s.wait_for_pod_ready(pod_name, ns)
            self.callback.on_log("Loader pod ready, copying templates tarball...")

            self._k8s.copy_to_pod(str(tarball), pod_name, ns, "/tmp/plugins.tar.gz")
            self.callback.on_log("Extracting template plugins into PVC...")
            self._k8s.exec_command(
                pod_name,
                ns,
                [
                    "sh",
                    "-c",
                    "cd /plugins && find . -mindepth 1 -maxdepth 1 -exec rm -rf {} \\; "
                    "&& tar xzf /tmp/plugins.tar.gz && chmod -R a+rX /plugins",
                ],
            )
            self._k8s.delete_pod(pod_name, ns)
            step.output.append("Template plugins uploaded to PVC")

        tpl_paths = [Path(t.path) for t in self.config.content.template_plugins]
        content_hash = _hash_content_dir(tpl_paths, ignore_patterns=_IGNORE_TEMPLATES)
        self._k8s.annotate_pvc(pvc_name, ns, _CONTENT_HASH_ANNOTATION, content_hash)

        self._finish_step(step)

    def _setup_ztp_pvc(self) -> None:
        zs = self.config.infrastructure.ztp_storage
        if zs.type != ZTPStorageType.FILE:
            self._skip_step("setup-ztp-pvc", "ZTP storage type is S3, no PVC needed")
            return

        assert self._k8s is not None
        step = self._start_step("setup-ztp-pvc")
        ns = self.config.cluster.namespace
        pvc_name = zs.pvc_name or "ztp-os-images"

        self._k8s.ensure_pvc(
            pvc_name,
            ns,
            size=zs.pvc_size or "10Gi",
            access_mode=zs.access_mode or "ReadWriteOnce",
            storage_class=zs.storage_class or None,
        )
        step.output.append(f"PVC '{pvc_name}' ready ({zs.pvc_size})")

        valid_images = [img for img in zs.os_images if img.path and Path(img.path).exists()]
        if not valid_images:
            for img in zs.os_images:
                if img.path and not Path(img.path).exists():
                    self.callback.on_log(f"Warning: OS image not found, skipping: {img.path}")
            step.output.append("No OS images to upload (can be added later via API)")
            self._finish_step(step)
            return

        self._upload_ztp_images(pvc_name, ns, valid_images, step)
        self._finish_step(step)

    @staticmethod
    def _compute_sha256(filepath: str) -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _upload_ztp_images(
        self,
        pvc_name: str,
        ns: str,
        images: list[ZTPOSImage],
        step: DeployStep,
    ) -> None:
        """Upload OS images into the ZTP PVC with proper directory structure and manifest.

        Mirrors the layout expected by ``FileStoreClient``:
        ``{mount}/manifest.json`` plus ``{mount}/{platform}/{version}/{filename}``
        """
        assert self._k8s is not None
        pod_name = "nv-config-manager-ztp-loader"
        mount_path = "/mnt/images"

        self._k8s.delete_pod(pod_name, ns)
        self._k8s.create_loader_pod(
            pod_name,
            ns,
            pvc_name,
            mount_path=mount_path,
            node_selector=self.config.infrastructure.ztp_storage.node_selector or None,
        )
        self._k8s.wait_for_pod_ready(pod_name, ns)
        self.callback.on_log("ZTP loader pod ready, uploading OS images...")

        manifest: dict[str, Any] = {"images": []}

        for img in images:
            local_path = img.path
            fname = Path(local_path).name
            platform = normalize_ztp_platform(img.platform)
            version = img.version
            validate_ztp_path_component("platform", platform)
            validate_ztp_path_component("version", version)

            self.callback.on_log(f"  Computing sha256 for {fname}...")
            checksum = self._compute_sha256(local_path)

            remote_dir = f"{mount_path}/{platform}/{version}"
            self._k8s.exec_command(pod_name, ns, ["mkdir", "-p", remote_dir])

            remote_tmp = f"/tmp/{fname}"
            self.callback.on_log(f"  Copying {fname} -> {platform}/{version}/...")
            self._k8s.copy_to_pod(local_path, pod_name, ns, remote_tmp)
            self._k8s.exec_command(pod_name, ns, ["mv", remote_tmp, f"{remote_dir}/{fname}"])

            manifest["images"].append(
                {
                    "platform": platform,
                    "version": version,
                    "filename": fname,
                    "path": f"{platform}/{version}/{fname}",
                    "sha256": checksum,
                    "tags": {"firmware-image": ""},
                }
            )
            step.output.append(f"Uploaded: {platform}/{version}/{fname}")

        manifest_json = json.dumps(manifest, indent=2)
        self.callback.on_log("  Writing manifest.json...")
        self._k8s.exec_command(
            pod_name,
            ns,
            [
                "sh",
                "-c",
                f"cat > {mount_path}/manifest.json << 'MANIFEST_EOF'\n{manifest_json}\nMANIFEST_EOF",
            ],
        )

        self._k8s.delete_pod(pod_name, ns)
        self.callback.on_log(f"Uploaded {len(images)} OS image(s) to PVC with manifest")

    def _generate_values(self) -> None:
        step = self._start_step("generate-values")
        if not self._secrets_state:
            self._secrets_state = generate_secrets(self.config)

        values_fd = tempfile.NamedTemporaryFile(
            suffix=".yaml", prefix="nv-config-manager-values-", delete=False
        )
        values_fd.close()
        self._values_file = Path(values_fd.name)
        generate_helm_values(
            self.config,
            self._secrets_state,
            self._values_file,
            local_images=self.config.images.source == ImageSource.LOCAL,
            local_tags=self._local_image_tags or None,
            chart_dir=self.options.chart_dir,
        )
        step.output.append(f"Generated: {self._values_file}")
        self._finish_step(step)

    def _refresh_cert_manager(self) -> None:
        """Delete stale webhook TLS secrets and restart cert-manager.

        cert-manager's admission webhook uses a self-signed TLS certificate
        stored in a Secret.  On idle Kind clusters this cert can expire, which
        causes every ``helm upgrade`` to fail with ``x509: certificate has
        expired`` when Helm tries to validate Certificate / Issuer resources.

        Simply restarting the pods isn't enough — the old secret with the
        expired cert gets reloaded.  We must delete the webhook TLS secrets
        first so cert-manager regenerates them on startup.
        """
        assert self._k8s is not None
        cm_ns = "cert-manager"

        try:
            if not self._k8s.namespace_exists(cm_ns):
                return
        except Exception:
            return

        self.callback.on_log("Refreshing cert-manager webhook certificates...")

        try:
            secrets = self._k8s.v1.list_namespaced_secret(cm_ns)
            for secret in secrets.items:
                stype = secret.type or ""
                name = secret.metadata.name or ""
                if stype == "kubernetes.io/tls" or "webhook" in name or "ca" in name:
                    self._k8s.delete_secret(name, cm_ns)
                    self.callback.on_log(f"Deleted secret {name}")
        except Exception as exc:
            self.callback.on_log(f"Could not clean webhook secrets: {exc}")

        try:
            deploys = self._k8s.list_deployment_names(cm_ns)
        except Exception:
            return

        if not deploys:
            return

        generations = {}
        for deploy in deploys:
            generations[deploy] = self._k8s.restart_deployment(deploy, cm_ns)
            self.callback.on_log(f"Restarted {deploy}")

        for deploy in deploys:
            try:
                self._k8s.wait_for_rollout(
                    deploy,
                    cm_ns,
                    timeout=120,
                    on_message=self.callback.on_log,
                    min_generation=generations[deploy],
                )
            except TimeoutError:
                self.callback.on_log(f"Timeout waiting for {deploy} (continuing anyway)")

        time.sleep(5)
        self.callback.on_log("cert-manager refreshed — webhook certs regenerated")

    def _helm_install(self) -> None:
        if self.options.dry_run:
            self._skip_step("helm-install", "Dry run mode")
            return

        step = self._start_step("helm-install")
        release = self.config.cluster.release_name
        ns = self.config.cluster.namespace
        chart = self.options.chart_dir

        if self._rerun.is_rerun and self.config.infrastructure.tls:
            self._refresh_cert_manager()

        size = self.config.cluster.size.value
        size_values = Path(chart) / f"values-local-{size}.yaml"

        # Resolve subchart dependencies during connected installs. Airgapped
        # bundles already vendor the allowed dependency charts, and dependency
        # update would otherwise try to reach public Helm repositories.
        if self.config.cluster.airgapped:
            self.callback.on_log("Skipping helm dependency update in airgapped mode")
        else:
            self.callback.on_log("Running: helm dependency update ...")
            _run_logged(
                ["helm", "dependency", "update", chart],
                step,
                self.callback,
                timeout=300,
            )

        helm_args = [
            "helm",
            "upgrade",
            "--install",
            release,
            chart,
            "-n",
            ns,
            "-f",
            str(self._values_file),
            "--wait",
            "--timeout",
            self.options.helm_timeout,
        ]

        if self.options.helm_debug:
            helm_args.append("--debug")

        if size_values.exists():
            helm_args.extend(["-f", str(size_values)])

        if self._should_reuse_existing_gateway_class():
            helm_args.extend(["--set", "gateway.createGatewayClass=false"])

        # Local-dev metrics overlay (Prometheus + Alloy).
        # See deploy/helm/values-observability.yaml for the LOCAL-DEV-ONLY warning.
        if self.config.infrastructure.monitoring.observability_enabled:
            observability_values = Path(chart) / "values-observability.yaml"
            if observability_values.exists():
                self.callback.on_log(
                    "Layering values-observability.yaml (LOCAL-DEV observability stack)"
                )
                helm_args.extend(["-f", str(observability_values)])
            else:
                self.callback.on_log(
                    f"WARNING: observability enabled but {observability_values} not found"
                )

        debug_suffix = " --debug" if "--debug" in helm_args else ""
        self.callback.on_log(f"Running: helm upgrade --install {release}{debug_suffix} ...")
        if self.options.helm_debug and self.options.watch_pods:
            _run_logged_with_pod_summary(
                helm_args,
                self._k8s,
                ns,
                step,
                self.callback,
                timeout=1200,
            )
        else:
            _run_logged(helm_args, step, self.callback, timeout=1200)
        self._finish_step(step)

    def _should_reuse_existing_gateway_class(self) -> bool:
        """Return True when an existing GatewayClass is owned by another Helm release."""
        if (
            self.config.infrastructure.gateway != GatewayType.ENVOY_GATEWAY
            or not self.config.infrastructure.create_gateway
            or not self.config.infrastructure.create_gateway_class
        ):
            return False

        owner = _gateway_class_helm_owner()
        if owner is None:
            return False

        release_name, release_namespace = owner
        expected = (self.config.cluster.release_name, self.config.cluster.namespace)
        if owner == expected:
            return False

        self.callback.on_log(
            "GatewayClass "
            f"{_DEFAULT_GATEWAY_CLASS_NAME!r} already exists"
            f" and is owned by Helm release {release_name or '<unowned>'!r}"
            f" in namespace {release_namespace or '<unowned>'!r};"
            " reusing it by setting gateway.createGatewayClass=false"
        )
        return True

    def _patch_gateway(self) -> None:
        if self.config.infrastructure.gateway != GatewayType.ENVOY_GATEWAY:
            self._skip_step(
                "patch-gateway",
                "kgateway configures local NodePorts through GatewayParameters",
            )
            return

        lb = self.config.infrastructure.load_balancer
        if lb.provider != LBProvider.NONE:
            self._skip_step("patch-gateway", "LoadBalancer provider configured, no patching needed")
            return

        assert self._k8s is not None
        step = self._start_step("patch-gateway")
        ns = self.config.cluster.namespace
        gw_ns = "envoy-gateway-system"
        label = f"gateway.envoyproxy.io/owning-gateway-namespace={ns}"

        deployments = self._k8s.list_deployment_names(gw_ns, label_selector=label)
        if not deployments:
            step.output.append("No Envoy Gateway deployment found, skipping patch")
            self._finish_step(step)
            return

        deployment = deployments[0]
        existing_host_ports = self._k8s.get_deployment_host_ports(deployment, gw_ns)
        desired = [
            {"containerPort": 10080, "hostPort": 30080, "protocol": "TCP"},
            {"containerPort": 10443, "hostPort": 30443, "protocol": "TCP"},
        ]
        patch = [
            {"op": "add", "path": "/spec/template/spec/containers/0/ports/-", "value": p}
            for p in desired
            if p["hostPort"] not in existing_host_ports
        ]
        if patch:
            try:
                self._k8s.patch_deployment_json(deployment, gw_ns, patch)
            except Exception as exc:
                self.callback.on_log(f"Gateway patch (non-fatal): {exc}")

        self._k8s.delete_pods_by_label(gw_ns, label_selector=label)
        try:
            self._k8s.wait_for_deployment_available(deployment, gw_ns, timeout=120)
        except TimeoutError:
            self.callback.on_log("Gateway deployment wait timed out (non-fatal)")

        step.output.append(f"Patched {deployment} with NodePort 30080/30443")
        self._finish_step(step)

    def _restart_nautobot(self) -> None:
        if not self.config.content.jobs:
            self._skip_step("restart-nautobot", "No custom jobs to reload")
            return

        if not self._rerun.is_rerun:
            self._skip_step("restart-nautobot", "Fresh install, Helm starts pods automatically")
            return

        if not self._rerun.jobs_changed:
            self._skip_step("restart-nautobot", "Jobs unchanged, no restart needed")
            return

        assert self._k8s is not None
        step = self._start_step("restart-nautobot")
        ns = self.config.cluster.namespace
        release = self.config.cluster.release_name

        nautobot_deploys = [
            f"{release}-nautobot",
            f"{release}-nautobot-celery",
            f"{release}-nautobot-celery-beat",
        ]
        generations = {}
        for deploy in nautobot_deploys:
            generations[deploy] = self._k8s.restart_deployment(deploy, ns)
            self.callback.on_log(f"Triggered rollout restart for {deploy}")

        for deploy in nautobot_deploys:
            self.callback.on_log(f"Waiting for {deploy} rollout to complete...")
            try:
                self._k8s.wait_for_rollout(
                    deploy,
                    ns,
                    timeout=360,
                    on_message=self.callback.on_log,
                    min_generation=generations[deploy],
                )
            except TimeoutError:
                self.callback.on_log(f"Rollout wait timed out for {deploy} (non-fatal)")

        step.output.append("Nautobot restarted to pick up new jobs")
        self._finish_step(step)

    def _restart_render_service(self) -> None:
        if not self.config.content.template_plugins:
            self._skip_step("restart-render", "No template plugins configured")
            return

        if not self._rerun.is_rerun:
            self._skip_step("restart-render", "Fresh install, Helm starts pods automatically")
            return

        if not self._rerun.templates_changed:
            self._skip_step("restart-render", "Templates unchanged, no restart needed")
            return

        assert self._k8s is not None
        step = self._start_step("restart-render")
        ns = self.config.cluster.namespace

        release = self.config.cluster.release_name
        render_deploys = self._k8s.list_deployment_names(ns, label_selector=f"app={release}-render")
        if not render_deploys:
            step.output.append("No render-service deployments found, skipping restart")
            self._finish_step(step)
            return

        generations = {}
        for deploy_name in render_deploys:
            generations[deploy_name] = self._k8s.restart_deployment(deploy_name, ns)
            self.callback.on_log(f"Triggered rollout restart for {deploy_name}")

        for deploy_name in render_deploys:
            self.callback.on_log(f"Waiting for {deploy_name} rollout to complete...")
            try:
                self._k8s.wait_for_rollout(
                    deploy_name,
                    ns,
                    timeout=300,
                    on_message=self.callback.on_log,
                    min_generation=generations[deploy_name],
                )
            except TimeoutError:
                self.callback.on_log(f"Rollout wait timed out for {deploy_name} (non-fatal)")

        step.output.append("Render service restarted to pick up new templates")
        self._finish_step(step)

    def _run_post_deploy_jobs(self) -> None:
        if not self.config.content.run_after_deploy:
            self._skip_step("run-jobs", "No post-deploy jobs configured")
            return

        step = self._start_step("run-jobs")

        assert self._k8s is not None
        runner = NautobotJobRunner(
            self._k8s,
            self.config.cluster.namespace,
            self.config.cluster.release_name,
            on_log=lambda message: self._log_post_deploy_job(message, step),
        )
        total = len(self.config.content.run_after_deploy)
        failed = 0
        for i, job_spec in enumerate(self.config.content.run_after_deploy, 1):
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
                self.callback.on_log(f"  Error: {exc}")
                failed += 1

        if failed > 0:
            step.error = f"{failed} of {total} job(s) failed"
            self._finish_step(step, StepStatus.FAILED)
            raise RuntimeError(step.error)
        else:
            step.output.append(f"All {total} job(s) completed successfully")
            self._finish_step(step)

    def _log_post_deploy_job(self, message: str, step: DeployStep) -> None:
        """Stream a shared Nautobot-job runner message to deployment progress."""
        line = f"  {message}"
        self.callback.on_log(line)
        step.output.append(line)

    def _refresh_caches(self) -> None:
        if not self.config.content.run_after_deploy:
            self._skip_step("refresh-cache", "No post-deploy jobs ran")
            return

        assert self._k8s is not None
        step = self._start_step("refresh-cache")
        ns = self.config.cluster.namespace
        release = self.config.cluster.release_name

        restarts: list[str] = []
        if self.config.services.config_store:
            restarts.append(f"{release}-config-store-cache-refresh")
        if self.config.services.dhcp:
            restarts.append(f"{release}-dhcp-refresh")

        if not restarts:
            step.output.append("No cache services to refresh")
            self._finish_step(step)
            return

        generations: dict[str, int] = {}
        for deploy_name in restarts:
            try:
                generations[deploy_name] = self._k8s.restart_deployment(deploy_name, ns)
                self.callback.on_log(f"Restarted {deploy_name}")
            except Exception as exc:
                self.callback.on_log(f"Restart failed for {deploy_name} (non-fatal): {exc}")

        for deploy_name in restarts:
            try:
                self._k8s.wait_for_rollout(
                    deploy_name,
                    ns,
                    timeout=120,
                    min_generation=generations.get(deploy_name, 0),
                )
                step.output.append(f"Refreshed {deploy_name}")
            except Exception as exc:
                self.callback.on_log(f"Rollout wait timed out for {deploy_name} (non-fatal): {exc}")

        self._finish_step(step)

    def _find_ztp_pod(self, ns: str) -> str:
        """Locate a ZTP pod for running integration tests."""
        assert self._k8s is not None
        try:
            pods = self._k8s.v1.list_namespaced_pod(
                ns, label_selector="app.kubernetes.io/component=network-ztp"
            )
            if pods.items:
                return pods.items[0].metadata.name or ""
        except Exception:
            pass
        try:
            pods = self._k8s.v1.list_namespaced_pod(ns)
            for pod in pods.items:
                name = pod.metadata.name or ""
                if "ztp" in name and "api" not in name:
                    return name
        except Exception:
            pass
        return ""

    def _build_test_script(self, ns: str, release: str, token: str) -> str:
        """Generate the Python test runner script to exec in-pod."""
        token_escaped = token.replace("'", "'\\''")
        return (
            "import os, sys\n"
            "os.environ.update({\n"
            f"    'NAUTOBOT_URL': 'http://{release}-nautobot',\n"
            f"    'RENDER_URL': 'http://{release}-render-api:9000',\n"
            f"    'ZTP_URL': 'http://{release}-ztp-api:9000',\n"
            f"    'DHCP_URL': 'http://{release}-dhcp-internal:9000',\n"
            f"    'TEMPORAL_URL': 'http://{release}-temporal-api:9000',\n"
            f"    'NAUTOBOT_TOKEN': '{token_escaped}',\n"
            "    'SFTP_HOST': '127.0.0.1',\n"
            "    'SFTP_PORT': '2222',\n"
            "})\n"
            "sys.exit(__import__('pytest').main([\n"
            "    'src/tests/integration/',\n"
            "    '-v',\n"
            "    '--tb=short',\n"
            f"    '--nv-config-manager-namespace', '{ns}',\n"
            "    '--no-header',\n"
            "]))\n"
        )

    def _run_integration_tests(self) -> None:
        if not self.options.run_tests:
            self._skip_step("run-tests", _SKIP_REASON)
            return

        if self.config.sso.enabled:
            self._skip_step("run-tests", "Skipped: SSO enabled — tests require OIDC browser auth")
            return

        assert self._k8s is not None
        step = self._start_step("run-tests")
        ns = self.config.cluster.namespace
        release = self.config.cluster.release_name

        ztp_pod = self._find_ztp_pod(ns)
        if not ztp_pod:
            step.error = "Could not find ZTP pod for running tests"
            self._finish_step(step, StepStatus.FAILED)
            return

        self.callback.on_log(f"Using ZTP pod as test runner: {ztp_pod}")

        nautobot_token = ""
        try:
            data = self._k8s.read_secret_data("nautobot-admin", ns)
            nautobot_token = data.get("api_token", "")
        except Exception:
            self.callback.on_log(
                "Could not retrieve nautobot-admin token; tests that need it will fail"
            )

        test_script = self._build_test_script(ns, release, nautobot_token)
        self.callback.on_log("Running integration tests...")
        try:
            all_output: list[str] = []
            for line in self._k8s.exec_command_streaming(
                ztp_pod, ns, ["python", "-c", test_script], container="sftp"
            ):
                self.callback.on_log(line)
                step.output.append(line)
                all_output.append(line)

            full = "\n".join(all_output).lower()
            if "failed" in full or "error" in full:
                step.output.append("Some integration tests failed")
                self._finish_step(step, StepStatus.FAILED)
            else:
                step.output.append("Integration tests passed")
                self._finish_step(step)
        except Exception as exc:
            for line in str(exc).splitlines()[:20]:
                self.callback.on_log(line)
            step.error = "Integration tests failed"
            self._finish_step(step, StepStatus.FAILED)

    def _collect_endpoints(self) -> list[str]:
        step = self._start_step("endpoints")
        hostname = self.config.cluster.hostname

        if not hostname:
            endpoints = ["No hostname configured -- use kubectl port-forward"]
        else:
            endpoints = self._build_endpoint_list(hostname)

        for ep in endpoints:
            step.output.append(ep)
            self.callback.on_log(ep)
        self._finish_step(step)
        return endpoints

    def _build_endpoint_list(self, hostname: str) -> list[str]:
        """Build the list of endpoint URLs from the config."""
        proto = "https" if self.config.infrastructure.tls else "http"
        svc = self.config.services
        eps: list[str] = []
        mapping: list[tuple[bool, str]] = [
            (svc.render, f"Render Service:   {proto}://render.{hostname}"),
            (svc.ztp, f"Network ZTP:      {proto}://ztp.{hostname}"),
            (svc.dhcp, f"Network DHCP:     {proto}://dhcp.{hostname}"),
            (svc.config_store, f"Config Store API: {proto}://config-store.{hostname}"),
            (svc.config_store, f"Config Store UI:  {proto}://{hostname}/configs"),
            (svc.temporal, f"Workflow API:     {proto}://workflow.{hostname}"),
            (svc.temporal, f"Workflow UI:      {proto}://{hostname}/workflows"),
            (svc.temporal, f"Temporal UI:      {proto}://temporal.{hostname}"),
            (svc.nautobot, f"Nautobot:         {proto}://nautobot.{hostname}"),
        ]
        for enabled, url in mapping:
            if enabled:
                eps.append(url)
        if not svc.nautobot and svc.external_nautobot_url:
            eps.append(f"Nautobot (ext):   {svc.external_nautobot_url}")
        return eps
