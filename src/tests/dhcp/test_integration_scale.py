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
"""Regression coverage for the DHCP scale fixture's reconciliation lifecycle."""

import json
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.integration import test_dhcp as scale


@pytest.mark.parametrize("failure", [None, "publish", "wait", "body", "restore"])
def test_scale_fixture_restores_desired_config_on_success_and_failure(monkeypatch, failure):
    """Keep credentials out of desired state and resume refresh after cleanup."""
    original = {"Dhcp4": {"subnet4": [{"subnet": "192.0.2.0/24"}]}, "Logging": {"loggers": []}}
    snapshot = deepcopy(original)
    state = {"desired": original, "paused": False}
    observed = []

    @contextmanager
    def paused_refresh(namespace):
        """Model a refresh writer that must remain paused through restoration."""
        state["paused"] = True
        try:
            yield
        finally:
            observed.append("resume")
            state["paused"] = False

    def desired_config(namespace, pod, config=None):
        """Fail if any fixture access races the refresh writer."""
        assert state["paused"]
        if config is None:
            return state["desired"]
        assert "lease-database" not in config["Dhcp4"]
        seeded = len(config["Dhcp4"]["subnet4"]) > 1
        if seeded:
            assert config["Dhcp4"]["subnet4"][0] == snapshot["Dhcp4"]["subnet4"][0]
            assert len(config["Dhcp4"]["subnet4"][-1]["reservations"]) == 201
            if failure == "publish":
                state["desired"] = config
                raise RuntimeError("publish failed after mutation")
        elif failure == "restore":
            raise RuntimeError("restore failed")
        state["desired"] = config

    def wait_for_subnet(namespace, pod, subnet, *, present):
        """Check that cleanup converges before the refresh writer resumes."""
        assert state["paused"]
        if present:
            assert state["desired"]["Logging"] == original["Logging"]
            if failure == "wait":
                raise RuntimeError("reconciliation failed")
        else:
            assert state["desired"] == snapshot
        observed.append(present)

    monkeypatch.setattr(scale, "_paused_dhcp_refresh", paused_refresh)
    monkeypatch.setattr(scale, "_kea_desired_config", desired_config)
    monkeypatch.setattr(scale, "_wait_for_kea_subnet", wait_for_subnet)
    # The fixture must never obtain its seed from unsanitized live config.
    monkeypatch.setattr(scale, "_get_kea_dhcp4_config", Mock(side_effect=AssertionError))

    def exercise():
        """Exercise the fixture's public lifecycle including an assertion failure."""
        with scale._temporary_kea_dhcp4_config("test", "dhcp", 201) as (dhcp4, subnet):
            assert state["desired"]["Dhcp4"] == dhcp4
            assert subnet == "198.18.0.0/16"
            if failure == "body":
                raise RuntimeError("assertion failed")

    if failure:
        with pytest.raises(RuntimeError):
            exercise()
    else:
        exercise()
    if failure != "restore":
        assert state["desired"] == snapshot
        assert observed[-2] is False
    assert original == snapshot
    assert observed[-1] == "resume"
    assert not state["paused"]


@pytest.mark.parametrize("present", [True, False])
def test_scale_fixture_waits_until_live_config_matches(monkeypatch, present):
    """Poll until the live subnet matches the requested presence state."""
    with_subnet = {"subnet4": [{"subnet": "198.18.0.0/16"}]}
    without_subnet = {"subnet4": []}
    configs = [without_subnet, with_subnet] if present else [with_subnet, without_subnet]
    get_config = Mock(side_effect=configs)
    sleep = Mock()
    monkeypatch.setattr(scale, "_get_kea_dhcp4_config", get_config)
    monkeypatch.setattr(scale.time, "sleep", sleep)
    scale._wait_for_kea_subnet("test", "dhcp", "198.18.0.0/16", present=present)
    assert get_config.call_count == 2
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("replicas", [0, 1, 3])
@pytest.mark.parametrize("failure", [None, "scale", "wait", "body"])
def test_pause_refresh_waits_for_pods_and_restores_replicas(monkeypatch, replicas, failure):
    """Restore the exact deployment size even after partial setup or test failure."""
    commands = []
    pod_reads = []

    def kubectl(command, **kwargs):
        """Model an old refresh pod that survives the initial scale-down request."""
        commands.append(command)
        if "deployments" in command:
            payload = {
                "items": [
                    {
                        "metadata": {"name": "dhcp-refresh"},
                        "spec": {
                            "replicas": replicas,
                            "selector": {"matchLabels": {"app": "dhcp-refresh"}},
                        },
                    }
                ]
            }
        elif "pods" in command:
            pod_reads.append(True)
            if failure == "wait":
                return subprocess.CompletedProcess(command, 1, "", "unavailable")
            payload = {"items": [{"metadata": {"name": "old-pod"}}] if len(pod_reads) == 1 else []}
        else:
            if "--replicas=0" in command and len(commands) == 2 and failure == "scale":
                return subprocess.CompletedProcess(command, 1, "", "ambiguous scale failure")
            payload = {}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(scale.subprocess, "run", kubectl)
    monkeypatch.setattr(scale.time, "sleep", Mock())

    def exercise():
        """Enter the fixture only after every refresh pod has stopped."""
        with scale._paused_dhcp_refresh("test"):
            assert len(pod_reads) == 2
            if failure == "body":
                raise RuntimeError("test failed")

    if failure:
        with pytest.raises(RuntimeError if failure == "body" else pytest.fail.Exception):
            exercise()
    else:
        exercise()
    assert "scale" in commands[-1]
    assert f"--replicas={replicas}" in commands[-1]
    assert "deployment/dhcp-refresh" in commands[-1]
