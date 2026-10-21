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
"""LLDP-only host boot configuration and separation from the SpX demo."""

from pathlib import Path

import pytest
import yaml

from nv_config_manager_installer.air_sim.context_topology import build_site_design_from_mock_context
from nv_config_manager_installer.air_sim.host_cloud_init import generate_lldp_host_cloud_init
from nv_config_manager_installer.air_sim.models import DeviceInfo
from nv_config_manager_installer.air_sim.topology import AirTopologyBuilder


def _host() -> DeviceInfo:
    return DeviceInfo(
        name="gpu-tray",
        platform="Ubuntu",
        role="Compute-Tray",
        model="Server",
        firmware_version="",
        interfaces=["eth0", "eth1"],
        interface_macs={"eth0": "02:00:00:00:00:01", "eth1": "02:00:00:00:00:02"},
        air_config={"lldp": {"eth1": {"name": "A56p0", "port_description": "gpu-tray C1-2-L1"}}},
    )


def test_persistent_lldp_fixture_uses_mac_matching_and_separate_bootstrap() -> None:
    config = yaml.safe_load(generate_lldp_host_cloud_init(_host()))
    files = {entry["path"]: entry for entry in config["write_files"]}
    network = yaml.safe_load(files["/etc/netplan/90-nvcm-lldp.yaml"]["content"])
    interfaces = network["network"]["ethernets"]
    assert interfaces["eth0"]["dhcp4"] is True
    assert interfaces["eth1"]["dhcp4"] is False
    assert interfaces["eth1"]["match"] == {"macaddress": "02:00:00:00:00:02"}
    assert interfaces["eth1"]["set-name"] == "A56p0"
    assert files["/etc/netplan/90-nvcm-lldp.yaml"]["permissions"] == "0600"
    lldp = files["/etc/lldpd.d/90-nvcm-demo.conf"]["content"]
    assert "configure lldp portidsubtype ifname" in lldp
    assert 'configure ports A56p0 lldp portdescription "gpu-tray C1-2-L1"' in lldp
    # Settings are on disk before the daemon restarts, not ephemeral CLI overrides.
    command, option, script = config["runcmd"][0]
    assert (command, option) == ("bash", "-ec")
    assert script.index("netplan apply") < script.index("apt-get")
    assert "systemctl restart lldpd" in script
    assert "lldpcli configure" not in script


@pytest.mark.parametrize("bad_description", ["", "one-token", "one two three", "a\nb", 'a "b"'])
def test_invalid_port_description_is_rejected(bad_description: str) -> None:
    host = _host()
    host.air_config["lldp"]["eth1"]["port_description"] = bad_description
    with pytest.raises(ValueError, match="two-token"):
        generate_lldp_host_cloud_init(host)


def test_unconfigured_hosts_are_unchanged_and_missing_macs_fail() -> None:
    host = _host()
    host.air_config = {}
    assert generate_lldp_host_cloud_init(host) is None
    host = _host()
    host.interface_macs = {}
    with pytest.raises(ValueError, match="requires a MAC"):
        generate_lldp_host_cloud_init(host)


def test_superpod_hyperreach_fixture_keeps_spx_hosts_and_ports_separate(tmp_path: Path) -> None:
    design = build_site_design_from_mock_context("air_superpod", "demo")
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump(design))
    builder = AirTopologyBuilder(str(path))
    topology = builder.build_topology()
    endpoints_by_mac = {}
    for link in topology["links"]:
        for endpoint in link:
            if isinstance(endpoint, dict) and endpoint.get("mac"):
                identity = (endpoint["node"], endpoint["interface"])
                mac = endpoint["mac"].lower()
                assert mac not in endpoints_by_mac or endpoints_by_mac[mac] == identity
                endpoints_by_mac[mac] = identity
    host_name = "su02-hyperreach-host"
    host = builder.devices[host_name]
    assert host.interface_macs["eth0"] != builder.devices["oob-mgmt-server"].interface_macs["eth1"]
    config = yaml.safe_load(generate_lldp_host_cloud_init(host))
    assert len(host.interfaces) == 3
    interfaces = {(i["device"], i["name"]): i for i in design["interfaces"]}
    for rack, lane in [(1, "C1-2-L1"), (2, "C1-2-L2")]:
        port = f"eth{rack}"
        assert interfaces[(host_name, port)]["label"] == lane
        assert "mac_address" not in interfaces[(host_name, port)]  # No FDB fallback masks LLDP.
        assert host.interface_macs[port]
        leaf = f"su01-cin-leaf-r0{rack}"
        assert any(
            {"node": leaf, "interface": "swp2"} in link
            and any(isinstance(ep, dict) and ep.get("node") == host_name for ep in link)
            for link in topology["links"]
        )
        spx_host = f"su01-compute-tray-r0{rack}"
        assert generate_lldp_host_cloud_init(builder.devices[spx_host]) is None
        assert interfaces[(spx_host, "ens3")]["description"] == f"{leaf}:swp1"
        assert "label" not in interfaces[(spx_host, "ens3")]
    assert [i["address"] for i in design["ip_addresses"] if i["interface"] == "ens3"] == [
        "172.16.0.0/31",
        "172.16.0.128/31",
    ]
    assert config["runcmd"]
