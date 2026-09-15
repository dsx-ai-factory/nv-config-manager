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
"""Jinja2 context for the mock topology design."""

import ipaddress
import json
from pathlib import Path
from typing import Any

import yaml
from django.utils.text import slugify
from nautobot_design_builder.context import Context, context_file

from ..device_validation import require_cumulus_primary_ip4_interface
from .maps import COLOR_NAME_MAP


def find_containing_aggregate(ip_prefix: str, aggregates: list[dict]) -> dict | None:
    """Find the smallest aggregate prefix that contains the given prefix.

    Args:
        ip_prefix: The prefix to find a container for (e.g., "10.240.227.0/31")
        aggregates: List of aggregate prefix dicts with 'prefix' key

    Returns:
        The smallest containing aggregate dict, or None if not found
    """
    try:
        target = ipaddress.ip_network(ip_prefix, strict=False)
    except ValueError:
        return None

    containing = []
    for agg in aggregates:
        try:
            agg_net = ipaddress.ip_network(agg["prefix"], strict=False)
            if target.subnet_of(agg_net) and target != agg_net:
                containing.append((agg_net.prefixlen, agg))
        except ValueError:
            continue

    if not containing:
        return None

    # Return the one with the longest prefix (smallest containing network)
    containing.sort(key=lambda x: x[0], reverse=True)
    return containing[0][1]


