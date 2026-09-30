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
def test_get_returns_empty_default_when_configmap_missing(mock_core_v1_cls, _mock_load_config):
    core_v1 = mock_core_v1_cls.return_value
    core_v1.read_namespaced_config_map.side_effect = _not_found()

    store = ActivationStateStore("render-activation-state", "ns")
    state = store.get()

    assert state == _EMPTY_STATE
    assert state is not _EMPTY_STATE  # must be a copy, not the shared module constant


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_get_parses_existing_configmap_data(mock_core_v1_cls, _mock_load_config):
    core_v1 = mock_core_v1_cls.return_value
    stored = {"active_digest": "sha256:aaaa", "in_progress_digest": None, "requests": {}}
    cm = MagicMock()
    cm.data = {"state.json": json.dumps(stored)}
    core_v1.read_namespaced_config_map.return_value = cm

    store = ActivationStateStore("render-activation-state", "ns")
    assert store.get() == stored


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_creates_configmap_when_missing(mock_core_v1_cls, _mock_load_config):
    core_v1 = mock_core_v1_cls.return_value
    core_v1.read_namespaced_config_map.side_effect = _not_found()

    store = ActivationStateStore("render-activation-state", "ns")
    result = store.update(lambda s: {**s, "active_digest": "sha256:new"})

    assert result["active_digest"] == "sha256:new"
    core_v1.create_namespaced_config_map.assert_called_once()
    core_v1.replace_namespaced_config_map.assert_not_called()


@patch("nv_config_manager.render.activation_state.load_k8s_config")
@patch("nv_config_manager.render.activation_state.client.CoreV1Api")
def test_update_replaces_existing_configmap(mock_core_v1_cls, _mock_load_config):
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
def test_update_retries_on_write_conflict(mock_core_v1_cls, _mock_load_config):
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
