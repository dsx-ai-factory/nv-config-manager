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
"""Tests for the activation_v1 router: request shape, auth (reuses the existing require_group()
SPIFFE-backed mechanism), recording, idempotency, conflicts, stale in-flight activations and the
completion report. This service no longer restarts anything -- Template Preview does -- so these
tests only check what is recorded; the real Kubernetes ConfigMap (ActivationStateStore) is replaced
with an in-memory double."""

from __future__ import annotations

from configparser import ConfigParser
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import nv_config_manager.common.auth as auth_mod
from nv_config_manager.common.auth import _jwks_clients, load_auth_config
from nv_config_manager.render.activation_state import _EMPTY_STATE, artifacts_to_install
from nv_config_manager.render.api.activation_v1 import ACTIVATION_GROUP, router


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


STORE = "nv_config_manager.render.api.activation_v1.ActivationStateStore"


def _allow_headers():
    load_auth_config(_make_config(auth={"accept_request_headers": "true"}))


@patch(STORE, FakeStateStore)
def test_activate_records_the_set_and_its_artifacts_but_changes_nothing_else():
    _allow_headers()
    resp = _client().post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ACCEPTED"
    state = FakeStateStore._shared_state
    assert state["in_progress_digest"] == "sha256:aaaa"
    assert state["in_progress_artifacts"][0]["plugin_id"] == "site-overrides"
    assert state["in_progress_since"] is not None
    assert state["requests"]["req-1"]["status"] == "REQUESTED"
    # nothing is active until the caller reports a successful restart
    assert state["active_digest"] is None and state["active_artifacts"] == []


@patch(STORE, FakeStateStore)
def test_the_in_flight_set_is_what_a_restarted_pod_installs():
    _allow_headers()
    _client().post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    installed = artifacts_to_install(FakeStateStore._shared_state)

    assert [a["plugin_id"] for a in installed] == ["site-overrides"]


def test_without_an_in_flight_activation_pods_install_the_active_set():
    state = deepcopy(_EMPTY_STATE)
    state["active_artifacts"] = [{"plugin_id": "old"}]
    state["in_progress_artifacts"] = [{"plugin_id": "stale-leftover"}]  # ignored: nothing in flight

    assert artifacts_to_install(state) == [{"plugin_id": "old"}]


@patch(STORE, FakeStateStore)
def test_a_ready_report_promotes_the_in_flight_set():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    resp = client.post(
        "/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS
    )

    assert resp.status_code == 200 and resp.json()["status"] == "READY"
    state = FakeStateStore._shared_state
    assert state["active_digest"] == "sha256:aaaa"
    assert state["active_artifacts"][0]["plugin_id"] == "site-overrides"
    assert state["in_progress_digest"] is None and state["in_progress_artifacts"] == []
    assert state["requests"]["req-1"]["status"] == "READY"


@patch(STORE, FakeStateStore)
def test_a_failed_report_keeps_the_previous_set_active():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body("req-1", "sha256:old"), headers=AUTH_HEADERS)
    client.post("/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS)
    client.post("/v1/activation/activate", json=_body("req-2", "sha256:new"), headers=AUTH_HEADERS)

    resp = client.post(
        "/v1/activation/req-2/complete",
        json={"outcome": "FAILED", "detail": "Deployment 'api' did not become ready"},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 200 and resp.json()["status"] == "FAILED"
    assert "did not become ready" in resp.json()["detail"]
    state = FakeStateStore._shared_state
    assert state["active_digest"] == "sha256:old"
    assert state["in_progress_digest"] is None
    assert state["requests"]["req-2"]["status"] == "FAILED"


@patch(STORE, FakeStateStore)
def test_a_retried_activate_returns_accepted_until_it_is_completed_then_the_outcome():
    _allow_headers()
    client = _client()

    first = client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)
    again = client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)
    client.post("/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS)
    after = client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)

    assert (first.json()["status"], again.json()["status"]) == ("ACCEPTED", "ACCEPTED")
    assert after.json()["status"] == "READY"


