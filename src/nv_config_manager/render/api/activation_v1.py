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
"""V1 Activation API -- internal endpoint Template Preview calls to activate
a dynamic template plugin set. See SRD FR-240/FR-242/FR-245/FR-246, SADD §9.

Mounted only when both `renderService.unsafeDynamicTemplatePlugins.enabled`
and `.acknowledgeArbitraryCodeExecution` are true -- see
render/api/main.py, which conditionally calls app.include_router() for this
router rather than always including it.

Per SRD §5.2 / SADD §9.3-9.5: activation is a rolling restart of the
render-service Deployments, not a warm subprocess pool -- pod restart is
acceptable as long as it's transparent to users (Kubernetes' own
rolling-update mechanics provide that guarantee). This endpoint does not
fetch/verify plugin artifacts itself -- that happens in an init container
on each pod at startup; it triggers the rollout, and a new pod that can't
actually load the requested plugin set would fail to become ready, which
surfaces here as a rollout timeout.
"""

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nv_config_manager.common.auth import SSOIdentity, require_group
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.render.activation_state import ActivationStateStore, now_iso
from nv_config_manager.render.rollout import (
    RolloutError,
    restart_deployment_no_wait,
    trigger_and_wait_for_rollout,
)

# See SRD FR-246: authorization for this endpoint reuses the existing
# internal service-to-service auth layer (SPIFFE-based require_group), not
# a new mechanism. The operator maps Template Preview's SPIFFE ID to this
# group name via renderService.unsafeDynamicTemplatePlugins
# .templatePreviewSpiffeId in Helm (see deploy/helm/templates/_helpers.tpl).
ACTIVATION_GROUP = "template-preview-activation"

responses: dict[int | str, dict[str, Any]] = {
    409: {"description": "A different plugin set activation is already in progress"},
}

router = APIRouter(prefix="/activation", responses=responses)
logger = get_logger(__name__, category=LogCategory.RENDER_API)


def _activation_deployments() -> list[str]:
    raw = os.getenv("RENDER_ACTIVATION_DEPLOYMENTS", "")
    return [name.strip() for name in raw.split(",") if name.strip()]


def _activation_namespace() -> str:
    return os.getenv("RENDER_ACTIVATION_NAMESPACE", "default")


def _activation_configmap_name() -> str:
    return os.getenv("RENDER_ACTIVATION_CONFIGMAP", "render-activation-state")


def _activation_rollout_timeout() -> float:
    return float(os.getenv("RENDER_ACTIVATION_ROLLOUT_TIMEOUT_SECONDS", "300"))


def _activation_self_deployment() -> str | None:
    """Name of the Deployment backing the pod running this process, if set.

    Passed to trigger_and_wait_for_rollout() so it can restart this
    Deployment last and without waiting on it -- see that function's
    docstring for why waiting on your own Deployment's rollout can never
    succeed. Empty/unset falls back to the old wait-on-everything
    behavior (only safe if this Deployment isn't itself in the restart
    list, e.g. a test double).
    """
    return os.getenv("RENDER_ACTIVATION_SELF_DEPLOYMENT") or None


class ArtifactLocation(BaseModel):
    """One plugin's artifact location within a candidate plugin set.

    `digest` is the plugin's identity (a canonicalized-tree digest, not a
    hash of any byte sequence that crosses the wire) -- what names the
    plugin, used to build `url`. `archive_checksum` is a hash of the raw
    archive bytes actually served at `url`, and is what the artifact-fetch
    init container verifies its download against; the two are never the
    same value for a real, multi-file plugin.
    """

    plugin_id: str
    digest: str
    archive_checksum: str
    url: str


class ActivationRequest(BaseModel):
    """Body of a POST /v1/activation/activate call.

    request_id makes the call idempotent on both sides (SADD §3.5): a
    retried activation request with the same request_id is safe.
    """

    request_id: str
    plugin_set_digest: str
    artifacts: list[ArtifactLocation] = Field(default_factory=list)


class ActivationResponse(BaseModel):
    status: str
    detail: str


