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
"""Unit tests for the provider boundary used by the integration suite."""

from __future__ import annotations

import pytest

from tests.integration.dcim_adapter import (
    NautobotIntegrationAdapter,
    load_dcim_integration_adapter,
)


def test_loader_resolves_adapter_class() -> None:
    adapter = load_dcim_integration_adapter(
        "tests.integration.dcim_adapter:NautobotIntegrationAdapter"
    )

    assert adapter is NautobotIntegrationAdapter


@pytest.mark.parametrize("spec", ["", "module", ":Class", "module:"])
def test_loader_rejects_invalid_spec(spec: str) -> None:
    with pytest.raises(ValueError, match="module:class"):
        load_dcim_integration_adapter(spec)


def test_nautobot_adapter_normalizes_provider_records(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = NautobotIntegrationAdapter("https://nautobot.example/", "token")
    monkeypatch.setattr(
        adapter,
        "_graphql",
        lambda query: {
            "config_manager_devices": [
                {
                    "id": "managed-1",
                    "render_enabled": True,
                    "ztp_enabled": True,
                    "backup_enabled": False,
                    "intended_config": {"path": "leaf-1/startup.yaml", "commit_id": "abc"},
                    "device": {
                        "id": "device-1",
                        "name": "leaf-1",
                        "serial": "serial-1",
                        "status": {"name": "Provisioning"},
                        "platform": {"name": "Cumulus Linux"},
                        "role": {"name": "Leaf"},
                        "primary_ip4": {"address": "192.0.2.1/24"},
                        "interfaces": [
                            {
                                "name": "eth0",
                                "mac_address": "00:11:22:33:44:55",
                                "mgmt_only": True,
                                "role": {"name": "Uplink"},
                                "ip_addresses": [{"address": "192.0.2.1/24"}],
                            }
                        ],
                    },
                }
            ],
            "reserved_ips": [{"interfaces": [{"device": {"id": "device-1"}}]}],
        },
    )

    devices = adapter.list_devices(
        ztp_enabled=True,
        platform="Cumulus Linux",
        include_interfaces=True,
    )

    assert devices == [
        {
            "id": "device-1",
            "name": "leaf-1",
            "serial": "serial-1",
            "status": "Provisioning",
            "platform": "Cumulus Linux",
            "role": "Leaf",
            "primary_ip4": "192.0.2.1/24",
            "interfaces": [
                {
                    "name": "eth0",
                    "mac_address": "00:11:22:33:44:55",
                    "mgmt_only": True,
                    "role": "Uplink",
                    "ip_addresses": [{"address": "192.0.2.1/24"}],
                }
            ],
            "dhcp_reserved": True,
            "render_enabled": True,
            "ztp_enabled": True,
            "backup_enabled": False,
            "intended_config": {"path": "leaf-1/startup.yaml", "commit_id": "abc"},
        }
    ]
