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
"""V1 Activation API -- internal endpoints Template Preview calls to activate a dynamic template
plugin set. See SRD FR-240/FR-242/FR-245/FR-246/FR-258/FR-259, SADD §9.

Mounted only when both `renderService.unsafeDynamicTemplatePlugins.enabled` and
`.acknowledgeArbitraryCodeExecution` are true -- see render/api/main.py, which conditionally calls
app.include_router() for this router rather than always including it.

Activation is a rolling restart of the render-service Deployments (SRD §5.2), but the *restart* is
not done here. Template Preview holds the Kubernetes permission to restart those Deployments, so
this service never restarts itself or its siblings. The two sides split the work:

1. ``POST /activation/activate`` -- Template Preview announces a plugin set. This service checks the
   request, durably records it (the digest and the artifacts every restarted pod must install) and
   answers ``ACCEPTED``; it does not touch any Deployment.
2. Template Preview restarts the render Deployments and waits for them to roll out. A pod that
   starts installs the recorded in-flight set (see ``artifacts_to_install``).
3. ``POST /activation/{request_id}/complete`` -- Template Preview reports the outcome. On ``READY``
   the in-flight set becomes the active one; on ``FAILED`` the in-flight record is dropped and the
   previous set stays active.

Because the new set is recorded before any pod restarts, every Deployment installs it. If Template
Preview dies between steps 1 and 3, the in-flight record goes stale and is replaced by the next
activation (see ``_stale_after_seconds``).
"""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nv_config_manager.common.auth import SSOIdentity, require_group
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.render.activation_state import ActivationStateStore, now_iso

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


def _activation_namespace() -> str:
    return os.getenv("RENDER_ACTIVATION_NAMESPACE", "default")


def _activation_configmap_name() -> str:
    return os.getenv("RENDER_ACTIVATION_CONFIGMAP", "render-activation-state")


def _stale_after_seconds() -> float:
    """How long an announced-but-never-completed activation blocks a different one.

    Longer than Template Preview's rollout wait, so only an activation whose caller died is replaced.
    """
    return float(os.getenv("RENDER_ACTIVATION_STALE_SECONDS", "900"))


def _seconds_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()


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
    """``ACCEPTED``: recorded, the caller must now restart the render Deployments and report the
    outcome. ``READY`` / ``FAILED``: nothing more to do (already active, or a replay of a finished
    request)."""

    status: str
    detail: str


class CompletionRequest(BaseModel):
    outcome: Literal["READY", "FAILED"]
    detail: str = ""


def _response_for(recorded_status: str, request_id: str) -> ActivationResponse:
    status = "ACCEPTED" if recorded_status == "REQUESTED" else recorded_status
    return ActivationResponse(
        status=status,
        detail=f"Already processed (idempotent replay of request_id={request_id}).",
    )


@router.post("/activate", response_model=ActivationResponse)
async def activate(
    request: ActivationRequest,
    identity: SSOIdentity = Depends(require_group(ACTIVATION_GROUP)),
) -> ActivationResponse:
    """Record an activation request. Does not restart anything.

    Idempotent on request_id (SADD §3.5): a retried call for a request_id already recorded returns
    the recorded result. Only one activation may be in flight at a time: a request for a
    *different* digest while one is in flight is rejected with 409, unless the in-flight one is
    stale (its caller never reported an outcome), in which case it is abandoned and replaced.
    """
    store = ActivationStateStore(_activation_configmap_name(), _activation_namespace())
    outcome: dict[str, str] = {}

    # The checks run inside the read-modify-write, so two requests that arrive together cannot both
    # pass them against the same snapshot: the store retries the loser against the winner's state.
    def _record(s: dict) -> dict:
        outcome.clear()
        existing = s["requests"].get(request.request_id)
        if existing is not None:
            outcome["replay"] = existing["status"]
            return s
        if s["active_digest"] == request.plugin_set_digest and not s["in_progress_digest"]:
            # Recorded as finished, so a retry of this request_id stays READY even after another set
            # becomes active (request_id idempotency), and never announces this set again.
            s["requests"][request.request_id] = {
                "digest": request.plugin_set_digest,
                "status": "READY",
                "updated_at": now_iso(),
            }
            outcome["already_active"] = "1"
            return s
        in_flight = s["in_progress_digest"]
        if in_flight is not None and in_flight != request.plugin_set_digest:
            if _seconds_since(s.get("in_progress_since")) < _stale_after_seconds():
                raise HTTPException(
                    status_code=409,
                    detail=f"Activation of {in_flight!r} is already in progress.",
                )
            logger.warning("Abandoning stale in-flight activation of %s", in_flight)
        for recorded in s["requests"].values():
            if (
                recorded["status"] == "REQUESTED"
                and recorded["digest"] != request.plugin_set_digest
            ):
                recorded["status"] = "FAILED"
                recorded["updated_at"] = now_iso()
        s["in_progress_digest"] = request.plugin_set_digest
        s["in_progress_artifacts"] = [a.model_dump() for a in request.artifacts]
        s["in_progress_since"] = now_iso()
        s["requests"][request.request_id] = {
            "digest": request.plugin_set_digest,
            "status": "REQUESTED",
            "updated_at": now_iso(),
        }
        outcome["accepted"] = "1"
        return s

    await asyncio.to_thread(store.update, _record)
    if "replay" in outcome:
        logger.info(
            "Activation request_id=%s already processed (status=%s) -- idempotent replay",
            request.request_id,
            outcome["replay"],
        )
        return _response_for(outcome["replay"], request.request_id)
    if "already_active" in outcome:
        return ActivationResponse(status="READY", detail="Requested plugin set is already active.")
    logger.info(
        "Activation accepted from %s for plugin set %s (request_id=%s, %d artifacts)",
        identity.user,
        request.plugin_set_digest,
        request.request_id,
        len(request.artifacts),
    )
    return ActivationResponse(
        status="ACCEPTED",
        detail="Recorded. Restart the render Deployments, then report the outcome.",
    )


