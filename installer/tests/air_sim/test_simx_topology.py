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
"""SimX node and link metadata survives the site-design conversion."""

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


@pytest.fixture
def simx_design() -> dict[str, Any]:
    return {
        "devices": [
            {
                "name": "host01",
                "platform": "Ubuntu",
                "_air": {
                    "os": "example/ubuntu-doca",
                    "cpu": 4,
                    "memory": 8192,
                    "storage": 20,
                    "emulation_type": "HOST",
                    "config_only_features": True,
                    "model": "host",
                    "simulator_image": "example-simx-image",
                    "network_pci": {
                        "nic0": {
                            "emulation_type": "NIC_ETHERNET",
                            "model": "connectx8",
                            "dpa": True,
                            "port_type": "ethernet",
                            "number_of_ports": 2,
                            "pci_addresses": ["0000:03:00.0", "0000:03:00.1"],
                        }
                    },
                    "nic_model": "virtio",
                    "cpu_mode": "custom",
                    "cpu_options": [],
                    "secureboot": False,
                    "pxehost": False,
                    "features": {"uefi": False},
                    "storage_pci": {},
                    "labels": None,
                    "positioning": {"x": 200, "y": 400},
                    "lldp": False,
                },
            },
            {
                "name": "leaf01",
                "platform": "Cumulus Linux",
                "local_config_context_data": {"intended-firmware": {"version": "5.18.0"}},
                "_air": {
                    "cpu": 3,
                    "memory": 4096,
                    "storage": 20,
                    "cpu_mode": "custom",
                    "cpu_options": [],
                    "secureboot": False,
                    "features": {"uefi": False},
                    "os": "ignored-by-cumulus-image-resolution",
                },
            },
        ],
        "interfaces": [
            {
                "device": "host01",
                "name": "eth_p0",
                "mac_address": "02:00:00:00:00:01",
                "_air": {"network_pci": "nic0"},
            },
            {"device": "host01", "name": "eth_p1", "_air": {"network_pci": "nic0"}},
            {"device": "host01", "name": "eth0"},
            {"device": "leaf01", "name": "eth0", "mac_address": "02:00:00:00:01:01"},
            {"device": "leaf01", "name": "swp1"},
        ],
        "cabling_assignments": {
            "connections": [
                {
                    "source": {"device": "host01", "component": {"name": "eth_p0"}},
                    "destination": {"device": "leaf01", "component": {"name": "swp1"}},
                }
            ]
        },
    }


def _builder(tmp_path: Path, design: dict[str, Any], **kwargs: Any) -> AirTopologyBuilder:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(design), encoding="utf-8")
    return AirTopologyBuilder(str(path), **kwargs)


def test_simx_nodes_and_dual_port_links_survive_conversion(tmp_path, simx_design):
    topology = _builder(tmp_path, simx_design).build_topology()

    expected_host = {k: v for k, v in simx_design["devices"][0]["_air"].items() if k != "lldp"}
    assert topology["nodes"]["host01"] == expected_host
    assert [
        {
            "node": "host01",
            "interface": "eth_p0",
            "mac": "02:00:00:00:00:01",
            "network_pci": "nic0",
        },
        {"node": "leaf01", "interface": "swp1"},
    ] in topology["links"]
    assert [
        {"node": "host01", "interface": "eth_p1", "network_pci": "nic0"},
        "unconnected",
    ] in topology["links"]
    assert [{"node": "host01", "interface": "eth0"}, "unconnected"] in topology["links"]
    assert json.loads(json.dumps(topology)) == topology


def test_cumulus_options_preserve_resolved_image_precedence(tmp_path, simx_design):
    builder = _builder(tmp_path, simx_design)
    assert builder.build_topology()["nodes"]["leaf01"]["os"] == "cumulus-vx-5.18.0"
    builder.set_cumulus_image_overrides({"5.18.0": "resolved-cumulus-image"})

    assert builder.build_topology()["nodes"]["leaf01"] == {
        "os": "resolved-cumulus-image",
        "cpu": 3,
        "memory": 4096,
        "storage": 20,
        "cpu_mode": "custom",
        "cpu_options": [],
        "secureboot": False,
        "features": {"uefi": False},
    }


def test_simx_link_metadata_is_retained_for_exit_interfaces(tmp_path, simx_design):
    simx_design["interfaces"][1]["description"] = "exit"
    topology = _builder(tmp_path, simx_design).build_topology()

    assert [
        {"node": "host01", "interface": "eth_p1", "network_pci": "nic0"},
        "exit",
    ] in topology["links"]


@pytest.mark.parametrize("reference", ["missing-nic", "", 123, {"name": "nic0"}])
def test_unknown_or_invalid_nic_references_fail_before_air_creation(
    tmp_path, simx_design, reference
):
    simx_design["interfaces"][0]["_air"]["network_pci"] = reference
    with pytest.raises(ValueError, match="host01 eth_p0 _air.network_pci must name a NIC"):
        _builder(tmp_path, simx_design)


def test_inventory_only_simx_hosts_do_not_require_valid_air_nic_references(tmp_path, simx_design):
    simx_design["devices"][0]["_air"]["enabled"] = False
    simx_design["interfaces"][0]["_air"]["network_pci"] = "missing-nic"

    topology = _builder(tmp_path, simx_design).build_topology()

    assert set(topology["nodes"]) == {"leaf01"}
    assert [{"node": "leaf01", "interface": "swp1"}, "unconnected"] in topology["links"]


def test_minimal_mode_rejects_lossy_simx_grouping(tmp_path, simx_design):
    with pytest.raises(ValueError, match="SimX network_pci requires full topology mode"):
        _builder(tmp_path, simx_design, minimal_mode=True).build_topology()


def test_mock_context_preserves_simx_node_and_interface_options(tmp_path, simx_design):
    devices_dir = tmp_path / "simx" / "devices"
    devices_dir.mkdir(parents=True)
    host = simx_design["devices"][0]
    host["interfaces"] = [
        {k: v for k, v in interface.items() if k != "device"}
        for interface in simx_design["interfaces"]
        if interface["device"] == host["name"]
    ]
    (devices_dir / "host.json").write_text(json.dumps({"data": {"device": host}}))

    design = build_site_design_from_mock_context("simx", "demo", context_root=tmp_path)
    topology = _builder(tmp_path, design).build_topology()

    assert topology["nodes"]["host01"]["network_pci"] == host["_air"]["network_pci"]
    assert [
        {
            "node": "host01",
            "interface": "eth_p0",
            "mac": "02:00:00:00:00:01",
            "network_pci": "nic0",
        },
        "unconnected",
    ] in topology["links"]
