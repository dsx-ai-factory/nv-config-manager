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
"""Kubernetes client wrapper for the NVIDIA Config Manager installer.

Provides a high-level, pure-Python interface over the ``kubernetes`` client
library.  Every method is safe to call from a thread (each instance carries its
own ``ApiClient``).  The wrapper is intentionally *thin* — it maps NVIDIA Config Manager
installer operations to Kubernetes API calls without adding abstraction beyond
what the deployer needs.

The ``kubernetes`` package is a pure-Python wheel with no C extensions, so it
packages cleanly into bundled distributions.
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import io
import json
import os
import socket
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import requests as _requests
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream as k8s_stream

LOADER_POD_IMAGE = "docker.io/library/busybox:1.36"


class PodExecError(RuntimeError):
    """A remote process failed or closed without an exit status."""

    def __init__(self, namespace: str, name: str, returncode: int | None) -> None:
        """Retain the exit status separately from potentially sensitive output."""
        self.returncode = returncode
        super().__init__(f"Command in pod {namespace}/{name} failed with exit {returncode}")


def kubectl_current_context(kubeconfig: str | Path | None = None) -> str | None:
    """Return ``kubectl config current-context`` output, or None on failure.

    This is the canonical answer to "which cluster is the user pointed at?".
    The Python kubernetes client's own merge logic disagrees with kubectl
    when KUBECONFIG lists multiple files, so we always defer to kubectl.
    """
    env = None
    if kubeconfig is not None:
        env = {**os.environ, "KUBECONFIG": str(kubeconfig)}
    try:
        result = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            env=env,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    ctx = result.stdout.strip()
    return ctx or None


def pin_kubeconfig_to_current_context() -> tuple[Path, str] | None:
    """Materialize a minified, single-context kubeconfig and pin KUBECONFIG to it.

    Why: when KUBECONFIG merges multiple files, every consumer (kubectl, helm,
    Python kubernetes client) can pick a different "current context" depending
    on its merge rules. Writing a flattened, ``--minify`` kubeconfig containing
    only the active context — and pointing ``$KUBECONFIG`` at that single
    file — eliminates the ambiguity for every subprocess we spawn for the
    rest of the installer run.

    Returns ``(path_to_temp_kubeconfig, context_name)`` on success, or None if
    kubectl is unavailable or the active context cannot be determined.
    The caller is responsible for deleting the file when the run finishes.
    """
    ctx = kubectl_current_context()
    if not ctx:
        return None
    try:
        result = subprocess.run(
            [
                "kubectl",
                "config",
                "view",
                "--minify",
                "--flatten",
                "--raw",
                f"--context={ctx}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None

    fd, path_str = tempfile.mkstemp(prefix="nv-config-manager-kubeconfig-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(result.stdout)
        os.chmod(path_str, 0o600)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(path_str)
        return None

    os.environ["KUBECONFIG"] = path_str
    return Path(path_str), ctx


class K8sClient:
    """High-level wrapper around the ``kubernetes`` Python client."""

    def __init__(
        self,
        context: str | None = None,
        kubeconfig: str | Path | None = None,
    ) -> None:
        # The Python kubernetes client and kubectl/helm disagree on which
        # context is "current" when KUBECONFIG merges multiple files that each
        # set their own current-context: kubectl picks the FIRST file's value,
        # the Python client picks the LAST. That mismatch has caused the
        # installer to create namespaces/secrets in the wrong cluster while
        # helm later operates on a different one. To keep every code path
        # honest we always pin Python to the exact context kubectl reports,
        # falling back only if kubectl is unavailable (e.g. in-cluster auth).
        kubeconfig_value = str(kubeconfig) if kubeconfig is not None else None
        if kubeconfig_value is None:
            kubeconfig_value = os.environ.get("KUBECONFIG") or None
        self.kubeconfig: str | None = kubeconfig_value
        if context is None:
            context = kubectl_current_context(kubeconfig_value)
        if context:
            if kubeconfig_value:
                config.load_kube_config(config_file=kubeconfig_value, context=context)
            else:
                config.load_kube_config(context=context)
        elif kubeconfig_value:
            config.load_kube_config(config_file=kubeconfig_value)
        else:
            config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.coordination_v1 = client.CoordinationV1Api()
        # Use the context we explicitly bound to as the source of truth.
        # ``config.list_kube_config_contexts()`` is unreliable here: the
        # kubernetes module captures the KUBECONFIG default path at import
        # time, so it can return stale results that contradict the cluster
        # the API client is actually talking to.
        self.active_context: str | None = context
        try:
            self.api_server: str | None = self.v1.api_client.configuration.host
        except Exception:
            self.api_server = None

    def _kubectl_env(self) -> dict[str, str] | None:
        """Return a subprocess environment pinned to this client's kubeconfig."""
        if self.kubeconfig is None:
            return None
        return {**os.environ, "KUBECONFIG": self.kubeconfig}

    # -- Cluster connectivity -------------------------------------------------

    def check_connectivity(self) -> bool:
        try:
            self.v1.list_namespace(limit=1)
            return True
        except Exception:
            return False

    # -- Namespace operations -------------------------------------------------

    def namespace_exists(self, name: str) -> bool:
        try:
            self.v1.read_namespace(name)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def namespace_phase(self, name: str) -> str | None:
        """Return the namespace phase ("Active", "Terminating") or None if absent."""
        try:
            ns = self.v1.read_namespace(name)
        except ApiException as e:
            if e.status == 404:
                return None
            raise
        return getattr(ns.status, "phase", None) if ns.status else None

    def create_namespace(self, name: str) -> None:
        body = client.V1Namespace(metadata=client.V1ObjectMeta(name=name))
        self.v1.create_namespace(body)

    def ensure_namespace(self, name: str) -> bool:
        """Return True if the namespace was created, False if it already existed."""
        if self.namespace_exists(name):
            return False
        self.create_namespace(name)
        return True

    # -- Secret operations ----------------------------------------------------

    def secret_exists(self, name: str, namespace: str) -> bool:
        try:
            self.v1.read_namespaced_secret(name, namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def delete_secret(self, name: str, namespace: str) -> bool:
        """Delete a secret. Returns True if deleted, False if not found."""
        try:
            self.v1.delete_namespaced_secret(name, namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def read_secret_data(self, name: str, namespace: str) -> dict[str, str]:
        """Read and base64-decode all keys from a secret. Returns {} on 404."""
        try:
            secret = self.v1.read_namespaced_secret(name, namespace)
            if not secret.data:
                return {}
            return {k: base64.b64decode(v).decode() for k, v in secret.data.items()}
        except ApiException as e:
            if e.status == 404:
                return {}
            raise

    def apply_secret(
        self,
        name: str,
        namespace: str,
        string_data: dict[str, str],
        secret_type: str = "Opaque",
    ) -> None:
        """Create or replace a secret with plaintext string data."""
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            string_data=string_data,
            type=secret_type,
        )
        try:
            self.v1.create_namespaced_secret(namespace, body)
        except ApiException as e:
            if e.status == 409:
                self.v1.replace_namespaced_secret(name, namespace, body)
            else:
                raise

    def apply_docker_registry_secret(
        self,
        name: str,
        namespace: str,
        server: str,
        username: str,
        password: str,
    ) -> None:
        """Create or replace a docker-registry secret."""
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        docker_config = json.dumps(
            {"auths": {server: {"username": username, "password": password, "auth": auth}}}
        )
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            data={".dockerconfigjson": base64.b64encode(docker_config.encode()).decode()},
            type="kubernetes.io/dockerconfigjson",
        )
        try:
            self.v1.create_namespaced_secret(namespace, body)
        except ApiException as e:
            if e.status == 409:
                self.v1.replace_namespaced_secret(name, namespace, body)
            else:
                raise

    def apply_file_secret(
        self,
        name: str,
        namespace: str,
        file_data: dict[str, bytes],
    ) -> None:
        """Create or replace a secret with binary file data (e.g. from-file)."""
        encoded = {k: base64.b64encode(v).decode() for k, v in file_data.items()}
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            data=encoded,
            type="Opaque",
        )
        try:
            self.v1.create_namespaced_secret(namespace, body)
        except ApiException as e:
            if e.status == 409:
                self.v1.replace_namespaced_secret(name, namespace, body)
            else:
                raise

    # -- PVC operations -------------------------------------------------------

    def _pods_using_pvc(self, name: str, namespace: str) -> list[str]:
        """Return owner references (kind/name) for pods that mount the named PVC."""
        owners: list[str] = []
        try:
            for pod in self.v1.list_namespaced_pod(namespace).items:
                volumes = (pod.spec.volumes or []) if pod.spec else []
                if not any(
                    v.persistent_volume_claim and v.persistent_volume_claim.claim_name == name
                    for v in volumes
                ):
                    continue
                refs = (pod.metadata.owner_references or []) if pod.metadata else []
                if refs:
                    owners.extend(f"{r.kind}/{r.name}" for r in refs)
                elif pod.metadata and pod.metadata.name:
                    owners.append(f"Pod/{pod.metadata.name}")
        except ApiException:
            pass
        return list(dict.fromkeys(owners))  # deduplicate, preserve order

    def _wait_for_pvc_deletion(self, name: str, namespace: str, timeout: int = 60) -> None:
        """Poll until the named PVC disappears or raise TimeoutError."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.v1.read_namespaced_persistent_volume_claim(name, namespace)
                time.sleep(2)
            except ApiException as e:
                if e.status == 404:
                    return
                raise
        blockers = self._pods_using_pvc(name, namespace)
        hint = (
            f"Scale down {', '.join(blockers)} to release the volume, "
            "then re-run the installer once the PVC is gone."
            if blockers
            else "Identify which pods are still mounting it, scale them down, "
            "then re-run the installer once the PVC is gone."
        )
        raise TimeoutError(
            f"PVC {name} did not terminate within {timeout}s — "
            f"another pod is still mounting it. {hint}"
        )

    def _raise_terminating_pvc_error(self, name: str, namespace: str) -> None:
        """Raise RuntimeError with a targeted hint for a PVC stuck in Terminating state."""
        blockers = self._pods_using_pvc(name, namespace)
        if blockers:
            hint = f"Scale down {', '.join(blockers)} to release the volume."
        else:
            hint = "Identify which pods are still mounting it and scale them down."
        raise RuntimeError(f"PVC {name} is stuck in Terminating state. {hint}")

    def _pvc_spec_matches(
        self,
        existing: Any,
        access_mode: str,
        storage_class: str | None,
    ) -> bool:
        existing_mode = (
            existing.spec.access_modes[0] if existing.spec.access_modes else "ReadWriteOnce"
        )
        sc_matches = (
            storage_class is None or (existing.spec.storage_class_name or "") == storage_class
        )
        return existing_mode == access_mode and sc_matches

    def ensure_pvc(
        self,
        name: str,
        namespace: str,
        size: str = "1Gi",
        access_mode: str = "ReadWriteOnce",
        storage_class: str | None = None,
        *,
        allow_recreate: bool = True,
    ) -> bool:
        """Create PVC if it does not already exist.

        When *allow_recreate* is True (default): if the PVC exists but its
        access mode or storage class no longer matches the desired spec, it is
        deleted and recreated.  A Terminating PVC is waited out and replaced.

        When *allow_recreate* is False: only create if the PVC is absent.
        A spec mismatch is silently ignored (content callers never change spec).
        A Terminating PVC raises immediately with a clear recovery message.

        Returns True if the PVC was created or recreated, False if it already
        existed with a matching spec.
        """
        try:
            existing = self.v1.read_namespaced_persistent_volume_claim(name, namespace)

            if existing.metadata and existing.metadata.deletion_timestamp:
                if not allow_recreate:
                    self._raise_terminating_pvc_error(name, namespace)
                # allow_recreate=True: wait for the previous deletion to finish.
                self._wait_for_pvc_deletion(name, namespace)
                # Fall through to create a fresh PVC below.
            else:
                if not allow_recreate:
                    return False
                if self._pvc_spec_matches(existing, access_mode, storage_class):
                    return False
                # Spec changed — delete and fall through to recreate.
                self.v1.delete_namespaced_persistent_volume_claim(name, namespace)
                self._wait_for_pvc_deletion(name, namespace)
        except ApiException as e:
            if e.status != 404:
                raise

        spec = client.V1PersistentVolumeClaimSpec(
            access_modes=[access_mode],
            resources=client.V1VolumeResourceRequirements(requests={"storage": size}),
        )
        if storage_class:
            spec.storage_class_name = storage_class

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            spec=spec,
        )
        self.v1.create_namespaced_persistent_volume_claim(namespace, body)
        return True

    def list_nodes(self) -> list[tuple[str, dict[str, str]]]:
        """Return (node_name, labels) for every node in the cluster."""
        nodes = self.v1.list_node().items
        return [
            (n.metadata.name, dict(n.metadata.labels or {}))
            for n in sorted(nodes, key=lambda n: n.metadata.name)
        ]

    def get_pvc_annotation(self, name: str, namespace: str, annotation: str) -> str:
        try:
            pvc = self.v1.read_namespaced_persistent_volume_claim(name, namespace)
            if pvc.metadata and pvc.metadata.annotations:
                return pvc.metadata.annotations.get(annotation, "")
            return ""
        except ApiException:
            return ""

    def pvc_exists(self, name: str, namespace: str) -> bool:
        """Return whether a PersistentVolumeClaim exists.

        Unlike :meth:`get_pvc_annotation`, this deliberately preserves API
        errors other than ``404`` so callers do not mistake an authorization or
        connectivity issue for an absent PVC.
        """
        try:
            self.v1.read_namespaced_persistent_volume_claim(name, namespace)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise

    def annotate_pvc(self, name: str, namespace: str, annotation: str, value: str) -> None:
        body = {"metadata": {"annotations": {annotation: value}}}
        self.v1.patch_namespaced_persistent_volume_claim(name, namespace, body)

    # -- Lease operations -----------------------------------------------------

    @staticmethod
    def _lease_is_expired(lease: client.V1Lease, now: datetime.datetime) -> bool:
        """Return whether a Lease may safely be claimed by another holder."""
        spec = lease.spec
        if spec is None or not spec.lease_duration_seconds:
            return False
        renewal = spec.renew_time or spec.acquire_time
        if renewal is None:
            return False
        if renewal.tzinfo is None:
            renewal = renewal.replace(tzinfo=datetime.UTC)
        return renewal + datetime.timedelta(seconds=spec.lease_duration_seconds) <= now

    def acquire_lease(
        self,
        name: str,
        namespace: str,
        holder_identity: str,
        *,
        duration_seconds: int,
    ) -> None:
        """Acquire a namespace-scoped Lease or fail if another update holds it."""
        now = datetime.datetime.now(datetime.UTC)
        lease = client.V1Lease(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            spec=client.V1LeaseSpec(
                holder_identity=holder_identity,
                lease_duration_seconds=duration_seconds,
                acquire_time=now,
                renew_time=now,
            ),
        )
        try:
            self.coordination_v1.create_namespaced_lease(namespace, lease)
            return
        except ApiException as exc:
            if exc.status != 409:
                raise

        existing = self.coordination_v1.read_namespaced_lease(name, namespace)
        if not self._lease_is_expired(existing, now):
            holder = getattr(existing.spec, "holder_identity", None) or "unknown"
            raise RuntimeError(
                f"Another NVCM PVC update is already in progress for '{name}' (holder: {holder})."
            )

        existing.spec = lease.spec
        try:
            self.coordination_v1.replace_namespaced_lease(name, namespace, existing)
        except ApiException as exc:
            if exc.status == 409:
                raise RuntimeError(
                    f"Another NVCM PVC update acquired lease '{name}' before this run."
                ) from exc
            raise

    def release_lease(self, name: str, namespace: str, holder_identity: str) -> None:
        """Release a Lease only when it is still held by this installer run."""
        try:
            lease = self.coordination_v1.read_namespaced_lease(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
        if getattr(lease.spec, "holder_identity", None) != holder_identity:
            return
        resource_version = getattr(lease.metadata, "resource_version", None)
        body = client.V1DeleteOptions(
            preconditions=client.V1Preconditions(resource_version=resource_version)
        )
        try:
            self.coordination_v1.delete_namespaced_lease(name, namespace, body=body)
        except ApiException as exc:
            if exc.status not in {404, 409}:
                raise

    def renew_lease(
        self,
        name: str,
        namespace: str,
        holder_identity: str,
        *,
        duration_seconds: int,
    ) -> None:
        """Renew a held Lease, failing if another updater has claimed it."""
        try:
            lease = self.coordination_v1.read_namespaced_lease(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise RuntimeError(f"PVC update lease '{name}' no longer exists.") from exc
            raise

        if lease.spec is None or lease.spec.holder_identity != holder_identity:
            raise RuntimeError(f"PVC update lease '{name}' is no longer held by this run.")
        lease.spec.renew_time = datetime.datetime.now(datetime.UTC)
        lease.spec.lease_duration_seconds = duration_seconds
        try:
            self.coordination_v1.replace_namespaced_lease(name, namespace, lease)
        except ApiException as exc:
            if exc.status == 409:
                raise RuntimeError(f"PVC update lease '{name}' changed during renewal.") from exc
            raise

    # -- Pod operations -------------------------------------------------------

    def delete_pod(self, name: str, namespace: str, wait: bool = False) -> None:
        try:
            self.v1.delete_namespaced_pod(
                name,
                namespace,
                grace_period_seconds=0,
                propagation_policy="Background" if not wait else "Foreground",
            )
        except ApiException as e:
            if e.status == 404:
                return
            raise

    def wait_for_pod_gone(self, name: str, namespace: str, timeout: int = 30) -> None:
        """Block until a pod no longer exists (404), up to *timeout* seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.v1.read_namespaced_pod(name, namespace)
                time.sleep(1)
            except ApiException as e:
                if e.status == 404:
                    return
                raise
        raise TimeoutError(
            f"Pod {name} in namespace {namespace} did not terminate within {timeout}s"
        )

    def create_loader_pod(
        self,
        name: str,
        namespace: str,
        pvc_name: str,
        mount_path: str,
        image: str = LOADER_POD_IMAGE,
        node_selector: dict[str, str] | None = None,
        node_name: str | None = None,
    ) -> None:
        """Create a pod that mounts a PVC and remains alive until explicitly deleted."""
        body = client.V1Pod(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="loader",
                        image=image,
                        command=["sh", "-c", "while true; do sleep 3600; done"],
                        volume_mounts=[client.V1VolumeMount(name="data", mount_path=mount_path)],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="data",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=pvc_name
                        ),
                    )
                ],
                node_selector=node_selector or None,
                node_name=node_name,
            ),
        )
        self.v1.create_namespaced_pod(namespace, body)

    def get_pvc_mounted_node(self, name: str, namespace: str) -> str | None:
        """Return one node currently running a pod that mounts *name*, if any."""
        pods = self.v1.list_namespaced_pod(namespace).items
        for pod in pods:
            if not pod.status or pod.status.phase != "Running" or not pod.spec:
                continue
            node_name = pod.spec.node_name
            if not node_name:
                continue
            for volume in pod.spec.volumes or []:
                claim = volume.persistent_volume_claim
                if claim and claim.claim_name == name:
                    return str(node_name)
        return None

    @staticmethod
    def _short_status_text(value: str, *, limit: int = 180) -> str:
        value = " ".join(value.split())
        if len(value) <= limit:
            return value
        return f"{value[: limit - 3]}..."

    @classmethod
    def _container_status_details(
        cls, statuses: list[Any] | None, *, prefix: str = "container"
    ) -> list[str]:
        details: list[str] = []
        for status in statuses or []:
            name = status.name or "<unknown>"
            state = status.state
            state_text = "unknown"
            if state and state.waiting:
                reason = state.waiting.reason or "Waiting"
                message = state.waiting.message or ""
                state_text = reason
                if message:
                    state_text = f"{state_text} ({cls._short_status_text(message)})"
            elif state and state.terminated:
                reason = state.terminated.reason or "Terminated"
                state_text = f"{reason} exitCode={state.terminated.exit_code}"
                if state.terminated.message:
                    state_text = (
                        f"{state_text} ({cls._short_status_text(state.terminated.message)})"
                    )
            elif state and state.running:
                state_text = "Running"

            ready = "ready" if status.ready else "not ready"
            details.append(f"{prefix} {name}: {state_text}, {ready}")
        return details

    @classmethod
    def _pod_condition_details(cls, pod: client.V1Pod) -> list[str]:
        conditions = pod.status.conditions if pod.status and pod.status.conditions else []
        details: list[str] = []
        for cond in conditions:
            detail = f"condition {cond.type}={cond.status}"
            if cond.reason:
                detail = f"{detail} ({cond.reason})"
            if cond.message:
                detail = f"{detail}: {cls._short_status_text(cond.message)}"
            details.append(detail)
        return details

    def _pod_event_details(self, name: str, namespace: str) -> list[str]:
        try:
            events = self.v1.list_namespaced_event(
                namespace,
                field_selector=f"involvedObject.kind=Pod,involvedObject.name={name}",
            ).items
        except ApiException:
            return []

        def event_sort_key(event: Any) -> str:
            ts = event.last_timestamp or event.event_time or event.first_timestamp
            return ts.isoformat() if ts else ""

        details: list[str] = []
        for event in sorted(events, key=event_sort_key)[-5:]:
            reason = event.reason or "Event"
            message = self._short_status_text(event.message or "")
            details.append(f"event {reason}: {message}" if message else f"event {reason}")
        return details

    def _pod_not_ready_details(self, name: str, namespace: str) -> str:
        try:
            pod = self.v1.read_namespaced_pod(name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return "pod no longer exists"
            return f"could not read pod status: {exc}"

        details: list[str] = []
        if pod.status:
            if pod.status.phase:
                details.append(f"phase={pod.status.phase}")
            if pod.status.reason:
                details.append(f"reason={pod.status.reason}")
            if pod.status.message:
                details.append(f"message={self._short_status_text(pod.status.message)}")
            details.extend(self._pod_condition_details(pod))
            details.extend(
                self._container_status_details(
                    pod.status.init_container_statuses,
                    prefix="init container",
                )
            )
            details.extend(self._container_status_details(pod.status.container_statuses))
        details.extend(self._pod_event_details(name, namespace))
        return "; ".join(details) if details else "no pod status details available"

    def wait_for_pod_ready(self, name: str, namespace: str, timeout: int = 120) -> None:
        """Block until a pod's Ready condition is True."""
        w = watch.Watch()
        try:
            for event in w.stream(
                self.v1.list_namespaced_pod,
                namespace,
                field_selector=f"metadata.name={name}",
                timeout_seconds=timeout,
            ):
                pod: client.V1Pod = event["object"]
                if pod.status and pod.status.conditions:
                    for cond in pod.status.conditions:
                        if cond.type == "Ready" and cond.status == "True":
                            return
        finally:
            w.stop()
        details = self._pod_not_ready_details(name, namespace)
        raise TimeoutError(f"Pod {name} not ready within {timeout}s: {details}")

    # -- Pod exec and file copy -----------------------------------------------

    def exec_command(
        self,
        name: str,
        namespace: str,
        command: list[str],
        container: str | None = None,
        timeout: int = 1_800,
    ) -> str:
        """Execute a command in a running pod, raising when it fails or times out."""
        kwargs: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "command": command,
            "stderr": True,
            "stdout": True,
            "stdin": False,
            "tty": False,
            "_preload_content": False,
        }
        if container:
            kwargs["container"] = container
        deadline = time.monotonic() + timeout
        kwargs["_request_timeout"] = timeout
        ws = k8s_stream(self.v1.connect_get_namespaced_pod_exec, **kwargs)
        try:
            while ws.is_open():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Command in pod {namespace}/{name} did not complete within {timeout}s"
                    )
                ws.update(timeout=min(1, remaining))

            returncode = ws.returncode
            output = ws.read_all()
            if returncode != 0:
                detail = str(output).strip()
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    f"Command in pod {namespace}/{name} failed with exit {returncode}{suffix}"
                )
            return str(output)
        finally:
            ws.close()

    def exec_command_streaming(
        self,
        name: str,
        namespace: str,
        command: list[str],
        container: str | None = None,
        stdin: str | None = None,
    ) -> Generator[str]:
        """Yield command output as it arrives, raising unless the remote exit is zero."""
        kwargs: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "command": command,
            "stderr": True,
            "stdout": True,
            "stdin": stdin is not None,
            "tty": False,
            "_preload_content": False,
        }
        if container:
            kwargs["container"] = container
        ws = k8s_stream(self.v1.connect_get_namespaced_pod_exec, **kwargs)
        buf = ""
        try:
            if stdin is not None:
                ws.write_stdin(stdin)
            while ws.is_open():
                ws.update(timeout=1)
                stdout = ws.read_stdout(timeout=0)
                if stdout:
                    buf += stdout
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        yield line
                stderr = ws.read_stderr(timeout=0)
                if stderr:
                    buf += stderr
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        yield line
            if buf.strip():
                yield buf
            returncode = ws.returncode
            if returncode != 0:
                raise PodExecError(namespace, name, returncode)
        finally:
            ws.close()

    def copy_to_pod(
        self,
        local_path: str,
        name: str,
        namespace: str,
        remote_path: str,
        container: str | None = None,
    ) -> None:
        """Copy a local file into a running pod using tar stream through exec.

        Equivalent to ``kubectl cp <local> <ns>/<pod>:<remote>``.
        Raises ``RuntimeError`` if the in-pod tar extraction fails.

        For large files (>50 MB) this falls back to a subprocess ``kubectl cp``
        call because the Kubernetes Python client websocket does not handle
        backpressure well and tends to break the pipe on sustained writes.
        """
        src = Path(local_path)
        if not src.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        _LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MB
        if src.stat().st_size > _LARGE_FILE_THRESHOLD:
            self._copy_to_pod_kubectl(str(src), name, namespace, remote_path, container)
            return

        remote_dir = str(Path(remote_path).parent)
        remote_name = Path(remote_path).name

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            tf.add(str(src), arcname=remote_name)
        tar_bytes = tar_buf.getvalue()

        kwargs: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "command": ["tar", "xf", "-", "-C", remote_dir],
            "stderr": True,
            "stdout": True,
            "stdin": True,
            "tty": False,
            "_preload_content": False,
        }
        if container:
            kwargs["container"] = container

        resp = k8s_stream(self.v1.connect_get_namespaced_pod_exec, **kwargs)

        chunk_size = 64 * 1024  # 64 KB
        while tar_bytes:
            chunk = tar_bytes[:chunk_size]
            tar_bytes = tar_bytes[chunk_size:]
            resp.write_stdin(chunk)
            resp.update(timeout=0)
        resp.close()

        try:
            stderr_out = resp.read_stderr(timeout=0) if hasattr(resp, "read_stderr") else ""
        except (TypeError, AttributeError):
            stderr_out = ""
        try:
            rc = resp.returncode if hasattr(resp, "returncode") else None
        except (TypeError, KeyError, AttributeError):
            rc = None
        if rc is not None and rc != 0:
            raise RuntimeError(f"copy_to_pod: tar extraction failed (exit {rc}): {stderr_out}")

        self._verify_remote_path(name, namespace, remote_path, container)

    def _copy_to_pod_kubectl(
        self,
        local_path: str,
        name: str,
        namespace: str,
        remote_path: str,
        container: str | None = None,
    ) -> None:
        """Use subprocess kubectl cp for large files where websocket is unreliable."""
        target = f"{namespace}/{name}:{remote_path}"
        cmd = ["kubectl", "cp", local_path, target, "-n", namespace]
        if container:
            cmd += ["-c", container]

        result = subprocess.run(
            cmd,
            capture_output=True,
            env=self._kubectl_env(),
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"kubectl cp failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        self._verify_remote_path(name, namespace, remote_path, container)

    def _verify_remote_path(
        self,
        name: str,
        namespace: str,
        remote_path: str,
        container: str | None = None,
    ) -> None:
        """Check that a file exists in the pod after copy."""
        verify_kwargs: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "command": ["test", "-e", remote_path],
            "stderr": True,
            "stdout": True,
            "stdin": False,
            "tty": False,
        }
        if container:
            verify_kwargs["container"] = container
        try:
            k8s_stream(self.v1.connect_get_namespaced_pod_exec, **verify_kwargs)
        except ApiException as exc:
            raise RuntimeError(
                f"copy_to_pod: verification failed — {remote_path} not found in pod: {exc}"
            ) from exc

    # -- Deployment operations ------------------------------------------------

    def list_deployment_names(self, namespace: str, label_selector: str = "") -> list[str]:
        result = self.apps_v1.list_namespaced_deployment(namespace, label_selector=label_selector)
        return [d.metadata.name for d in result.items]

    def get_deployment_host_ports(self, name: str, namespace: str) -> set[int]:
        """Return hostPorts already configured on the first container of a deployment."""
        dep = self.apps_v1.read_namespaced_deployment(name, namespace)
        containers = dep.spec.template.spec.containers
        if not containers:
            return set()
        container = containers[0]
        ports = container.ports or []
        return {p.host_port for p in ports if p.host_port}

    def patch_deployment_json(self, name: str, namespace: str, patch: list[dict[str, Any]]) -> None:
        """Apply a JSON-patch (RFC 6902) to a deployment."""
        self.apps_v1.patch_namespaced_deployment(name, namespace, patch)

    def delete_pods_by_label(self, namespace: str, label_selector: str) -> None:
        self.v1.delete_collection_namespaced_pod(
            namespace, label_selector=label_selector, grace_period_seconds=0
        )

    def wait_for_deployment_available(self, name: str, namespace: str, timeout: int = 120) -> None:
        """Block until a deployment's Available condition is True."""
        w = watch.Watch()
        try:
            for event in w.stream(
                self.apps_v1.list_namespaced_deployment,
                namespace,
                field_selector=f"metadata.name={name}",
                timeout_seconds=timeout,
            ):
                dep: client.V1Deployment = event["object"]
                if dep.status and dep.status.conditions:
                    for cond in dep.status.conditions:
                        if cond.type == "Available" and cond.status == "True":
                            return
        finally:
            w.stop()
        raise TimeoutError(f"Deployment {name} not available within {timeout}s")

    def restart_deployment(self, name: str, namespace: str) -> int:
        """Trigger a rollout restart by patching the pod template annotation.

        Returns the new metadata.generation so callers can pass it to
        wait_for_rollout to avoid treating a stale pre-restart event as done.
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        patch = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}
                }
            }
        }
        result = self.apps_v1.patch_namespaced_deployment(name, namespace, patch)
        return result.metadata.generation or 0

    @staticmethod
    def _rollout_complete(dep: Any) -> bool:
        """Check whether a deployment rollout has fully converged."""
        status = dep.status
        if status is None:
            return False
        spec_replicas = dep.spec.replicas if dep.spec.replicas is not None else 1
        if spec_replicas == 0:
            return (status.observed_generation or 0) >= (dep.metadata.generation or 0) and (
                status.replicas or 0
            ) == 0
        return (
            (status.observed_generation or 0) >= (dep.metadata.generation or 0)
            and (status.updated_replicas or 0) >= spec_replicas
            and (status.ready_replicas or 0) >= spec_replicas
            and (status.available_replicas or 0) >= spec_replicas
        )

    def _handle_rollout_event(
        self,
        event: dict[str, Any],
        name: str,
        min_generation: int,
        on_message: Any | None,
    ) -> bool:
        """Process one Watch event; returns True when the rollout is complete."""
        dep: client.V1Deployment = event["object"]
        if min_generation and (dep.metadata.generation or 0) < min_generation:
            return False
        status = dep.status
        if status is None:
            return False
        spec_replicas = dep.spec.replicas if dep.spec.replicas is not None else 1
        ready = status.ready_replicas or 0
        updated = status.updated_replicas or 0
        available = status.available_replicas or 0
        if on_message:
            on_message(
                f"  {name}: {ready}/{spec_replicas} ready, {updated} updated, {available} available"
            )
        if self._rollout_complete(dep):
            if on_message:
                on_message(f"  {name}: rollout complete")
            return True
        return False

    def wait_for_rollout(
        self,
        name: str,
        namespace: str,
        timeout: int = 360,
        on_message: Any | None = None,
        min_generation: int = 0,
    ) -> None:
        """Wait for a deployment rollout to complete, emitting progress messages.

        min_generation: if non-zero, ignore events where metadata.generation is
        still below this value (guards against the Watch returning a stale
        pre-restart snapshot before Kubernetes propagates the generation bump).
        """
        deadline = time.monotonic() + timeout
        w = watch.Watch()
        try:
            for event in w.stream(
                self.apps_v1.list_namespaced_deployment,
                namespace,
                field_selector=f"metadata.name={name}",
                timeout_seconds=timeout,
            ):
                if time.monotonic() > deadline:
                    break
                if self._handle_rollout_event(event, name, min_generation, on_message):
                    return
        finally:
            w.stop()

        raise TimeoutError(f"Rollout of {name} not complete within {timeout}s")

    # -- Port-forward for in-cluster HTTP calls --------------------------------

    @contextmanager
    def port_forward(
        self,
        service: str,
        namespace: str,
        remote_port: int = 80,
    ) -> Generator[int]:
        """Context manager that runs ``kubectl port-forward`` and yields the local port.

        Uses kubectl subprocess because the Python client's port-forward
        requires websocket protocol upgrades that are fragile across cluster
        providers.
        """
        local_port = _find_free_port()
        proc = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                f"svc/{service}",
                f"{local_port}:{remote_port}",
                "-n",
                namespace,
            ],
            env=self._kubectl_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_port(local_port, timeout=15)
            yield local_port
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class ServiceProxy:
    """HTTP client that talks to a Kubernetes service via port-forward.

    Replaces the old approach of spinning up temporary ``curlimages/curl`` pods
    for each request.  Keeps a single ``kubectl port-forward`` alive for the
    lifetime of the proxy.
    """

    def __init__(self, k8s: K8sClient, service: str, namespace: str, port: int = 80) -> None:
        self._k8s = k8s
        self._service = service
        self._namespace = namespace
        self._remote_port = port
        self._local_port: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._proc is not None:
            return
        self._local_port = _find_free_port()
        self._proc = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                f"svc/{self._service}",
                f"{self._local_port}:{self._remote_port}",
                "-n",
                self._namespace,
            ],
            env=self._k8s._kubectl_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_port(self._local_port, timeout=15)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
            self._local_port = None

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: str | None = None,
        timeout: int = 30,
    ) -> str:
        """Make an HTTP request through the port-forward. Returns the response body."""
        if self._local_port is None:
            raise RuntimeError("ServiceProxy not started — call .start() first")

        url = f"http://localhost:{self._local_port}/{path.lstrip('/')}"
        try:
            resp = _requests.request(method, url, headers=headers, data=data, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except _requests.HTTPError as e:
            body = e.response.text[:500] if e.response is not None else ""
            code = e.response.status_code if e.response is not None else "?"
            raise RuntimeError(f"HTTP {code} from {method} {path}: {body}") from e
        except _requests.ConnectionError as e:
            raise RuntimeError(f"Connection error to {url}: {e}") from e

    def __enter__(self) -> ServiceProxy:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: int = 15) -> None:
    """Poll until a TCP port on localhost is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise TimeoutError(f"Port {port} did not open within {timeout}s")