@patch(STORE, FakeStateStore)
def test_an_already_active_digest_needs_no_restart():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body("req-1"), headers=AUTH_HEADERS)
    client.post("/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS)

    resp = client.post("/v1/activation/activate", json=_body("req-2"), headers=AUTH_HEADERS)

    assert resp.json()["status"] == "READY"
    assert "req-2" not in FakeStateStore._shared_state["requests"]


@patch(STORE, FakeStateStore)
def test_a_different_digest_while_one_is_in_flight_is_rejected():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body("req-1", "sha256:aaaa"), headers=AUTH_HEADERS)

    resp = client.post(
        "/v1/activation/activate", json=_body("req-2", "sha256:zzzz"), headers=AUTH_HEADERS
    )

    assert resp.status_code == 409
    assert FakeStateStore._shared_state["in_progress_digest"] == "sha256:aaaa"


class StaleReadStore(FakeStateStore):
    """Reads see the state from before the other request's write, as two simultaneous requests do."""

    def get(self) -> dict:
        return deepcopy(_EMPTY_STATE)


@patch(STORE, StaleReadStore)
def test_two_simultaneous_different_activations_cannot_both_be_accepted():
    _allow_headers()
    client = _client()
    first = client.post(
        "/v1/activation/activate", json=_body("req-1", "sha256:aaaa"), headers=AUTH_HEADERS
    )

    second = client.post(
        "/v1/activation/activate", json=_body("req-2", "sha256:zzzz"), headers=AUTH_HEADERS
    )

    assert first.json()["status"] == "ACCEPTED"
    assert second.status_code == 409
    state = FakeStateStore._shared_state
    assert state["in_progress_digest"] == "sha256:aaaa"
    assert state["requests"]["req-1"]["status"] == "REQUESTED"


@patch(STORE, FakeStateStore)
def test_a_stale_in_flight_activation_is_replaced_by_the_next_one():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body("req-1", "sha256:aaaa"), headers=AUTH_HEADERS)

    def _age(s):
        s["in_progress_since"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        return s

    FakeStateStore().update(_age)

    resp = client.post(
        "/v1/activation/activate", json=_body("req-2", "sha256:zzzz"), headers=AUTH_HEADERS
    )

    assert resp.status_code == 200 and resp.json()["status"] == "ACCEPTED"
    state = FakeStateStore._shared_state
    assert state["in_progress_digest"] == "sha256:zzzz"
    assert state["requests"]["req-1"]["status"] == "FAILED"  # abandoned, not left dangling


@patch(STORE, FakeStateStore)
def test_completing_an_unknown_request_is_404():
    _allow_headers()

    resp = _client().post(
        "/v1/activation/nope/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS
    )

    assert resp.status_code == 404


@patch(STORE, FakeStateStore)
def test_repeating_a_completion_is_safe_but_changing_it_is_a_conflict():
    _allow_headers()
    client = _client()
    client.post("/v1/activation/activate", json=_body(), headers=AUTH_HEADERS)
    client.post("/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS)

    same = client.post(
        "/v1/activation/req-1/complete", json={"outcome": "READY"}, headers=AUTH_HEADERS
    )
    changed = client.post(
        "/v1/activation/req-1/complete", json={"outcome": "FAILED"}, headers=AUTH_HEADERS
    )

    assert same.status_code == 200 and same.json()["status"] == "READY"
    assert changed.status_code == 409


def test_completing_without_the_activation_group_is_forbidden():
    _allow_headers()

    resp = _client().post(
        "/v1/activation/req-1/complete",
        json={"outcome": "READY"},
        headers={
            "X-Auth-Request-Email": "someone-else@example.com",
            "X-Auth-Request-Groups": "some-other-group",
        },
    )

    assert resp.status_code == 403


def test_completion_outcomes_are_limited_to_ready_and_failed():
    _allow_headers()

    resp = _client().post(
        "/v1/activation/req-1/complete", json={"outcome": "MAYBE"}, headers=AUTH_HEADERS
    )

    assert resp.status_code == 422


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