@router.post("/activate", response_model=ActivationResponse)
async def activate(
    request: ActivationRequest,
    identity: SSOIdentity = Depends(require_group(ACTIVATION_GROUP)),
) -> ActivationResponse:
    """Make the requested plugin set active via a rolling restart.

    Idempotent on request_id (SADD §3.5): a retried call for a request_id
    already recorded returns the recorded result instead of restarting
    anything a second time. Only one activation may be in progress at a
    time; a concurrent request for a *different* digest is rejected with
    409 rather than queued or interleaved.

    `READY` means every *other* Deployment's rollout was confirmed
    complete, and the outcome was durably recorded, before this process's
    own Deployment (`self_deployment`) was restarted -- it does not mean
    that restart was itself confirmed to succeed, since the pod handling
    this request cannot reliably observe its own replacement (see
    `restart_deployment_no_wait()`). Kubernetes' own rollout guarantee is
    the safety net if that pod never becomes healthy: traffic keeps going
    to the previous, still-serving pods regardless of what this response
    said.
    """
    store = ActivationStateStore(_activation_configmap_name(), _activation_namespace())
    state = await asyncio.to_thread(store.get)

    existing = state["requests"].get(request.request_id)
    if existing is not None:
        logger.info(
            "Activation request_id=%s already processed (status=%s) -- idempotent replay",
            request.request_id,
            existing["status"],
        )
        return ActivationResponse(
            status=existing["status"],
            detail=f"Already processed (idempotent replay of request_id={request.request_id}).",
        )

    if state["active_digest"] == request.plugin_set_digest:
        return ActivationResponse(status="READY", detail="Requested plugin set is already active.")

    if (
        state["in_progress_digest"] is not None
        and state["in_progress_digest"] != request.plugin_set_digest
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Activation of {state['in_progress_digest']!r} is already in progress.",
        )

    def _mark_requested(s: dict) -> dict:
        s["in_progress_digest"] = request.plugin_set_digest
        s["requests"][request.request_id] = {
            "digest": request.plugin_set_digest,
            "status": "REQUESTED",
            "updated_at": now_iso(),
        }
        return s

    await asyncio.to_thread(store.update, _mark_requested)

    logger.info(
        "Activation started by %s for plugin set %s (request_id=%s, %d artifacts)",
        identity.user,
        request.plugin_set_digest,
        request.request_id,
        len(request.artifacts),
    )

    deployments = _activation_deployments()
    self_deployment = _activation_self_deployment()
    others = [name for name in deployments if name != self_deployment]

    try:
        await asyncio.to_thread(
            trigger_and_wait_for_rollout,
            others,
            _activation_namespace(),
            _activation_rollout_timeout(),
        )
    except RolloutError as exc:
        logger.error("Activation failed for plugin set %s: %s", request.plugin_set_digest, exc)

        def _mark_failed(s: dict) -> dict:
            s["in_progress_digest"] = None
            s["requests"][request.request_id] = {
                "digest": request.plugin_set_digest,
                "status": "FAILED",
                "updated_at": now_iso(),
            }
            return s

        await asyncio.to_thread(store.update, _mark_failed)
        return ActivationResponse(status="FAILED", detail=str(exc))

    def _mark_ready(s: dict) -> dict:
        s["active_digest"] = request.plugin_set_digest
        s["in_progress_digest"] = None
        # The per-plugin artifact locations, not just the plugin-set
        # digest: a plugin set can contain multiple plugins, each with its
        # own fetchable artifact (SADD §9.6) -- the init container fetching
        # on the next pod start needs this list, not just the set's
        # identity, which isn't itself something ArtifactStore serves.
        s["active_artifacts"] = [a.model_dump() for a in request.artifacts]
        s["requests"][request.request_id] = {
            "digest": request.plugin_set_digest,
            "status": "READY",
            "updated_at": now_iso(),
        }
        return s

    # Durably record success *before* restarting our own Deployment below --
    # this process may not survive long enough to run anything after that
    # call, so nothing past this point can be relied on for correctness.
    await asyncio.to_thread(store.update, _mark_ready)

    if self_deployment is not None and self_deployment in deployments:
        try:
            await asyncio.to_thread(
                restart_deployment_no_wait, self_deployment, _activation_namespace()
            )
        except RolloutError as exc:
            # State is already durably READY -- this is a best-effort
            # trigger, logged but not surfaced as request failure.
            logger.error(
                "Activation for plugin set %s recorded READY, but restarting own "
                "Deployment %s failed: %s",
                request.plugin_set_digest,
                self_deployment,
                exc,
            )

    logger.info(
        "Activation succeeded for plugin set %s (request_id=%s)",
        request.plugin_set_digest,
        request.request_id,
    )
    return ActivationResponse(
        status="READY", detail=f"Plugin set {request.plugin_set_digest} is now active."
    )
