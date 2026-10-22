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
"""Persistent LLDP fixtures for Ubuntu hosts in AIR demos."""

from __future__ import annotations

import json
import re

import yaml

from nv_config_manager_installer.air_sim.models import DeviceInfo


def generate_lldp_host_cloud_init(device: DeviceInfo) -> str | None:
    """Configure opted-in hosts using MACs instead of image-specific NIC ordering."""
    config = device.air_config.get("lldp")
    if config is None:
        return None
    if device.platform != "Ubuntu" or not isinstance(config, dict) or not config:
        raise ValueError(f"{device.name}: _air.lldp requires Ubuntu and an interface mapping")
    if "eth0" not in device.interfaces:
        raise ValueError(f"{device.name}: LLDP hosts require an eth0 exit interface")

    commands = [
        "configure lldp portidsubtype ifname",
        f"configure system hostname {json.dumps(device.name)}",
    ]
    ethernets = {}
    runtime_names = set()
    for interface in device.interfaces:
        settings = config.get(interface, {})
        if not isinstance(settings, dict):
            raise ValueError(f"{device.name}: invalid LLDP settings for {interface}")
        runtime_name = settings.get("name", interface)
        if not isinstance(runtime_name, str) or not re.fullmatch(
            r"[a-zA-Z0-9_.-]{1,15}", runtime_name
        ):
            raise ValueError(f"{device.name}: invalid Linux interface name {runtime_name!r}")
        if runtime_name in runtime_names:
            raise ValueError(f"{device.name}: duplicate Linux interface name {runtime_name}")
        runtime_names.add(runtime_name)
        mac = device.interface_macs.get(interface, "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
            raise ValueError(f"{device.name}: {interface} requires a MAC for LLDP host setup")
        ethernets[interface] = {
            "match": {"macaddress": mac},
            "set-name": runtime_name,
            "dhcp4": interface == "eth0",
            "optional": True,
        }
        if settings:
            description = settings.get("port_description")
            if (
                not isinstance(description, str)
                or not re.fullmatch(r'[^\s"\\]+ [^\s"\\]+', description)
                or interface == "eth0"
            ):
                raise ValueError(
                    f"{device.name}: {interface} requires a two-token port_description"
                )
            commands.append(
                f"configure ports {runtime_name} lldp portdescription {json.dumps(description)}"
            )
    unknown = set(config) - set(device.interfaces)
    if unknown:
        raise ValueError(f"{device.name}: unknown LLDP interfaces: {sorted(unknown)}")

    network = {"network": {"version": 2, "renderer": "networkd", "ethernets": ethernets}}
    document = {
        "write_files": [
            {
                "path": "/etc/netplan/90-nvcm-lldp.yaml",
                "permissions": "0600",
                "content": yaml.safe_dump(network, sort_keys=False),
            },
            {
                "path": "/etc/lldpd.d/90-nvcm-demo.conf",
                "permissions": "0644",
                "content": "\n".join(commands) + "\n",
            },
        ],
        "runcmd": [
            [
                "bash",
                "-ec",
                (
                    "netplan apply\n"
                    "/lib/systemd/systemd-networkd-wait-online --interface=eth0 --timeout=120\n"
                    "export DEBIAN_FRONTEND=noninteractive\n"
                    "apt-get -o Acquire::Retries=3 update\n"
                    "apt-get -o Acquire::Retries=3 install -y lldpd\n"
                    "systemctl enable lldpd\n"
                    "systemctl restart lldpd\n"
                    "lldpcli show interfaces details\n"
                ),
            ],
        ],
    }
    return "#cloud-config\n" + yaml.safe_dump(document, sort_keys=False)