@context_file(*Path(__file__).parent.glob("common/*.yaml"))
class BaseContext(Context):
    """Base context for all mock topology designs."""

    blueprint: str
    deployment_name: str
    context_dir: str = "common"
    device_file_glob: str = "*.json"
    prune_dangling_connected_interfaces: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize context and load device JSON data."""
        super().__init__(*args, **kwargs)
        self.json = {}
        self._load_devices()
        if self.prune_dangling_connected_interfaces:
            self._prune_dangling_connected_interfaces()
        self._load_bgp_routing_instances()
        self._load_manufacturers()
        self._load_device_types()
        self._load_aggregate_prefixes()
        self._load_prefixes()
        self._load_prefix_gateway_relationships()
        self._assign_ip_address_parent_prefixes()
        self._load_vrfs()
        self._load_vlans()
        self._load_overlays()
        self._load_ib_pkeys()
        self._load_vrf_device_assignments()
        self._load_roles()
        self._load_tags()
        self._load_config_contexts()

    def _load_devices(self) -> None:
        """Load device data from JSON files in the context_dir/devices directory."""
        devices_dir = Path(__file__).parent / self.context_dir / "devices"
        self.json["devices"] = []
        self.json["overlay_payloads"] = []

        if devices_dir.exists():
            for json_file in sorted(devices_dir.glob(self.device_file_glob)):
                try:
                    with open(json_file) as f:
                        json_data = json.load(f)
                        data = json_data.get("data", {})
                        device = data.get("device")
                        if not device:
                            continue
                        self._ensure_mock_device_serial(device, json_file)
                        self._require_cumulus_eth0_mac(device, json_file)
                        require_cumulus_primary_ip4_interface(device, json_file)
                        self.json["devices"].append(device)
                        self.json["overlay_payloads"].append(
                            {
                                "device": device,
                                "vxlans": data.get("vxlans", []),
                                "overlay_assignments": data.get("overlay_assignments", []),
                            }
                        )
                except (json.JSONDecodeError, OSError) as e:
                    print(f"Warning: Could not load {json_file}: {e}")

    @staticmethod
    def _ensure_mock_device_serial(device: dict[str, Any], source: Path) -> None:
        """Require Cumulus serials and give other mock devices stable serials."""
        if device.get("serial"):
            return
        platform_name = (device.get("platform") or {}).get("name", "")
        if "Cumulus" in platform_name:
            device_name = device.get("name") or source.name
            raise ValueError(
                f"Mock Cumulus device {device_name} in {source} must define "
                "serial for ZTP validation"
            )
        device_name = device.get("name") or str(device.get("id") or "device")
        device["serial"] = f"MOCK-{slugify(device_name).upper().replace('-', '')}"

    @staticmethod
    def _require_cumulus_eth0_mac(device: dict[str, Any], source: Path) -> None:
        """Require explicit eth0 MACs for Cumulus mock devices."""
        platform_name = (device.get("platform") or {}).get("name", "")
        if "Cumulus" not in platform_name:
            return
        eth0 = next(
            (
                interface
                for interface in device.get("interfaces", [])
                if interface.get("name") == "eth0"
            ),
            None,
        )
        if eth0 and eth0.get("mac_address"):
            return
        device_name = device.get("name") or source.name
        raise ValueError(
            f"Mock Cumulus device {device_name} in {source} must define "
            "interfaces[].name=eth0 mac_address for DHCP/ZTP reservations"
        )

    def _prune_dangling_connected_interfaces(self) -> None:
        """Remove interfaces connected to devices that are not in this mock sample."""
        device_names = {device["name"] for device in self.json["devices"] if device.get("name")}

        for device in self.json["devices"]:
            interfaces = []
            for interface in device.get("interfaces", []):
                connected_interface = interface.get("connected_interface")
                remote_device = (connected_interface or {}).get("device") or {}
                remote_name = remote_device.get("name")
                if (
                    remote_name
                    and remote_name not in device_names
                    and interface.get("name") != "eth0"
                ):
                    continue
                interfaces.append(interface)
            device["interfaces"] = interfaces

    def _load_bgp_routing_instances(self) -> None:
        """Load BGP plugin routing-instance seed data from the context directory."""
        bgp_file = Path(__file__).parent / self.context_dir / "bgp_routing_instances.yaml"
        self.json["bgp_routing_instances"] = []
        self.json["bgp_peerings"] = []

        if bgp_file.exists():
            try:
                with open(bgp_file) as f:
                    data = yaml.safe_load(f) or {}
                    self.json["bgp_routing_instances"] = data.get("bgp_routing_instances", [])
                    self.json["bgp_peerings"] = data.get("bgp_peerings", [])
            except (yaml.YAMLError, OSError) as e:
                print(f"Warning: Could not load {bgp_file}: {e}")

    def _load_manufacturers(self) -> None:
        """Load manufacturer data from JSON files in the context_dir/manufacturers directory."""
        manufacturers = set()
        for device in self.json["devices"]:
            if mfr_name := device.get("device_type", {}).get("manufacturer", {}).get("name"):
                manufacturers.add(mfr_name)

        self.json["manufacturers"] = list(manufacturers)

    def _load_device_types(self) -> None:
        """Load device type data from JSON files."""
        device_types = set()
        for device in self.json["devices"]:
            manufacturer = device["device_type"]["manufacturer"]["name"]
            model = device["device_type"]["model"]
            device_types.add((manufacturer, model))

        self.json["device_types"] = [
            {"manufacturer": manufacturer, "model": model}
            for manufacturer, model in sorted(device_types)
        ]

    def _load_aggregate_prefixes(self) -> None:
        """Load aggregate prefixes from prefixes.yaml in the context directory."""
        prefixes_file = Path(__file__).parent / self.context_dir / "prefixes.yaml"
        self.json["aggregate_prefixes"] = []

        if prefixes_file.exists():
            try:
                with open(prefixes_file) as f:
                    data = yaml.safe_load(f)
                    self.json["aggregate_prefixes"] = data.get("aggregate_prefixes", [])
            except (yaml.YAMLError, OSError) as e:
                print(f"Warning: Could not load {prefixes_file}: {e}")

    def _load_prefixes(self) -> None:
        """Load prefix data from device interfaces and aggregate prefixes.

        This method:
        1. Starts with aggregate prefixes from prefixes.yaml (with roles/tags)
        2. Extracts /31 p2p prefixes from device interfaces
        3. For each /31, finds the smallest containing aggregate and inherits its role

        Parent relationships are handled automatically by Nautobot.
        """
        # Start with aggregate prefixes (they have roles and tags defined)
        prefix_data = {}
        for agg in self.json.get("aggregate_prefixes", []):
            prefix_data[agg["prefix"]] = {
                "prefix": agg["prefix"],
                "role": agg.get("role"),
                "tags": agg.get("tags", []),
            }

        # Collect /31 p2p prefixes from device interfaces
        # Track which prefixes should get dhcp-subnet tag:
        # - management networks with dhcp-reserve IPs
        # - explicit dhcp-pool / dhcp-reserve interfaces
        dhcp_subnet_prefixes = set()

        for device in self.json["devices"]:
            device_role = device.get("role", {}).get("name", "") if device.get("role") else ""
            is_smn_device = device_role.startswith("SMN")

            for interface in device.get("interfaces", []):
                intf_name = interface.get("name", "")
                intf_role = (
                    interface.get("role", {}).get("name", "") if interface.get("role") else ""
                )
                is_mgmt = (
                    intf_name == "eth0"
                    or intf_name.startswith("Management")
                    or bool(interface.get("mgmt_only"))
                )
                is_explicit_dhcp = bool(interface.get("dhcp_pool")) or bool(
                    interface.get("dhcp_reserve")
                )
                is_uplink = intf_role == "Uplink"

                for ip in interface.get("ip_addresses", []):
                    mask_length = ip.get("mask_length", 32)
                    address = ip.get("address", "")

                    if address and (is_mgmt or is_explicit_dhcp or (is_smn_device and is_uplink)):
                        try:
                            dhcp_subnet_prefixes.add(
                                str(ipaddress.ip_network(address, strict=False))
                            )
                        except ValueError:
                            pass

                    if mask_length == 31 and address:
                        # Normalize to network address (both IPs in a /31 share the same network)
                        try:
                            network = ipaddress.ip_network(address, strict=False)
                            network_prefix = str(network)
                        except ValueError:
                            continue

                        # Skip if already defined in aggregates (preserves YAML-defined tags)
                        if network_prefix in prefix_data:
                            continue

                        # Find containing aggregate to inherit role
                        containing = find_containing_aggregate(
                            network_prefix, self.json.get("aggregate_prefixes", [])
                        )
                        inherited_role = containing.get("role") if containing else None

                        prefix_data[network_prefix] = {
                            "prefix": network_prefix,
                            "role": inherited_role,
                            "tags": [],
                        }

        # Add dhcp-subnet tag to management and SMN uplink prefixes.
        for prefix_str in dhcp_subnet_prefixes:
            if prefix_str not in prefix_data:
                containing = find_containing_aggregate(
                    prefix_str, self.json.get("aggregate_prefixes", [])
                )
                prefix_data[prefix_str] = {
                    "prefix": prefix_str,
                    "role": containing.get("role") if containing else None,
                    "tags": [],
                }
            if "dhcp-subnet" not in prefix_data[prefix_str]["tags"]:
                prefix_data[prefix_str]["tags"].append("dhcp-subnet")

        # Convert to list and sort by prefix length (smaller first for proper creation order)
        prefix_list = list(prefix_data.values())
        prefix_list.sort(key=lambda x: int(x["prefix"].split("/")[1]))

        self.json["prefixes"] = prefix_list

    def _load_prefix_gateway_relationships(self) -> None:
        """Load explicit prefix-to-gateway relationships from DHCP pool links."""
        relationships = {}

        for device in self.json["devices"]:
            for interface in device.get("interfaces", []):
                if not interface.get("dhcp_pool"):
                    continue

                for address_data in interface.get("ip_addresses", []):
                    address = address_data.get("address")
                    if not address:
                        continue

                    try:
                        interface_ip = ipaddress.ip_interface(address)
                        network = ipaddress.ip_network(address, strict=False)
                    except ValueError:
                        continue

                    if network.prefixlen not in (31, 127):
                        continue

                    gateway = next(
                        str(candidate) for candidate in network if candidate != interface_ip.ip
                    )
                    prefix = str(network)
                    existing_gateway = relationships.get(prefix)
                    if existing_gateway and existing_gateway != gateway:
                        print(
                            "Warning: conflicting prefix gateway for "
                            f"{prefix}: {existing_gateway} vs {gateway}"
                        )
                        continue

                    relationships[prefix] = gateway

        self.json["prefix_gateway_relationships"] = [
            {"prefix": prefix, "gateway": gateway}
            for prefix, gateway in sorted(relationships.items())
        ]

    def _assign_ip_address_parent_prefixes(self) -> None:
        """Annotate interface IPs with their most specific containing prefix."""
        prefixes = []
        for prefix in self.json.get("prefixes", []):
            try:
                prefixes.append(
                    (
                        ipaddress.ip_network(prefix["prefix"], strict=False),
                        prefix["prefix"],
                    )
                )
            except ValueError:
                continue

        for device in self.json["devices"]:
            for interface in device.get("interfaces", []):
                for address in interface.get("ip_addresses", []):
                    ip_address = address.get("address")
                    if not ip_address:
                        continue
                    try:
                        host = ipaddress.ip_interface(ip_address).ip
                    except ValueError:
                        continue

                    containing_prefixes = [
                        (prefix.prefixlen, prefix_str)
                        for prefix, prefix_str in prefixes
                        if host in prefix
                    ]
                    if containing_prefixes:
                        containing_prefixes.sort(reverse=True)
                        address["parent_prefix"] = containing_prefixes[0][1]

    def _load_vrfs(self) -> None:
        """Load VRF data referenced by devices and interfaces."""
        vrfs = {}

        def merge_vrf(vrf: dict[str, Any] | None) -> None:
            if not (vrf and vrf.get("name")):
                return

            current = vrfs.setdefault(vrf["name"], {"name": vrf["name"]})
            for key, value in vrf.items():
                if value in (None, ""):
                    continue
                if isinstance(value, list) and not value:
                    current.setdefault(key, value)
                    continue
                current[key] = value

        for device in self.json["devices"]:
            for vrf in device.get("vrfs", []):
                merge_vrf(vrf)

            for interface in device.get("interfaces", []):
                merge_vrf(interface.get("vrf"))

        for payload in self.json.get("overlay_payloads", []):
            for vxlan in payload.get("vxlans", []):
                merge_vrf(vxlan.get("vrf"))

        self.json["vrfs"] = [
            {
                "name": name,
                "rd": vrf.get("rd"),
                "import_targets": vrf.get("import_targets", []),
                "export_targets": vrf.get("export_targets", []),
            }
            for name, vrf in sorted(vrfs.items())
        ]

    def _load_vlans(self) -> None:
        """Load VLAN data referenced by device interfaces."""
        vlans = {}
        for device in self.json["devices"]:
            for interface in device.get("interfaces", []):
                untagged_vlan = interface.get("untagged_vlan")
                if untagged_vlan and untagged_vlan.get("vid"):
                    vlans[untagged_vlan["vid"]] = untagged_vlan

                for tagged_vlan in interface.get("tagged_vlans", []):
                    if tagged_vlan and tagged_vlan.get("vid"):
                        vlans[tagged_vlan["vid"]] = tagged_vlan

        for payload in self.json.get("overlay_payloads", []):
            for vxlan in payload.get("vxlans", []):
                vlan = vxlan.get("vlan")
                if vlan and vlan.get("vid"):
                    vlans[vlan["vid"]] = vlan

        self.json["vlans"] = [
            {"vid": vid, "name": vlan.get("name", f"Vlan{vid}")}
            for vid, vlan in sorted(vlans.items())
        ]

    def _load_overlays(self) -> None:
        """Load overlay, VXLAN, and overlay assignment data from device payloads."""
        overlays = {}
        vxlans = {}
        overlay_assignments = {}
        device_names_by_id = {
            device.get("id"): device.get("name")
            for device in self.json["devices"]
            if device.get("id") and device.get("name")
        }

        for payload in self.json.get("overlay_payloads", []):
            for vxlan in payload.get("vxlans", []):
                overlay = vxlan.get("overlay") or {}
                overlay_name = overlay.get("name")
                if overlay_name:
                    overlays[overlay_name] = {"name": overlay_name}
                if vxlan.get("vnid") is not None:
                    vxlans.setdefault(int(vxlan["vnid"]), vxlan)

            for assignment in payload.get("overlay_assignments", []):
                object_type = assignment.get("assigned_object_type") or {}
                if object_type.get("app_label") != "dcim" or object_type.get("model") != "device":
                    continue

                overlay = assignment.get("overlay") or {}
                overlay_name = overlay.get("name")
                assigned_object_id = assignment.get("assigned_object_id")
                device_name = device_names_by_id.get(assigned_object_id)
                if not overlay_name or not device_name:
                    continue

                overlays[overlay_name] = {"name": overlay_name}
                overlay_assignments[(overlay_name, device_name)] = {
                    "overlay": {"name": overlay_name},
                    "assigned_object_type": {"app_label": "dcim", "model": "device"},
                    "assigned_object_id": assigned_object_id,
                }

        self.json["overlays"] = [overlays[name] for name in sorted(overlays)]
        self.json["vxlans"] = [vxlans[vnid] for vnid in sorted(vxlans)]
        self.json["overlay_assignments"] = [
            overlay_assignments[key] for key in sorted(overlay_assignments)
        ]

    def _load_ib_pkeys(self) -> None:
        """Load InfiniBand PKey overlay + pkey data from ib_pkeys.yaml."""
        ib_pkeys_file = Path(__file__).parent / self.context_dir / "ib_pkeys.yaml"
        self.json["ib_pkey_overlays"] = []

        if not ib_pkeys_file.exists():
            return

        try:
            with open(ib_pkeys_file) as f:
                data = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as e:
            print(f"Warning: Could not load {ib_pkeys_file}: {e}")
            return

        self.json["ib_pkey_overlays"] = data.get("ib_pkey_overlays", [])

    def _load_vrf_device_assignments(self) -> None:
        """Load device-to-VRF assignments required before interfaces reference VRFs."""
        assignments = {}
        for device in self.json["devices"]:
            device_name = device.get("name")
            if not device_name:
                continue

            for vrf in device.get("vrfs", []):
                if vrf and vrf.get("name"):
                    assignments[(device_name, vrf["name"])] = {
                        "device": device_name,
                        "vrf": vrf["name"],
                        "rd": vrf.get("rd"),
                    }

            for interface in device.get("interfaces", []):
                vrf = interface.get("vrf")
                if vrf and vrf.get("name"):
                    assignments[(device_name, vrf["name"])] = {
                        "device": device_name,
                        "vrf": vrf["name"],
                        "rd": vrf.get("rd"),
                    }

        self.json["vrf_device_assignments"] = [
            assignment for _, assignment in sorted(assignments.items())
        ]

    def _extract_content_type_data(self, field_name: str) -> list[dict]:
        """Extract and deduplicate data by content type.

        Args:
            field_name: The field name to extract from devices and interfaces

        Returns:
            List of dictionaries with 'name' and 'content_types' keys
        """
        content_type_mapping = {}

        for device in self.json["devices"]:
            device_data = device.get(field_name)
            if device_data:
                device_items = device_data if isinstance(device_data, list) else [device_data]

                for item in device_items:
                    if item and item.get("name"):
                        item_name = item["name"]
                        if item_name not in content_type_mapping:
                            content_type_mapping[item_name] = set()
                        content_type_mapping[item_name].add("dcim.device")

            for interface in device.get("interfaces", []):
                interface_data = interface.get(field_name)
                if interface_data:
                    interface_items = (
                        interface_data if isinstance(interface_data, list) else [interface_data]
                    )

                    for item in interface_items:
                        if item and item.get("name"):
                            item_name = item["name"]
                            if item_name not in content_type_mapping:
                                content_type_mapping[item_name] = set()
                            content_type_mapping[item_name].add("dcim.interface")

        # Convert to the desired format
        result = []
        for item_name, content_types in content_type_mapping.items():
            result.append({"name": item_name, "content_types": list(content_types)})

        return result

    # Roles managed by bootstrap job - skip creating these in designs
    BOOTSTRAP_MANAGED_ROLES = {
        # Device Roles
        "WAN",
        "TAN-BBR",
        "CIN-Core",
        "CIN-Spine",
        "CIN-Leaf",
        "TAN-Core",
        "TAN-Spine",
        "TAN-Leaf",
        "SMN-Core",
        "SMN-Spine",
        "SMN-Leaf",
        "SMN-Aggleaf",
        "SMN-ZTPLeaf",
        "OOB-Switch",
        "UC-Leaf",
        "UC-BMCLeaf",
        "InfiniBand Spine",
        "InfiniBand Leaf",
        "GPU",
        "UFM",
        "Console Server",
        # Superpod Device Roles
        "OOB-Spine",
        "Mgmt-Leaf",
        "Power-Leaf",
        "In-Band-Leaf",
        "Storage-Leaf",
        "Border-Leaf",
        "Converged-Spine",
        # Interface Roles
        "Uplink",
        "Downlink",
        "Loopback",
        # IPAM Prefix Roles
        "Site-Aggregate",
        "superpod-spine-p2p",
        "superpod-loopback",
        "Backbone-p2p",
        "BBR-Loopback-Vtep",
        "TAN-Core-p2p",
        "TAN-Spine-p2p",
        "TAN-Leaf-p2p",
        "TAN-BBR-p2p",
        "TAN-loopback",
        "TAN-Loopback",
        "TAN-Leaf-VLAN",
        "TAN-Inet",
        "TAN-Firewall",
        "CIN-Core-p2p",
        "CIN-Spine-p2p",
        "CIN-Leaf-p2p",
        "CIN-loopback",
        "SMN-Core-p2p",
        "SMN-Spine-p2p",
        "SMN-Aggleaf-p2p",
        "SMN-loopback",
        "SMN-Leaf-VLAN",
        "SMN-ZTPleaf-Prefix",
        "OOB-loopback",
        "OOB-Aggregate",
        "OOB-VLAN",
        "UC-Leaf-p2p",
        "UC-Leaf-P2P",
        "UC-BMCLeaf-p2p",
        "UC-BMCLeaf-P2P",
        "UC-BMCLeaf-VLAN",
        "UC-Loopback",
        "UC-Server-Loopback",
        "UC-Site-Prefix",
        "UC-VIPs",
        "DPU-loopback",
        "DPU-Loopback",
        "DPU-Admin",
        "DPU-admin",
        "Forge-Breakfix",
        "Forge-Control-Plane",
        "Forge-Control-Plane-Vips",
        "Forge-Tenants",
        "Scp-Anycast-Vips",
        "Scp-Dhcp-Allocations",
        "Scp-Server-Loopbacks",
        "Scp-Site-Prefix",
        "Scp-Temp-Hosts",
        "Scp-Uc-Cgnat",
        "UserStorage-Server-Loopbacks",
        "UserStorage-Server-P2P",
        "UserStorage-Server-VIPs",
        "UserStorage-Site-Prefix",
        "AZ-Delegated",
        "AZ-IPMI",
        "NMX-M-Prefix",
    }

    def _load_roles(self) -> None:
        """Load role data from devices/interfaces and aggregate prefixes.

        Excludes roles that are managed by the bootstrap job to avoid conflicts.
        """
        role_content_types = {
            role["name"]: set(role["content_types"])
            for role in self._extract_content_type_data("role")
        }

        # Add prefix role membership from aggregate prefixes. This can extend an
        # existing role such as Loopback from dcim.interface to ipam.prefix.
        for agg in self.json.get("aggregate_prefixes", []):
            role_name = agg.get("role")
            if role_name:
                role_content_types.setdefault(role_name, set()).add("ipam.prefix")

        self.json["roles"] = [
            {"name": name, "content_types": sorted(content_types)}
            for name, content_types in sorted(role_content_types.items())
            if name not in self.BOOTSTRAP_MANAGED_ROLES
        ]
        self.json["role_content_type_extensions"] = [
            {"name": name, "content_types": sorted(content_types)}
            for name, content_types in sorted(role_content_types.items())
            if name in self.BOOTSTRAP_MANAGED_ROLES
        ]

    # Tags managed by bootstrap job - skip creating these in designs
    BOOTSTRAP_MANAGED_TAGS = {
        "role-aggregate",
        "ignore-bgp-aggregate",
        "uc-jumphost",
        "dhcp-subnet",
        "dhcp-reserve",
        "dhcp-pool",
        "DpuLoopback",
        "ForgeAdmin",
        "ForgeControlPlane",
        "ForgeOverlay",
        "ForgeServiceVip",
        "nv-config-manager-managed",
        "dns-exempt",
        "spectrumx",
        "cable-validation-ignore",
        "cable-validation-link-state-only",
        "Uplink",
        "Downlink",
        "route-server",
        "dataloader-test",
        "dc-ops-install",
    }

    def _load_tags(self) -> None:
        """Load tag data from devices/interfaces and aggregate prefixes.

        Excludes tags that are managed by the bootstrap job to avoid conflicts.
        """
        # Get tags from devices and interfaces
        tags = self._extract_content_type_data("tags")
        # Filter out bootstrap-managed tags
        tags = [t for t in tags if t["name"] not in self.BOOTSTRAP_MANAGED_TAGS]
        tag_names = {t["name"] for t in tags}

        # Add tags from aggregate prefixes (excluding bootstrap-managed ones)
        for agg in self.json.get("aggregate_prefixes", []):
            for tag_name in agg.get("tags", []):
                if (
                    tag_name
                    and tag_name not in tag_names
                    and tag_name not in self.BOOTSTRAP_MANAGED_TAGS
                ):
                    tags.append({"name": tag_name, "content_types": ["ipam.prefix"]})
                    tag_names.add(tag_name)

        self.json["tags"] = tags

    def _load_config_contexts(self) -> None:
        """Load config contexts from locations.yaml in the context directory."""
        locations_file = Path(__file__).parent / self.context_dir / "locations.yaml"
        self.json["config_contexts"] = []
        self.json["spx_namespaces"] = []

        if locations_file.exists():
            try:
                with open(locations_file) as f:
                    data = yaml.safe_load(f) or {}
                    self.json["config_contexts"] = [
                        self._render_config_context_metadata(config_context)
                        for config_context in data.get("config_contexts", [])
                    ]
                    self.json["spx_namespaces"] = data.get("spx_namespaces", [])
            except (OSError, yaml.YAMLError) as e:
                print(f"Warning: Could not load config contexts from {locations_file}: {e}")

    def _render_config_context_metadata(self, config_context: dict[str, Any]) -> dict[str, Any]:
        """Render deployment-scoped config context lookups without touching data."""
        rendered = dict(config_context)

        for key in ("name", "description"):
            if isinstance(rendered.get(key), str):
                rendered[key] = self._render_deployment_name(rendered[key])

        for key in ("locations", "roles", "tenants", "tags"):
            if isinstance(rendered.get(key), list):
                rendered[key] = [
                    self._render_deployment_name(value) if isinstance(value, str) else value
                    for value in rendered[key]
                ]

        return rendered

    def _render_deployment_name(self, value: str) -> str:
        """Render the deployment name placeholder used by static context YAML."""
        deployment_name = getattr(self, "deployment_name", "")
        return value.replace("{{ deployment_name }}", deployment_name).replace(
            "{{deployment_name}}", deployment_name
        )

    def get_device_ref(self, manufacturer: str, model: str) -> str:
        """Get the device reference for a given manufacturer and model."""
        return slugify(f"{manufacturer}_{model}")

    def color(self, color_name: str = "blue") -> str:
        """Get the color for a given color name. Defaults to blue."""
        return COLOR_NAME_MAP.get(color_name, COLOR_NAME_MAP["blue"])


@context_file(*Path(__file__).parent.glob("dgx_cloud/*.yaml"))
class DgxCloudContext(BaseContext):
    """Jinja2 context for DGX Cloud mock topology design job."""

    context_dir = "dgx_cloud"
    prune_dangling_connected_interfaces = True


@context_file(*Path(__file__).parent.glob("superpod/*.yaml"))
class SuperpodContext(BaseContext):
    """Jinja2 context for Superpod mock topology design job."""

    context_dir = "superpod"
    device_file_glob = "[ab]0*.json"


def get_mock_topology_context_class(blueprint: str) -> type[BaseContext]:
    """Return the context class for a mock topology blueprint."""
    if blueprint == "dgx_cloud":
        return DgxCloudContext
    if blueprint == "superpod":
        return SuperpodContext

    blueprint_path = Path(blueprint)
    if not blueprint or blueprint in {".", ".."} or blueprint_path.name != blueprint:
        raise ValueError("Mock topology blueprint must match a context subdirectory name.")

    context_dir = Path(__file__).parent / blueprint
    if not context_dir.is_dir():
        raise ValueError(f"Mock topology context not found: {context_dir}")

    @context_file(*context_dir.glob("*.yaml"))
    class CustomTopologyContext(BaseContext):
        """Jinja2 context for a custom mock topology design job."""

        context_dir = blueprint

    safe_name = "".join(part.capitalize() for part in slugify(blueprint).split("-")) or "Custom"
    CustomTopologyContext.__name__ = f"{safe_name}Context"
    return CustomTopologyContext
