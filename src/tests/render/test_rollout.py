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
"""Tests for rollout.py's rollout-complete check and trigger/wait flow,
against a mocked Kubernetes client -- no live cluster in this environment.
A real rollout against a local kind cluster is a separate, follow-on
verification effort, not this."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from nv_config_manager.render.rollout import (
    RolloutError,
    _rollout_complete,
    restart_deployment_no_wait,
    trigger_and_wait_for_rollout,
)


def _deployment(
    *, generation=2, observed_generation=2, replicas=2, updated=2, ready=2, available=2
):
    dep = MagicMock()
    dep.metadata.generation = generation
    dep.spec.replicas = replicas
    dep.status.observed_generation = observed_generation
    dep.status.updated_replicas = updated
    dep.status.ready_replicas = ready
    dep.status.available_replicas = available
    return dep


def test_rollout_complete_when_all_conditions_met():
    assert _rollout_complete(_deployment()) is True


def test_rollout_not_complete_when_observed_generation_lags():
    assert _rollout_complete(_deployment(generation=3, observed_generation=2)) is False


def test_rollout_not_complete_when_not_all_replicas_ready():
    assert _rollout_complete(_deployment(replicas=3, ready=2)) is False


def test_rollout_not_complete_when_status_missing():
    dep = MagicMock()
    dep.status = None
    assert _rollout_complete(dep) is False


@patch("nv_config_manager.render.rollout.load_k8s_config")
@patch("nv_config_manager.render.rollout.client.AppsV1Api")
def test_trigger_and_wait_success(mock_apps_v1_cls, _mock_load_config):
    apps_v1 = mock_apps_v1_cls.return_value
    apps_v1.patch_namespaced_deployment.return_value = MagicMock(metadata=MagicMock(generation=5))
    apps_v1.read_namespaced_deployment.return_value = _deployment(
        generation=5, observed_generation=5
    )

    trigger_and_wait_for_rollout(["api", "consumer-nautobot"], "ns", timeout_seconds=5)

    assert apps_v1.patch_namespaced_deployment.call_count == 2
    assert apps_v1.read_namespaced_deployment.call_count == 2


@patch("nv_config_manager.render.rollout.load_k8s_config")
@patch("nv_config_manager.render.rollout.client.AppsV1Api")
def test_trigger_and_wait_raises_on_restart_api_error(mock_apps_v1_cls, _mock_load_config):
    apps_v1 = mock_apps_v1_cls.return_value
    apps_v1.patch_namespaced_deployment.side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(RolloutError):
        trigger_and_wait_for_rollout(["api"], "ns", timeout_seconds=5)


@patch("nv_config_manager.render.rollout.time.sleep", lambda _s: None)
@patch("nv_config_manager.render.rollout.load_k8s_config")
@patch("nv_config_manager.render.rollout.client.AppsV1Api")
def test_trigger_and_wait_times_out_if_never_ready(mock_apps_v1_cls, _mock_load_config):
    apps_v1 = mock_apps_v1_cls.return_value
    apps_v1.patch_namespaced_deployment.return_value = MagicMock(metadata=MagicMock(generation=5))
    # Never satisfies _rollout_complete -- ready_replicas always behind.
    apps_v1.read_namespaced_deployment.return_value = _deployment(
        generation=5, observed_generation=5, replicas=2, ready=1
    )

    with pytest.raises(RolloutError):
        trigger_and_wait_for_rollout(["api"], "ns", timeout_seconds=0.05)


@patch("nv_config_manager.render.rollout.load_k8s_config")
@patch("nv_config_manager.render.rollout.client.AppsV1Api")
def test_restart_deployment_no_wait_triggers_without_polling(mock_apps_v1_cls, _mock_load_config):
    apps_v1 = mock_apps_v1_cls.return_value
    apps_v1.patch_namespaced_deployment.return_value = MagicMock(metadata=MagicMock(generation=5))

    restart_deployment_no_wait("api", "ns")

    apps_v1.patch_namespaced_deployment.assert_called_once()
    assert apps_v1.patch_namespaced_deployment.call_args.args[0] == "api"
    apps_v1.read_namespaced_deployment.assert_not_called()


@patch("nv_config_manager.render.rollout.load_k8s_config")
@patch("nv_config_manager.render.rollout.client.AppsV1Api")
def test_restart_deployment_no_wait_raises_on_restart_api_error(
    mock_apps_v1_cls, _mock_load_config
):
    apps_v1 = mock_apps_v1_cls.return_value
    apps_v1.patch_namespaced_deployment.side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(RolloutError):
        restart_deployment_no_wait("api", "ns")
