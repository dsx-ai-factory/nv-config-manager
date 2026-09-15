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
"""Device workflow mixin tests."""

import pytest
from nv_config_manager_dcim.workflow_models import DeviceData, NetworkDeviceData, Platform

from nv_config_manager_workflows import search_attributes
from nv_config_manager_workflows.mixins import DeviceMixin


@pytest.fixture
def upserted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, list[object]]]:
    """Collect search attributes after the initial-attribute filter."""
    calls: list[dict[str, list[object]]] = []
    monkeypatch.setattr(search_attributes.workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(search_attributes.workflow, "upsert_search_attributes", calls.append)
    return calls


def test_common_device_metadata_is_attached(upserted: list[dict[str, list[object]]]) -> None:
    device = DeviceData(
        id="42",
        name="server01",
        role="server",
        site="rdu",
        device_type="compute",
    )

    DeviceMixin.attach_device_search_attributes(device)

    assert upserted == [
        {
            "DeviceID": ["42"],
            "DeviceRole": ["server"],
            "Site": ["rdu"],
            "DeviceName": ["server01"],
        }
    ]


def test_network_device_platform_is_attached(upserted: list[dict[str, list[object]]]) -> None:
    device = NetworkDeviceData(
        id="7",
        name="leaf01",
        role="leaf",
        site="rdu",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    DeviceMixin.attach_device_search_attributes(device)

    assert upserted[0]["DevicePlatform"] == [Platform.CUMULUS_LINUX]
