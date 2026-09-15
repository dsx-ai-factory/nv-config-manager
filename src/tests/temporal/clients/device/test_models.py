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

import pytest

from nv_config_manager.temporal.client.device.models import DeviceArpTable


def test_add_entry_normalizes_and_indexes_ip_mac_and_interface():
    table = DeviceArpTable()
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")

    mac = "00-11-22-33-44-55"
    assert table.ip_to_mac == {"192.0.2.1": [mac]}
    assert table.mac_to_ip == {mac: ["192.0.2.1"]}
    assert table.interface_to_mac == {"swp1": [mac]}


def test_add_entry_does_not_duplicate_the_same_mapping():
    table = DeviceArpTable()
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")
    table.add_entry("192.0.2.1", "00:11:22:33:44:55", "swp1")

    mac = "00-11-22-33-44-55"
    assert table.ip_to_mac["192.0.2.1"] == [mac]
    assert table.interface_to_mac["swp1"] == [mac]


def test_add_entry_raises_on_invalid_ip():
    table = DeviceArpTable()
    with pytest.raises(ValueError):
        table.add_entry("not-an-ip", "00:11:22:33:44:55", "swp1")
