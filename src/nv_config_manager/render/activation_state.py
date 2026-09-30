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
"""Persisted record of the active plugin-set digest and in-flight/completed
activation requests.

See SRD FR-242 ("NVCM's database -- not Template Preview's -- is
authoritative for distinguishing 'requested' from 'confirmed ready'") and
SADD §9.3/§9.4.

Backed by a Kubernetes ConfigMap, not a new database. This state has to
survive the render-service pods being restarted, because those pods are
themselves what gets restarted as part of fulfilling an activation request
-- in-memory state in the API pod handling the request would be lost
mid-rollout. A ConfigMap in etcd is unaffected by any render-service pod
restarting, needs no new provisioned infrastructure (unlike a database),
and this is a small, low-write-frequency piece of state -- not a fit for
introducing a new Postgres dependency just for this.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.render.k8s_client import load_k8s_config

logger = get_logger(__name__, category=LogCategory.RENDER_API)

_DATA_KEY = "state.json"
_MAX_CONFLICT_RETRIES = 5

_EMPTY_STATE: dict[str, Any] = {
    "active_digest": None,
    "in_progress_digest": None,
    # Per-plugin artifact locations for active_digest -- what the
    # artifact-fetch init container installs on pod startup.
    "active_artifacts": [],
    "requests": {},  # request_id -> {digest, status, updated_at}
}


class ActivationStateStore:
    """Read-modify-write access to the activation-state ConfigMap.

    Not thread-safe across processes by locking -- relies on the
    ConfigMap's resourceVersion for optimistic concurrency (a conflicting
    concurrent write raises a 409 from the API server, which `update()`
    retries with a fresh read). Fine for this use case: activation requests
    are rare and human-paced, not a high-throughput write path.
    """

    def __init__(self, name: str, namespace: str) -> None:
        self._name = name
        self._namespace = namespace

    def _core_v1(self) -> client.CoreV1Api:
        load_k8s_config()
        return client.CoreV1Api()

    def get(self) -> dict[str, Any]:
        """Return the current state, or the empty default if the ConfigMap
        doesn't exist yet (first activation ever in this deployment)."""
        core_v1 = self._core_v1()
        try:
            cm = core_v1.read_namespaced_config_map(self._name, self._namespace)
        except ApiException as exc:
            if exc.status == 404:
                return dict(_EMPTY_STATE)
            raise
        raw = (cm.data or {}).get(_DATA_KEY)
        if not raw:
            return dict(_EMPTY_STATE)
        return json.loads(raw)

    def update(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Read-modify-write: apply `mutator` to the current state and persist
        the result, retrying on a conflicting concurrent write.

        `mutator` receives the current state dict and returns the new state
        dict (may mutate and return the same object).
        """
        core_v1 = self._core_v1()
        for attempt in range(_MAX_CONFLICT_RETRIES):
            try:
                cm = core_v1.read_namespaced_config_map(self._name, self._namespace)
                exists = True
            except ApiException as exc:
                if exc.status != 404:
                    raise
                cm = None
                exists = False

            current = dict(_EMPTY_STATE)
            if cm is not None:
                raw = (cm.data or {}).get(_DATA_KEY)
                if raw:
                    current = json.loads(raw)

            new_state = mutator(current)
            body = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(
                    name=self._name,
                    resource_version=cm.metadata.resource_version if cm else None,
                ),
                data={_DATA_KEY: json.dumps(new_state)},
            )

            try:
                if exists:
                    core_v1.replace_namespaced_config_map(self._name, self._namespace, body)
                else:
                    body.metadata.resource_version = None
                    core_v1.create_namespaced_config_map(self._namespace, body)
                return new_state
            except ApiException as exc:
                if exc.status in (409, 422) and attempt < _MAX_CONFLICT_RETRIES - 1:
                    logger.info(
                        "Activation state ConfigMap write conflict, retrying (attempt %d)",
                        attempt + 1,
                    )
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("Unreachable: retry loop exited without returning or raising")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
