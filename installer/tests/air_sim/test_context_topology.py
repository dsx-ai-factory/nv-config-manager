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
"""Tests for DSX Air topology generation from mock_topology-shaped context data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from nv_config_manager_installer.air_sim.context_topology import (
    build_site_design_from_mock_context,
)
from nv_config_manager_installer.air_sim.topology import AirTopologyBuilder


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_context(context_root: Path) -> None:
    context_dir = context_root / "demo_blueprint"
    devices_dir = context_dir / "devices"
    devices_dir.mkdir(parents=True)

    _write_yaml(
        context_dir / "locations.yaml",
        {
            "global_defaults": {"tenant": "Public Demo"},
            "locations": [{"name": "SITE01 - {{ deployment_name }}", "location_type": "Site"}],
        },
    )
    _write_yaml(
        context_dir / "prefixes.yaml",
        {
            "aggregate_prefixes": [
                {"prefix": "172.18.0.0/16", "role": "Service-LB-Source", "tags": ["lb-allowed"]},
                {"prefix": "10.120.0.0/16", "role": "OOB-Aggregate", "tags": ["relay-return"]},
            ]
        },
    )
    _write_yaml(
        context_dir / "bgp_routing_instances.yaml",
        {"bgp_routing_instances": [{"device": "oob-mgmt-server", "asn": 65000}]},
    )
    _write_json(
        devices_dir / "oob-mgmt-server.json",
        {
            "data": {
                "device": {
                    "name": "oob-mgmt-server",
                    "role": {"name": "OOB-Server"},
                    "device_type": {"manufacturer": {"name": "Generic"}, "model": "Server"},
                    "status": {"name": "Active"},
                    "serial": "44:38:39:00:00:01",
                    "config_context": {},
                    "_air": {"os": "generic/ubuntu2404", "cpu_mode": "host-model"},
                    "interfaces": [
                        {
                            "name": "eth1",
                            "type": "1000base-t",
                            "mac_address": "44:38:39:01:00:01",
                            "ip_addresses": [
                                {
                                    "address": "10.120.0.0/31",
                                    "mask_length": 31,
                                    "ip_version": 4,
                                }
                            ],
                        }
                    ],
                }
            }
        },
    )
    _write_json(
        devices_dir / "oob-mleaf-01.json",
        {
            "data": {
                "device": {
                    "name": "oob-mleaf-01",
                    "role": {"name": "OOB-MLEAF"},
                    "device_type": {"manufacturer": {"name": "NVIDIA"}, "model": "SN2201"},
                    "platform": {"name": "Cumulus Linux"},
                    "status": {"name": "Provisioning"},
                    "serial": "44:38:39:00:00:02",
                    "config_context": {"intended-firmware": {"version": "5.16.1"}},
                    "tags": [{"name": "ztp"}],
                    "interfaces": [
                        {
                            "name": "eth0",
                            "type": "1000base-t",
                            "mgmt_only": True,
                            "mac_address": "44:38:39:00:00:02",
                            "ip_addresses": [],
                        },
                        {
                            "name": "swp1",
                            "type": "1000base-t",
                            "role": {"name": "Uplink"},
                            "dhcp_pool": True,
                            "dhcp_reserve": True,
                            "ip_addresses": [
                                {
                                    "address": "10.120.0.1/31",
                                    "mask_length": 31,
                                    "ip_version": 4,
                                }
                            ],
                            "connected_interface": {
                                "name": "eth1",
                                "device": {"name": "oob-mgmt-server"},
                            },
                        },
                    ],
                }
            }
        },
    )


def _devices_by_name(site_design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {device["name"]: device for device in site_design["devices"]}


def test_context_builder_preserves_topology_metadata(tmp_path: Path) -> None:
    _write_context(tmp_path)

    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    devices = _devices_by_name(site_design)
    oob_uplink = next(
        interface
        for interface in site_design["interfaces"]
        if interface["device"] == "oob-mleaf-01" and interface["name"] == "swp1"
    )

    assert devices["oob-mgmt-server"]["bgp_asn"] == 65000
    assert devices["oob-mleaf-01"]["local_config_context_data"] == {
        "intended-firmware": {"version": "5.16.1"}
    }
    assert oob_uplink["dhcp_pool"] is True
    assert oob_uplink["dhcp_reserve"] is True
    assert site_design["prefixes"] == [
        {"prefix": "172.18.0.0/16", "role": "Service-LB-Source", "tags": ["lb-allowed"]},
        {"prefix": "10.120.0.0/16", "role": "OOB-Aggregate", "tags": ["relay-return"]},
    ]


def test_air_topology_builder_resolves_oob_server_bgp_asn(tmp_path: Path) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    builder = AirTopologyBuilder(str(topology_path))

    assert builder.resolve_device_bgp_asn("oob-mgmt-server") == "65000"


def test_air_topology_builder_uses_resolved_cumulus_image(tmp_path: Path) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    builder = AirTopologyBuilder(str(topology_path))
    builder.set_cumulus_image_overrides({"5.16.1": "cumulus-linux-vx-amd64-5.16.1.0008.qcow2"})
    topology = builder.build_topology()

    assert topology["nodes"]["oob-mleaf-01"]["os"] == ("cumulus-linux-vx-amd64-5.16.1.0008.qcow2")


def test_air_topology_builder_preserves_explicit_oob_server_cpu_mode(tmp_path: Path) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    topology = AirTopologyBuilder(str(topology_path)).build_topology()

    assert topology["nodes"]["oob-mgmt-server"]["cpu_mode"] == "host-model"
    assert "cpu_mode" not in topology["nodes"]["oob-mleaf-01"]


@pytest.mark.parametrize("device_name", ["oob-mgmt-server", "oob-mleaf-01"])
def test_air_topology_builder_accepts_null_air_config(tmp_path: Path, device_name: str) -> None:
    """A YAML null AIR block uses defaults for both server and switch nodes."""
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    device = next(device for device in site_design["devices"] if device["name"] == device_name)
    device["_air"] = None
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    builder = AirTopologyBuilder(str(topology_path))
    topology = builder.build_topology()

    assert builder.devices[device["name"]].air_enabled is True
    assert builder.devices[device["name"]].air_config == {}
    assert device["name"] in topology["nodes"]


def test_air_topology_builder_omits_inventory_only_devices_and_their_cables(
    tmp_path: Path,
) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    site_design["devices"].append(
        {
            "name": "inventory-only-node",
            "role": "GPU-Node",
            "platform": "Ubuntu",
            "device_type": {"manufacturer": "NVIDIA", "model": "Compute Tray"},
            "_air": {"enabled": False},
        }
    )
    site_design["interfaces"].extend(
        [
            {
                "device": "inventory-only-node",
                "name": "bmc",
                "type": "1000base-t",
            },
            {
                "device": "oob-mleaf-01",
                "name": "swp2",
                "type": "1000base-t",
            },
        ]
    )
    site_design["cabling_assignments"]["connections"].append(
        {
            "source": {"device": "inventory-only-node", "component": {"name": "bmc"}},
            "destination": {"device": "oob-mleaf-01", "component": {"name": "swp2"}},
        }
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    builder = AirTopologyBuilder(str(topology_path))
    topology = builder.build_topology()

    assert "inventory-only-node" in builder.devices
    assert "inventory-only-node" not in topology["nodes"]
    assert not any(
        isinstance(endpoint, dict) and endpoint.get("node") == "inventory-only-node"
        for link in topology["links"]
        for endpoint in link
    )
    assert [
        {"node": "oob-mleaf-01", "interface": "swp2"},
        "unconnected",
    ] in topology["links"]


@pytest.mark.parametrize("air_enabled", [False, True])
def test_cumulus_eth0_without_mac_requires_air_enabled(
    tmp_path: Path,
    air_enabled: bool,
) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    site_design["devices"].append(
        {
            "name": "inventory-switch",
            "platform": "Cumulus Linux",
            "role": "SMN-Leaf",
            "device_type": {"manufacturer": "NVIDIA", "model": "SN5600"},
            "_air": {"enabled": air_enabled},
        }
    )
    site_design["interfaces"].append(
        {
            "device": "inventory-switch",
            "name": "eth0",
            "type": "1000base-t",
        }
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    if air_enabled:
        with pytest.raises(ValueError, match="interface eth0 must define"):
            AirTopologyBuilder(str(topology_path)).build_topology()
    else:
        builder = AirTopologyBuilder(str(topology_path))
        topology = builder.build_topology()
        assert "inventory-switch" in builder.devices
        assert "inventory-switch" not in topology["nodes"]


def test_air_topology_builder_omits_inventory_only_devices_in_minimal_mode(
    tmp_path: Path,
) -> None:
    _write_context(tmp_path)
    site_design = build_site_design_from_mock_context(
        "demo_blueprint", "demo", context_root=tmp_path
    )
    site_design["devices"].append(
        {
            "name": "inventory-only-node",
            "role": "GPU-Node",
            "platform": "Ubuntu",
            "device_type": {"manufacturer": "NVIDIA", "model": "Compute Tray"},
            "_air": {"enabled": False},
        }
    )
    topology_path = tmp_path / "site-design.yaml"
    _write_yaml(topology_path, site_design)

    topology = AirTopologyBuilder(str(topology_path), minimal_mode=True).build_topology()

    assert all("Compute-Tray" not in node for node in topology["nodes"])
