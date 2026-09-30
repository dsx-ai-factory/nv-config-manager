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
"""Trigger and wait for a rolling restart of the render-service Deployments.

See SRD §5.2, SADD §9.3 -- pod restart is acceptable for activation as long
as it's transparent to users (no failed renders, no downtime). Kubernetes'
own rolling-update mechanics provide that guarantee: a Deployment's old
ReplicaSet keeps serving until the new one is fully ready, so there is no
custom warm-pool/readiness protocol to build here.

The annotation-patch-to-force-a-rollout technique and the rollout-complete
check below mirror the proven implementation already used by the local-dev
installer (installer/src/nv_config_manager_installer/k8s.py
K8sClient.restart_deployment / _rollout_complete / wait_for_rollout) --
duplicated here in a smaller, render-service-scoped form rather than
sharing a module across the two separately-packaged components.
"""

from __future__ import annotations

import datetime
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.render.k8s_client import load_k8s_config

logger = get_logger(__name__, category=LogCategory.RENDER_API)


class RolloutError(Exception):
    """Raised when a triggered rollout does not complete within its timeout."""


def _rollout_complete(dep: client.V1Deployment) -> bool:
    """Whether a Deployment's rollout has fully converged.

    Same four-condition check as the installer's K8sClient._rollout_complete:
    the status generation has caught up, and updated/ready/available
    replicas have all reached the desired replica count.
    """
    status = dep.status
    if status is None:
        return False
    spec_replicas = dep.spec.replicas or 1
    return (
        (status.observed_generation or 0) >= (dep.metadata.generation or 0)
        and (status.updated_replicas or 0) >= spec_replicas
        and (status.ready_replicas or 0) >= spec_replicas
        and (status.available_replicas or 0) >= spec_replicas
    )


def _restart_deployment(apps_v1: client.AppsV1Api, name: str, namespace: str) -> int:
    """Patch the pod template annotation to force a new ReplicaSet.

    Returns the Deployment's new metadata.generation, so the caller can
    distinguish "this rollout" from a stale pre-restart snapshot when
    polling status.
    """
    now = datetime.datetime.now(datetime.UTC).isoformat()
    patch = {
        "spec": {
            "template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}
        }
    }
    result = apps_v1.patch_namespaced_deployment(name, namespace, patch)
    return result.metadata.generation or 0


def _wait_for_rollout(
    apps_v1: client.AppsV1Api,
    name: str,
    namespace: str,
    min_generation: int,
    deadline: float,
) -> None:
    """Poll a single Deployment's status until its rollout completes or the
    deadline passes. Raises RolloutError on timeout."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RolloutError(f"Deployment {name!r} did not become ready before timeout")
        dep = apps_v1.read_namespaced_deployment(name, namespace)
        if (dep.metadata.generation or 0) >= min_generation and _rollout_complete(dep):
            return
        time.sleep(min(2, remaining))


def trigger_and_wait_for_rollout(
    deployment_names: list[str],
    namespace: str,
    timeout_seconds: float = 300,
) -> None:
    """Restart each named Deployment and block until all of them are ready.

    Blocking/synchronous by design -- call via asyncio.to_thread from an
    async endpoint (matches this codebase's existing convention for
    wrapping blocking calls, e.g. render.py's
    `await asyncio.to_thread(renderer.render_entrypoints, ...)`).

    Raises RolloutError if any Deployment doesn't reach ready within
    timeout_seconds. Kubernetes itself keeps the previous ReplicaSet
    serving traffic throughout -- a raised RolloutError here means "the new
    plugin set never became available," not "renders stopped working."

    Callers whose own Deployment is one of `deployment_names` (the
    render-api pod handling the activation request, restarting itself)
    should exclude it here and restart it separately via
    `restart_deployment_no_wait()` -- see that function's docstring for
    why waiting on your own Deployment's rollout can never succeed.
    """
    load_k8s_config()
    apps_v1 = client.AppsV1Api()

    deadline = time.monotonic() + timeout_seconds
    generations: dict[str, int] = {}
    for name in deployment_names:
        try:
            generations[name] = _restart_deployment(apps_v1, name, namespace)
        except ApiException as exc:
            raise RolloutError(f"Failed to restart Deployment {name!r}: {exc}") from exc
        logger.info("Triggered rollout restart for Deployment %s/%s", namespace, name)

    for name, min_generation in generations.items():
        _wait_for_rollout(apps_v1, name, namespace, min_generation, deadline)
        logger.info("Deployment %s/%s rollout complete", namespace, name)


def restart_deployment_no_wait(name: str, namespace: str) -> None:
    """Trigger a rolling restart of `name` without waiting for it to complete.

    For the one Deployment that backs the pod issuing the restart (the
    render-api pod handling an activation request, restarting itself as
    part of activating a plugin set): that pod cannot reliably wait on its
    own rollout, or even finish its own follow-up bookkeeping afterward --
    once the replacement pod is ready, Kubernetes terminates this one,
    which can happen before this process gets to run its own next line of
    Python. So the caller must durably record the activation's outcome
    (see ActivationStateStore) *before* calling this, not after -- this
    call is the last thing that happens in the activation flow, and
    whether this process survives long enough to also return an HTTP
    response is not load-bearing for correctness (the caller's retry on
    the same request_id, per the endpoint's idempotency, gets the durably
    recorded outcome regardless).

    Raises RolloutError only if the restart couldn't even be triggered
    (e.g. an RBAC error) -- never for the rollout not completing, since
    this doesn't wait for that.
    """
    load_k8s_config()
    apps_v1 = client.AppsV1Api()
    try:
        _restart_deployment(apps_v1, name, namespace)
    except ApiException as exc:
        raise RolloutError(f"Failed to restart Deployment {name!r}: {exc}") from exc
    logger.info(
        "Triggered rollout restart for own Deployment %s/%s (not waiting on it)", namespace, name
    )
