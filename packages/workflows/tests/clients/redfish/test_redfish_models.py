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
"""Tests for the relocated Redfish models."""

from nv_config_manager_workflows.clients.redfish import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishNic,
    RedfishServer,
    RedfishVendor,
)


def test_redfish_models_preserve_fields_and_mac_normalization() -> None:
    dpu = RedfishDpu(
        address="192.0.2.20",
        vendor=RedfishVendor.BLUEFIELD,
        mac="58:a2:e1:72:dd:c5",
        ports=[RedfishDpuPort(name="eth0", mac="58:a2:e1:72:dd:b1")],
        base_mac="58:a2:e1:72:dd:a0",
        serial="DPU-SERIAL",
    )
    nic = RedfishNic(
        name="slot-1",
        slot="1",
        mac="58:a2:e1:72:dd:b2",
        dpu=dpu,
    )
    server = RedfishServer(
        address="192.0.2.10",
        vendor=RedfishVendor.DELL,
        mac="c8:4b:d6:7a:e9:e2",
        serial="SERVER-SERIAL",
        nics=[nic],
    )

    assert server.model_dump() == {
        "address": "192.0.2.10",
        "port": 443,
        "vendor": RedfishVendor.DELL,
        "mac": "C8-4B-D6-7A-E9-E2",
        "serial": "SERVER-SERIAL",
        "nics": [
            {
                "name": "slot-1",
                "slot": "1",
                "mac": "58-A2-E1-72-DD-B2",
                "dpu": {
                    "address": "192.0.2.20",
                    "port": 443,
                    "vendor": RedfishVendor.BLUEFIELD,
                    # RedfishDpu preserves this inherited-field behavior from the service model.
                    "mac": "58:a2:e1:72:dd:c5",
                    "ports": [{"name": "eth0", "mac": "58-A2-E1-72-DD-B1"}],
                    "base_mac": "58-A2-E1-72-DD-A0",
                    "serial": "DPU-SERIAL",
                },
            }
        ],
    }


def test_redfish_host_string_format_is_unchanged() -> None:
    host = RedfishHost(
        address="192.0.2.10",
        port=8443,
        vendor=RedfishVendor.LENOVO,
        mac="c8:4b:d6:7a:e9:e2",
    )

    assert str(host) == "Lenovo/C8-4B-D6-7A-E9-E2/192.0.2.10:8443"
