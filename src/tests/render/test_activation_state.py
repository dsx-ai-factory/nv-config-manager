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
"""Tests for ActivationStateStore against a mocked Kubernetes CoreV1Api --
no live cluster in this environment."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from nv_config_manager.render.activation_state import _EMPTY_STATE, ActivationStateStore


def _not_found():
    return ApiException(status=404, reason="Not Found")


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_get_returns_empty_default_when_configmap_missing(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    core_v1 = mock_core_v1_cls.return_value
    core_v1.read_namespaced_config_map.side_effect = _not_found()

    store = ActivationStateStore("render-activation-state", "ns")
    state = store.get()

    assert state == _EMPTY_STATE
    assert state is not _EMPTY_STATE  # must be a copy, not the shared module constant


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_get_parses_existing_configmap_data(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    core_v1 = mock_core_v1_cls.return_value
    stored = {"active_digest": "sha256:aaaa", "in_progress_digest": None, "requests": {}}
    cm = MagicMock()
    cm.data = {"state.json": json.dumps(stored)}
    core_v1.read_namespaced_config_map.return_value = cm

    store = ActivationStateStore("render-activation-state", "ns")
    state = store.get()
    assert {k: state[k] for k in stored} == stored  # what was stored is returned as stored


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_creates_configmap_when_missing(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    core_v1 = mock_core_v1_cls.return_value
    core_v1.read_namespaced_config_map.side_effect = _not_found()

    store = ActivationStateStore("render-activation-state", "ns")
    result = store.update(lambda s: {**s, "active_digest": "sha256:new"})

    assert result["active_digest"] == "sha256:new"
    core_v1.create_namespaced_config_map.assert_called_once()
    core_v1.replace_namespaced_config_map.assert_not_called()


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_replaces_existing_configmap(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    core_v1 = mock_core_v1_cls.return_value
    existing = dict(_EMPTY_STATE)
    cm = MagicMock()
    cm.data = {"state.json": json.dumps(existing)}
    cm.metadata.resource_version = "42"
    core_v1.read_namespaced_config_map.return_value = cm

    store = ActivationStateStore("render-activation-state", "ns")
    store.update(lambda s: {**s, "active_digest": "sha256:new"})

    core_v1.replace_namespaced_config_map.assert_called_once()
    core_v1.create_namespaced_config_map.assert_not_called()


@patch("nv_config_manager.render.activation_state.time.sleep", lambda _s: None)
@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_retries_on_write_conflict(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    core_v1 = mock_core_v1_cls.return_value
    cm = MagicMock()
    cm.data = {"state.json": json.dumps(_EMPTY_STATE)}
    cm.metadata.resource_version = "1"
    core_v1.read_namespaced_config_map.return_value = cm
    core_v1.replace_namespaced_config_map.side_effect = [
        ApiException(status=409, reason="Conflict"),
        MagicMock(),
    ]

    store = ActivationStateStore("render-activation-state", "ns")
    result = store.update(lambda s: {**s, "active_digest": "sha256:retried"})

    assert result["active_digest"] == "sha256:retried"
    assert core_v1.replace_namespaced_config_map.call_count == 2


@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
@patch("nv_config_manager.render.activation_state.load_k8s_config")
def test_state_written_by_an_older_version_is_filled_in_with_the_newer_keys(
    _cfg: MagicMock, core_cls: MagicMock
) -> None:
    stored = {"active_digest": "sha256:aaaa", "in_progress_digest": None, "requests": {}}
    core_cls.return_value.read_namespaced_config_map.return_value = MagicMock(
        data={"state.json": json.dumps(stored)}
    )

    state = ActivationStateStore("cm", "ns").get()

    assert state["active_digest"] == "sha256:aaaa"
    assert state["in_progress_artifacts"] == [] and state["in_progress_since"] is None


@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
@patch("nv_config_manager.render.activation_state.load_k8s_config")
def test_changing_a_returned_empty_state_does_not_change_the_default(
    _cfg: MagicMock, core_cls: MagicMock
) -> None:
    core_cls.return_value.read_namespaced_config_map.side_effect = ApiException(status=404)
    state = ActivationStateStore("cm", "ns").get()

    state["requests"]["x"] = {"status": "READY"}
    state["in_progress_artifacts"].append({"plugin_id": "x"})

    assert ActivationStateStore("cm", "ns").get()["requests"] == {}
    assert ActivationStateStore("cm", "ns").get()["in_progress_artifacts"] == []


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_gives_the_mutator_every_key_even_when_the_stored_state_lacks_some(
    mock_core_v1_cls: MagicMock, _mock_load_config: MagicMock
) -> None:
    # A state written by an older version has no in_progress_artifacts; get() fills it in, and update()
    # must too, or a mutator reading it raises KeyError and the write never happens.
    core_v1 = mock_core_v1_cls.return_value
    cm = MagicMock()
    cm.data = {"state.json": json.dumps({"active_digest": "sha256:old", "requests": {}})}
    cm.metadata.resource_version = "7"
    core_v1.read_namespaced_config_map.return_value = cm
    seen: dict = {}

    store = ActivationStateStore("render-activation-state", "ns")
    store.update(lambda s: seen.update(s) or s)

    assert seen["active_digest"] == "sha256:old"  # what was stored is kept
    assert seen["in_progress_digest"] is None and seen["in_progress_artifacts"] == []
    assert set(_EMPTY_STATE) <= set(seen)
