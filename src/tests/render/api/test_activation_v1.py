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
"""Tests for the activation_v1 router: request shape, auth (reuses the
existing require_group() SPIFFE-backed mechanism), idempotency, conflicting
concurrent activations, and success/failure handling. The real Kubernetes
calls (ActivationStateStore, trigger_and_wait_for_rollout) are replaced with
test doubles -- there is no live cluster in this test environment; a real
rollout against a local kind cluster is a separate, follow-on verification
effort, not a unit test's job."""

from __future__ import annotations

from configparser import ConfigParser
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import nv_config_manager.common.auth as auth_mod
from nv_config_manager.common.auth import _jwks_clients, load_auth_config
from nv_config_manager.render.activation_state import _EMPTY_STATE
from nv_config_manager.render.api.activation_v1 import ACTIVATION_GROUP, router
from nv_config_manager.render.rollout import RolloutError


def _make_config(**sections: dict[str, str]) -> ConfigParser:
    cp = ConfigParser()
    for section, values in sections.items():
        cp.add_section(section)
        for k, v in values.items():
            cp.set(section, k, v)
    return cp


@pytest.fixture(autouse=True)
def _clear_auth_caches():
    """Same reset pattern as src/tests/common/test_auth.py -- require_group()
    reads the module-level auth config cache, which must not leak between
    tests."""
    _jwks_clients.clear()
    auth_mod._auth_config = None
    yield
    _jwks_clients.clear()
    auth_mod._auth_config = None


class FakeStateStore:
    """In-memory stand-in for ActivationStateStore -- same get()/update()
    contract, backed by a plain dict instead of a Kubernetes ConfigMap."""

    def __init__(self, *_args, **_kwargs):
        pass

    _shared_state: dict = deepcopy(_EMPTY_STATE)

    def get(self) -> dict:
        return deepcopy(self._shared_state)

    def update(self, mutator):
        new_state = mutator(deepcopy(self._shared_state))
        FakeStateStore._shared_state = new_state
        return deepcopy(new_state)

    @classmethod
    def reset(cls):
        cls._shared_state = deepcopy(_EMPTY_STATE)


@pytest.fixture(autouse=True)
def _reset_fake_store():
    FakeStateStore.reset()
    yield
    FakeStateStore.reset()


AUTH_HEADERS = {
    "X-Auth-Request-Email": "template-preview@example.com",
    "X-Auth-Request-Groups": ACTIVATION_GROUP,
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return TestClient(app)


def _body(request_id: str = "req-1", digest: str = "sha256:aaaa") -> dict:
    return {
        "request_id": request_id,
        "plugin_set_digest": digest,
        "artifacts": [
            {
                "plugin_id": "site-overrides",
                "digest": "sha256:bbbb",
                "archive_checksum": "sha256:bbbb-raw",
                "url": "https://example/a",
            }
        ],
    }


@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_success_marks_digest_active(mock_rollout):
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))
    resp = _client().post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "READY"
    mock_rollout.assert_called_once()
    assert FakeStateStore._shared_state["active_digest"] == "sha256:aaaa"
    assert FakeStateStore._shared_state["in_progress_digest"] is None
    assert FakeStateStore._shared_state["requests"]["req-1"]["status"] == "READY"


@patch.dict(
    "os.environ",
    {"RENDER_ACTIVATION_DEPLOYMENTS": "api,consumer", "RENDER_ACTIVATION_SELF_DEPLOYMENT": "api"},
)
@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.restart_deployment_no_wait")
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_restarts_self_deployment_after_marking_ready(mock_rollout, mock_self_restart):
    """When RENDER_ACTIVATION_SELF_DEPLOYMENT names one of the Deployments,
    it must be excluded from trigger_and_wait_for_rollout (never waited on)
    and the ConfigMap must already say READY before restart_deployment_no_wait
    is called for it -- the ordering that survives this process dying
    immediately after triggering its own restart."""
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))
    body = _body()
    body["artifacts"][0]["plugin_id"] = "api"  # irrelevant to deployments, just reusing shape

    resp = _client().post("/v1/activation/activate", json=body, headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"
    # "api" must never appear in the list passed to trigger_and_wait_for_rollout.
    waited_on = mock_rollout.call_args.args[0]
    assert "api" not in waited_on
    mock_self_restart.assert_called_once_with("api", "default")
    assert FakeStateStore._shared_state["active_digest"] == body["plugin_set_digest"]


@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_rollout_failure_marks_failed_not_active(mock_rollout):
    mock_rollout.side_effect = RolloutError("Deployment 'api' did not become ready before timeout")
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))

    resp = _client().post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert "did not become ready" in body["detail"]
    assert FakeStateStore._shared_state["active_digest"] is None
    assert FakeStateStore._shared_state["in_progress_digest"] is None
    assert FakeStateStore._shared_state["requests"]["req-1"]["status"] == "FAILED"


@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_retried_request_id_is_idempotent_no_op(mock_rollout):
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))
    client = _client()

    first = client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert mock_rollout.call_count == 1

    second = client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json()["status"] == "READY"
    # Retrying the same request_id must not trigger a second rollout.
    assert mock_rollout.call_count == 1


@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_already_active_digest_is_no_op(mock_rollout):
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))
    client = _client()

    client.post("/v1/activation/activate", json=_body("req-1"), headers=AUTH_HEADERS)
    assert mock_rollout.call_count == 1

    resp = client.post("/v1/activation/activate", json=_body("req-2"), headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"
    # Same digest, different request_id -- still shouldn't re-trigger a rollout.
    assert mock_rollout.call_count == 1


@patch("nv_config_manager.render.api.activation_v1.ActivationStateStore", FakeStateStore)
@patch("nv_config_manager.render.api.activation_v1.trigger_and_wait_for_rollout")
def test_activate_conflicting_concurrent_digest_is_rejected(mock_rollout):
    """A activation for digest A is recorded as in-progress (rollout mocked
    to never resolve mid-test by simply not awaiting completion here --
    instead we directly manipulate FakeStateStore to simulate "in flight")."""
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))

    def _in_progress(s):
        s["in_progress_digest"] = "sha256:aaaa"
        return s

    FakeStateStore().update(_in_progress)

    resp = _client().post(
        "/v1/activation/activate", json=_body("req-2", "sha256:zzzz"), headers=AUTH_HEADERS
    )

    assert resp.status_code == 409
    mock_rollout.assert_not_called()


def test_activate_without_matching_group_is_forbidden():
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))

    resp = _client().post(
        "/v1/activation/activate",
        json=_body(),
        headers={
            "X-Auth-Request-Email": "someone-else@example.com",
            "X-Auth-Request-Groups": "some-other-group",
        },
    )

    assert resp.status_code == 403


def test_activate_unauthenticated_is_forbidden():
    load_auth_config(_make_config(auth={"accept_request_headers": "false"}))

    resp = _client().post("/v1/activation/activate", json=_body())

    assert resp.status_code == 403


def test_activate_rejects_malformed_body():
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))

    resp = _client().post(
        "/v1/activation/activate",
        json={"plugin_set_digest": "sha256:aaaa"},  # missing request_id
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 422