@router.post("/{request_id}/complete", response_model=ActivationResponse)
async def complete(
    request_id: str,
    body: CompletionRequest,
    identity: SSOIdentity = Depends(require_group(ACTIVATION_GROUP)),
) -> ActivationResponse:
    """Report the outcome of a restart that followed an accepted activation.

    ``READY`` promotes the in-flight set to the active set; ``FAILED`` drops it and leaves the
    previous set active. Idempotent: repeating the same outcome returns it; a different outcome for
    an already-finished request is a 409.
    """
    store = ActivationStateStore(_activation_configmap_name(), _activation_namespace())
    state = await asyncio.to_thread(store.get)

    if state["requests"].get(request_id) is None:
        raise HTTPException(status_code=404, detail=f"No activation request {request_id!r}.")

    outcome: dict[str, str] = {}

    # Every check runs inside the read-modify-write, against the state that is about to be written:
    # a replacement activation that commits after the read above must not be promoted, or have its
    # in-flight set cleared, by this completion.
    def _finish(s: dict) -> dict:
        outcome.clear()
        recorded = s["requests"].get(request_id)
        if recorded is None:
            raise HTTPException(status_code=404, detail=f"No activation request {request_id!r}.")
        if recorded["status"] != "REQUESTED":
            if recorded["status"] == body.outcome:
                outcome["replay"] = recorded["status"]
                return s
            raise HTTPException(
                status_code=409,
                detail=f"Request {request_id!r} already finished as {recorded['status']}.",
            )
        digest = s["in_progress_digest"]
        if digest != recorded["digest"]:
            raise HTTPException(
                status_code=409,
                detail=f"Request {request_id!r} is no longer the in-flight activation.",
            )
        if body.outcome == "READY":
            s["active_digest"] = digest
            s["active_artifacts"] = s["in_progress_artifacts"]
        s["in_progress_digest"] = None
        s["in_progress_artifacts"] = []
        s["in_progress_since"] = None
        # Requests for the same set share one activation: they finish together, so a second caller's
        # report and retries are replays of a recorded outcome, not conflicts.
        for sibling in s["requests"].values():
            if sibling["status"] == "REQUESTED" and sibling["digest"] == digest:
                sibling["status"] = body.outcome
                sibling["updated_at"] = now_iso()
        return s

    finished = await asyncio.to_thread(store.update, _finish)
    if "replay" in outcome:
        return _response_for(outcome["replay"], request_id)
    logger.info(
        "Activation %s reported %s by %s%s",
        request_id,
        body.outcome,
        identity.user,
        f": {body.detail}" if body.detail else "",
    )
    detail = (
        f"Plugin set {finished['requests'][request_id]['digest']} is now active."
        if body.outcome == "READY"
        else f"Activation failed; the previous plugin set stays active. {body.detail}".strip()
    )
    return ActivationResponse(status=body.outcome, detail=detail)
