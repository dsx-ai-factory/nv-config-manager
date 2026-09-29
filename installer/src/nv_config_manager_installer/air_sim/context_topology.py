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
"""Build temporary DSX Air topology input from mock_topology context data."""

from __future__ import annotations

import atexit
import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from nv_config_manager_installer.air_sim.constants import DEFAULT_MOCK_CONTEXT_ROOT


def _render_deployment_name(value: Any, deployment_name: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{ deployment_name }}", deployment_name).replace(
            "{{deployment_name}}", deployment_name
        )
    if isinstance(value, list):
        return [_render_deployment_name(item, deployment_name) for item in value]
    if isinstance(value, dict):
        return {key: _render_deployment_name(item, deployment_name) for key, item in value.items()}
    return value


def _name(value: Any, default: str = "") -> str:
    if isinstance(value, dict):
        return str(value.get("name") or default)
    if value is None:
        return default
    return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def build_site_design_from_mock_context(
    blueprint: str,
    deployment_name: str,
    *,
    context_root: Path = DEFAULT_MOCK_CONTEXT_ROOT,
) -> dict[str, Any]:
    """Return a DSX Air topology-builder site design from mock_topology context files."""
    context_dir = context_root / blueprint
    devices_dir = context_dir / "devices"
    if not devices_dir.is_dir():
        raise ValueError(f"Mock topology context has no devices directory: {devices_dir}")

    locations_doc = _render_deployment_name(
        _load_yaml(context_dir / "locations.yaml"), deployment_name
    )
    prefixes_doc = _render_deployment_name(
        _load_yaml(context_dir / "prefixes.yaml"), deployment_name
    )
    bgp_doc = _render_deployment_name(
        _load_yaml(context_dir / "bgp_routing_instances.yaml"), deployment_name
    )
    bgp_asns = {
        str(instance.get("device")): instance.get("asn")
        for instance in bgp_doc.get("bgp_routing_instances", [])
        if instance.get("device") and instance.get("asn") is not None
    }

    location_hierarchy = []
    for loc in locations_doc.get("locations", []):
        location_hierarchy.append(
            {
                "name": loc.get("name"),
                "type": loc.get("location_type", loc.get("type", "Region")),
                "tenant": locations_doc.get("global_defaults", {}).get("tenant"),
            }
        )

    prefixes = []
    for prefix in prefixes_doc.get("aggregate_prefixes", []):
        prefixes.append(
            {
                "prefix": prefix.get("prefix"),
                "role": prefix.get("role"),
                "tags": prefix.get("tags", []),
            }
        )

    devices = []
    interfaces = []
    ip_addresses = []
    connections = []

    for json_file in sorted(devices_dir.glob("*.json")):
        with open(json_file) as f:
            payload = _render_deployment_name(json.load(f), deployment_name)
        device = (payload.get("data") or {}).get("device") or {}
        if not device.get("name"):
            continue

        device_name = device["name"]
        normalized_device = {
            "name": device_name,
            "device_type": {
                "manufacturer": _name((device.get("device_type") or {}).get("manufacturer")),
                "model": (device.get("device_type") or {}).get("model", "Unknown"),
            },
            "status": _name(device.get("status"), "Active"),
            "role": _name(device.get("role"), "Unknown"),
            "platform": _name(device.get("platform")),
            "tenant": _name(device.get("tenant")),
            "serial": device.get("serial", ""),
            "local_config_context_data": device.get("config_context", {}),
            "tags": [_name(tag) for tag in device.get("tags", [])],
        }
        if device_name in bgp_asns:
            normalized_device["bgp_asn"] = bgp_asns[device_name]
        if device.get("_air"):
            normalized_device["_air"] = device["_air"]
        devices.append(normalized_device)

        for intf in device.get("interfaces", []):
            intf_name = intf.get("name")
            if not intf_name:
                continue
            normalized_intf = {
                "device": device_name,
                "name": intf_name,
                "type": intf.get("type", "1000base-t"),
                "description": intf.get("description", ""),
                "mac_address": intf.get("mac_address"),
                "role": _name(intf.get("role")),
                "mgmt_only": bool(intf.get("mgmt_only", False)),
                "dhcp_pool": bool(intf.get("dhcp_pool", False)),
                "dhcp_reserve": bool(intf.get("dhcp_reserve", False)),
                "mode": intf.get("mode"),
                "mtu": intf.get("mtu"),
            }
            if intf.get("untagged_vlan"):
                vlan = intf["untagged_vlan"]
                normalized_intf["untagged_vlan"] = (
                    vlan.get("vid") if isinstance(vlan, dict) else vlan
                )
            interfaces.append({k: v for k, v in normalized_intf.items() if v not in (None, "")})

            for ip in intf.get("ip_addresses", []):
                address = ip.get("address")
                if not address:
                    continue
                ip_addresses.append(
                    {
                        "device": device_name,
                        "interface": intf_name,
                        "address": address,
                        "mask_length": ip.get("mask_length"),
                    }
                )

            connected = intf.get("connected_interface") or {}
            remote_device = connected.get("device") or {}
            remote_name = remote_device.get("name")
            remote_interface = connected.get("name")
            if remote_name and remote_interface:
                connections.append(
                    {
                        "source": {"device": device_name, "component": {"name": intf_name}},
                        "destination": {
                            "device": remote_name,
                            "component": {"name": remote_interface},
                        },
                    }
                )

    return {
        "location_hierarchy": location_hierarchy,
        "prefixes": prefixes,
        "devices": devices,
        "interfaces": interfaces,
        "ip_addresses": ip_addresses,
        "cabling_assignments": {"connections": connections},
    }


def write_site_design_from_mock_context(
    blueprint: str,
    deployment_name: str,
    *,
    context_root: Path = DEFAULT_MOCK_CONTEXT_ROOT,
) -> str:
    """Write a temporary site-design YAML generated from mock context and return its path."""
    site_design = build_site_design_from_mock_context(
        blueprint,
        deployment_name,
        context_root=context_root,
    )
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix=f"air-{blueprint}-", delete=False
    )
    yaml.safe_dump(site_design, tmp, default_flow_style=False, sort_keys=False)
    tmp.close()
    atexit.register(Path(tmp.name).unlink, missing_ok=True)
    return tmp.name
