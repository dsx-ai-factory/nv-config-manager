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

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.integration import test_dhcp as scale


@pytest.mark.parametrize("failure", [None, "publish", "wait", "body"])
def test_scale_fixture_restores_desired_config_on_success_and_failure(monkeypatch, failure):
    original = {"Dhcp4": {"subnet4": [{"subnet": "192.0.2.0/24"}]}, "Logging": {"loggers": []}}
    snapshot = deepcopy(original)
    scaled = {"subnet4": [{"subnet": "198.18.0.0/16"}]}
    state = {"desired": original}
    observed = []

    def desired_config(namespace, pod, config=None):
        if config is None:
            return state["desired"]
        state["desired"] = config
        if failure == "publish" and config["Dhcp4"] == scaled:
            raise RuntimeError("publish failed after mutation")

    def wait_for_subnet(namespace, pod, subnet, *, present):
        if present:
            # The sidecar must read the seeded desired state, not restore baseline.
            assert state["desired"]["Dhcp4"] == scaled
            assert state["desired"]["Logging"] == original["Logging"]
            if failure == "wait":
                raise RuntimeError("reconciliation failed")
        else:
            assert state["desired"] == snapshot
        observed.append(present)

    monkeypatch.setattr(scale, "_kea_desired_config", desired_config)
    monkeypatch.setattr(scale, "_wait_for_kea_subnet", wait_for_subnet)

    def exercise():
        with scale._temporary_kea_dhcp4_config("test", "dhcp", scaled, "198.18.0.0/16"):
            assert state["desired"]["Dhcp4"] == scaled
            if failure == "body":
                raise RuntimeError("assertion failed")

    if failure:
        with pytest.raises(RuntimeError):
            exercise()
    else:
        exercise()
    assert state["desired"] == snapshot
    assert original == snapshot
    assert observed[-1] is False


@pytest.mark.parametrize("present", [True, False])
def test_scale_fixture_waits_until_live_config_matches(monkeypatch, present):
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
