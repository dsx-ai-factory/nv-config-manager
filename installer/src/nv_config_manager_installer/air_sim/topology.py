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
"""DSX Air topology builder for nvcm-air-simulation."""

from __future__ import annotations

import atexit
import ipaddress
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from nv_config_manager_installer.air_sim.constants import (
    DEFAULT_CUMULUS_VERSION,
    DEFAULT_NODE_CPU,
    DEFAULT_NODE_MEMORY,
    DEFAULT_SERVER_OS,
    _BlockStyleDumper,
)
from nv_config_manager_installer.air_sim.models import CableConnection, DeviceInfo, NVCMServerConfig

LOG = logging.getLogger(__name__)

AIR_NODE_PASSTHROUGH_FIELDS = ("cpu_mode", "cpu_options")


def _create_version_override_yaml(yaml_path: str, target_version: str) -> str:
    """Create a modified copy of the topology YAML with intended-firmware overridden.

    Walks devices[*].local_config_context_data.intended-firmware.version and
    replaces the value with *target_version* for every Cumulus device.  The
    modified YAML is written to a temp file whose path is returned.  An atexit
    handler is registered to clean it up.
    """
    with open(yaml_path) as f:
        site_design = yaml.safe_load(f)

    updated = 0
    for device in site_design.get("devices", []):
        platform = device.get("platform", "")
        if "Cumulus" not in platform:
            continue
        ctx = device.get("local_config_context_data")
        if ctx is None:
            ctx = {}
            device["local_config_context_data"] = ctx
        fw = ctx.get("intended-firmware")
        if fw is None:
            fw = {}
            ctx["intended-firmware"] = fw
        old = fw.get("version")
        fw["version"] = target_version
        LOG.debug(
            "Version override %s: %s -> %s",
            device.get("name", "?"),
            old,
            target_version,
        )
        updated += 1

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        prefix="cumulus_override_",
        delete=False,
    )
    yaml.dump(site_design, tmp, Dumper=_BlockStyleDumper, default_flow_style=False)
    tmp.close()
    atexit.register(os.unlink, tmp.name)

    LOG.info(
        "Created version-override YAML: %s (%d devices updated to %s)",
        tmp.name,
        updated,
        target_version,
    )
    return tmp.name


def _resolve_oob_server_ips_from_topology(
    site_design: dict[str, Any],
    oob_server_name: str,
) -> tuple[str, str | None]:
    """Resolve oob-mgmt-server eth1 IP and OOB gateway from topology.

    Scans site_design["ip_addresses"] for device == oob_server_name and
    interface == "eth1".  Returns (address, gateway) where gateway is the
    other address in the same subnet (e.g. for 7.240.192.0/31, gateway is
    7.240.192.1).  For non-/31 masks, gateway is None.

    Raises SystemExit if no matching ip_addresses entry is found -- the YAML
    is the single source of truth and must define this.
    """
    ip_addresses = site_design.get("ip_addresses", [])
    for ip in ip_addresses:
        if ip.get("device") != oob_server_name or ip.get("interface") != "eth1":
            continue
        addr = ip.get("address")
        if not addr:
            continue
        try:
            iface = ipaddress.IPv4Interface(addr)
        except ValueError:
            continue
        if iface.network.prefixlen == 31:
            other = next(
                (h for h in iface.network.hosts() if h != iface.ip),
                None,
            )
            gateway = str(other) if other is not None else None
        else:
            gateway = None
        return (addr, gateway)
    LOG.error(
        "No ip_addresses entry for %s eth1 in the topology YAML. "
        "The YAML must define the oob-mgmt-server eth1 address.",
        oob_server_name,
    )
    sys.exit(1)


class AirTopologyBuilder:
    """Build NVIDIA DSX Air topology from site export YAML."""

    def __init__(
        self,
        yaml_path: str,
        simulation_name: str | None = None,
        minimal_mode: bool = False,
        nvcm_server: NVCMServerConfig | None = None,
        cumulus_image_overrides: dict[str, str] | None = None,
    ) -> None:
        """Initialize the topology builder.

        Args:
            yaml_path: Path to the site export YAML file
            simulation_name: Name for the DSX Air simulation (auto-generated if not provided)
            minimal_mode: If True, group similar devices into single nodes for smaller sims
            nvcm_server: Configuration for adding a NVCM server node to the topology
            cumulus_image_overrides: DSX Air image names keyed by Cumulus firmware version
        """
        self.yaml_path = Path(yaml_path)
        self.minimal_mode = minimal_mode
        self.nvcm_server = nvcm_server
        self.cumulus_image_overrides = cumulus_image_overrides or {}

        # Parse YAML
        with open(self.yaml_path) as f:
            self.site_design = yaml.safe_load(f)

        # Extract site name from hierarchy
        self.site_name = self._extract_site_name()
        self.simulation_name = simulation_name or f"NVCM-E2E-{self.site_name}"

        # Device and connection storage
        self.devices: dict[str, DeviceInfo] = {}
        self.connections: list[CableConnection] = []

        # Resolve tagged prefixes from the YAML
        self.lb_allowed_prefixes = self._resolve_tagged_prefixes("lb-allowed") or ["0.0.0.0/0"]
        self.relay_return_prefixes = self._resolve_tagged_prefixes("relay-return")

        # Parse the topology
        self._parse_devices()
        self._parse_interfaces()
        self._parse_cables()

    def cumulus_firmware_versions(self) -> list[str]:
        """Return unique Cumulus firmware versions required by this topology."""
        return sorted(
            {
                device.firmware_version
                for device in self.devices.values()
                if ("Cumulus" in device.platform and device.firmware_version and device.air_enabled)
            }
        )

    def set_cumulus_image_overrides(self, image_overrides: dict[str, str]) -> None:
        """Set resolved DSX Air image names keyed by Cumulus firmware version."""
        self.cumulus_image_overrides = dict(image_overrides)

    def _cumulus_air_image(self, firmware_version: str) -> str:
        """Return the DSX Air OS image name for a Cumulus firmware version."""
        return self.cumulus_image_overrides.get(firmware_version, f"cumulus-vx-{firmware_version}")

    def _extract_site_name(self) -> str:
        """Extract the site name from location hierarchy."""
        hierarchy = self.site_design.get("location_hierarchy", [])
        for loc in hierarchy:
            if loc.get("type") == "Site":
                return loc.get("name", "Unknown")
        return "Unknown"

    def resolve_device_bgp_asn(self, device_name: str) -> str | None:
        """Return the BGP ASN for *device_name* from topology metadata."""
        for dev in self.site_design.get("devices", []):
            if dev.get("name") == device_name:
                asn = dev.get("bgp_asn")
                if asn is not None:
                    return str(asn)
                asn = (dev.get("bgp") or {}).get("asn")
                if asn is not None:
                    return str(asn)
                ctx = dev.get("local_config_context_data", {})
                asn = (ctx.get("bgp") or {}).get("asn")
                if asn is not None:
                    return str(asn)
        return None

    def _resolve_tagged_prefixes(self, tag: str) -> list[str]:
        """Return prefix CIDRs that carry the given tag."""
        prefixes_data = self.site_design.get("prefixes", [])
        result = []
        for entry in prefixes_data:
            tags = entry.get("tags", [])
            if tag in tags:
                pfx = entry.get("prefix")
                if pfx:
                    result.append(pfx)
        if result:
            LOG.info("Prefixes tagged '%s': %s", tag, ", ".join(result))
        return result

    @staticmethod
    def _index_to_mac(index: int) -> str:
        """Convert a 1-based index to a deterministic MAC in 44:38:39:xx:xx:xx."""
        b3 = (index >> 16) & 0xFF
        b4 = (index >> 8) & 0xFF
        b5 = index & 0xFF
        return f"44:38:39:{b3:02x}:{b4:02x}:{b5:02x}"

    def _parse_devices(self) -> None:
        """Parse devices from the site design."""
        devices_data = self.site_design.get("devices", [])
        cumulus_count = 0
        server_count = 0

        auto_serial_names: list[str] = []

        for device in devices_data:
            name = device.get("name")
            platform = device.get("platform", "")
            role = device.get("role", "Unknown")

            device_type = device.get("device_type", {})
            model = device_type.get("model", "Unknown")
            air_config = device.get("_air") or {}
            air_enabled = air_config.get("enabled", True) is not False
            raw_serial = device.get("serial", "")
            serial = "" if raw_serial == "auto" else raw_serial
            needs_auto = raw_serial == "auto"

            if "Cumulus" in platform:
                config_context = device.get("local_config_context_data", {})
                intended_firmware = config_context.get("intended-firmware", {})
                version = intended_firmware.get("version", DEFAULT_CUMULUS_VERSION)

                nvcm_status = device.get("nvcm", {})
                nvcm_enabled = nvcm_status.get("render_enabled", False)

                self.devices[name] = DeviceInfo(
                    name=name,
                    platform=platform,
                    role=role,
                    model=model,
                    firmware_version=version,
                    serial=serial,
                    nvcm_enabled=nvcm_enabled,
                    air_config=air_config,
                    air_enabled=air_enabled,
                )
                cumulus_count += 1
            else:
                self.devices[name] = DeviceInfo(
                    name=name,
                    platform=platform,
                    role=role,
                    model=model,
                    firmware_version=air_config.get("os", DEFAULT_SERVER_OS),
                    serial=serial,
                    nvcm_enabled=False,
                    air_config=air_config,
                    air_enabled=air_enabled,
                )
                server_count += 1

            if needs_auto:
                auto_serial_names.append(name)

        if auto_serial_names:
            auto_serial_names.sort()
            for idx, dev_name in enumerate(auto_serial_names, start=1):
                mac = self._index_to_mac(idx)
                self.devices[dev_name].serial = mac
                LOG.debug("Auto serial %s -> %s", dev_name, mac)
            LOG.info("Auto-generated serials for %d device(s)", len(auto_serial_names))

        LOG.info(f"Found {cumulus_count} Cumulus Linux devices")
        if server_count > 0:
            LOG.info(f"Found {server_count} server nodes")

    def _parse_interfaces(self) -> None:
        """Parse interfaces and attach them to devices."""
        interfaces_data = self.site_design.get("interfaces", [])

        self.exit_interfaces: list[tuple[str, str]] = []
        auto_mac_entries: list[tuple[str, str]] = []

        for intf in interfaces_data:
            device_name = intf.get("device")
            if device_name not in self.devices or not self.devices[device_name].air_enabled:
                continue
            intf_name = intf.get("name")
            intf_type = intf.get("type", "")
            description = intf.get("description") or ""
            raw_mac = intf.get("mac_address")
            if raw_mac is not None and not isinstance(raw_mac, str):
                raise ValueError(
                    f"{device_name} {intf_name} mac_address must be a string; "
                    "quote MAC addresses in YAML"
                )
            mac_address = None if raw_mac == "auto" else raw_mac
            needs_auto = raw_mac == "auto"

            if description.lower() == "exit":
                self.exit_interfaces.append((device_name, intf_name))
                self.devices[device_name].interfaces.append(intf_name)
                if mac_address:
                    self.devices[device_name].interface_macs[intf_name] = mac_address
                elif needs_auto:
                    auto_mac_entries.append((device_name, intf_name))
                continue

            device = self.devices[device_name]
            is_cumulus = "Cumulus" in device.platform

            if not is_cumulus:
                if intf_name.startswith("lo"):
                    continue
                device.interfaces.append(intf_name)
                if mac_address:
                    device.interface_macs[intf_name] = mac_address
                elif needs_auto:
                    auto_mac_entries.append((device_name, intf_name))
            else:
                if intf_type == "virtual":
                    continue
                if re.match(r"^(swp\d+|eth\d+|Ethernet\d+(/\d+)?)$", intf_name):
                    if device.air_enabled and intf_name == "eth0" and not mac_address:
                        raise ValueError(
                            f"Cumulus device {device_name} interface eth0 must define "
                            "an explicit mac_address for DHCP/ZTP reservations"
                        )
                    device.interfaces.append(intf_name)
                    if mac_address:
                        device.interface_macs[intf_name] = mac_address
                    elif needs_auto:
                        auto_mac_entries.append((device_name, intf_name))

        if auto_mac_entries:
            auto_mac_entries.sort()
            offset = 0x010000
            for idx, (dev_name, iface) in enumerate(auto_mac_entries, start=1):
                mac = self._index_to_mac(offset + idx)
                self.devices[dev_name].interface_macs[iface] = mac
                LOG.debug("Auto MAC %s:%s -> %s", dev_name, iface, mac)
            LOG.info("Auto-generated MACs for %d interface(s)", len(auto_mac_entries))

        if self.exit_interfaces:
            LOG.info(f"Found {len(self.exit_interfaces)} exit interface(s) for SSH access")

        # Cumulus VX derives both serial-number and system-mac from eth0.  The
        # topology YAML must carry the same value that Nautobot will use for
        # DHCP reservations; silently inventing it here hides broken input.
        for device in self.devices.values():
            if "Cumulus" not in device.platform:
                continue
            if not device.air_enabled:
                continue
            if "eth0" not in device.interfaces or not device.interface_macs.get("eth0"):
                raise ValueError(
                    f"Cumulus device {device.name} must define eth0 with an explicit "
                    "mac_address for DHCP/ZTP reservations"
                )

        # Log interface counts
        for device in self.devices.values():
            LOG.debug(f"{device.name}: {len(device.interfaces)} interfaces")

    def _parse_cables(self) -> None:
        """Parse cable connections from the site design."""
        cabling = self.site_design.get("cabling_assignments", {})
        connections_data = cabling.get("connections", [])

        seen_links: set[frozenset[tuple[str, str]]] = set()

        for conn in connections_data:
            source = conn.get("source", {})
            dest = conn.get("destination", {})

            source_device = source.get("device")
            source_intf = source.get("component", {}).get("name")
            dest_device = dest.get("device")
            dest_intf = dest.get("component", {}).get("name")

            # Only include connections where both endpoints are in our devices
            if source_device not in self.devices or dest_device not in self.devices:
                continue

            # Deduplicate: export may list the same link twice (A->B and B->A)
            link_key = frozenset({(source_device, source_intf), (dest_device, dest_intf)})
            if link_key in seen_links:
                continue
            seen_links.add(link_key)

            self.connections.append(
                CableConnection(
                    source_device=source_device,
                    source_interface=source_intf,
                    dest_device=dest_device,
                    dest_interface=dest_intf,
                )
            )

        LOG.info(f"Found {len(self.connections)} cable connections between devices")

    def build_topology(self) -> dict[str, Any]:
        """Build the DSX Air topology JSON.

        Returns:
            Dictionary in DSX Air JSON topology format
        """
        if self.minimal_mode:
            return self._build_minimal_topology()
        return self._build_full_topology()

    def _make_link_endpoint(self, device_name: str, intf_name: str) -> dict[str, str]:
        """Build a DSX Air link endpoint dict, including MAC when set."""
        endpoint: dict[str, str] = {"node": device_name, "interface": intf_name}
        device = self.devices.get(device_name)
        if device:
            mac = device.interface_macs.get(intf_name)
            if mac:
                endpoint["mac"] = mac.lower()
        return endpoint

    def _build_full_topology(self) -> dict[str, Any]:
        """Build full topology with one DSX Air node per device."""
        topology: dict[str, Any] = {
            "oob": False,  # We'll add eth0 as outbound interfaces manually
            "nodes": {},
            "links": [],
        }

        # Create a node for each device
        for device in self.devices.values():
            if not device.air_enabled:
                continue
            is_cumulus = "Cumulus" in device.platform

            if is_cumulus:
                node: dict[str, Any] = {
                    "memory": DEFAULT_NODE_MEMORY,
                    "cpu": DEFAULT_NODE_CPU,
                    "os": self._cumulus_air_image(device.firmware_version),
                }
            else:
                # Non-Cumulus node (servers, GPUs, DPUs) - use DSX Air config if available
                air_config = device.air_config
                node = {
                    "memory": air_config.get("memory", DEFAULT_NODE_MEMORY),
                    "cpu": air_config.get("cpu", DEFAULT_NODE_CPU),
                    "os": air_config.get("os", DEFAULT_SERVER_OS),
                }
                if air_config.get("storage"):
                    node["storage"] = air_config["storage"]
                for field in AIR_NODE_PASSTHROUGH_FIELDS:
                    if field in air_config:
                        node[field] = air_config[field]

            topology["nodes"][device.name] = node

            # Add all interfaces as unconnected first
            for intf in device.interfaces:
                topology["links"].append(
                    [self._make_link_endpoint(device.name, intf), "unconnected"]
                )

        # Override with actual connections
        connected_intfs: set[tuple[str, str]] = set()

        for conn in self.connections:
            if (
                conn.source_device not in topology["nodes"]
                or conn.dest_device not in topology["nodes"]
            ):
                continue
            topology["links"].append(
                [
                    self._make_link_endpoint(conn.source_device, conn.source_interface),
                    self._make_link_endpoint(conn.dest_device, conn.dest_interface),
                ]
            )
            connected_intfs.add((conn.source_device, conn.source_interface))
            connected_intfs.add((conn.dest_device, conn.dest_interface))

        # Remove unconnected links for interfaces that are actually connected
        # or that should be exit interfaces
        exit_intf_set = set(self.exit_interfaces) if hasattr(self, "exit_interfaces") else set()
        topology["links"] = [
            link
            for link in topology["links"]
            if not (
                isinstance(link[1], str)
                and link[1] == "unconnected"
                and (
                    (link[0]["node"], link[0]["interface"]) in connected_intfs
                    or (link[0]["node"], link[0]["interface"]) in exit_intf_set
                )
            )
        ]

        # Add "exit" links for SSH access (public-facing interfaces)
        if hasattr(self, "exit_interfaces"):
            for device_name, intf_name in self.exit_interfaces:
                if device_name not in topology["nodes"]:
                    continue
                topology["links"].append([self._make_link_endpoint(device_name, intf_name), "exit"])

        # Add NVCM server node if configured
        if self.nvcm_server:
            self._add_nvcm_server_to_topology(topology)

        LOG.info(
            f"Built topology with {len(topology['nodes'])} nodes and {len(topology['links'])} links"
        )
        return topology

    def _add_nvcm_server_to_topology(self, topology: dict[str, Any]) -> None:
        """Add or configure the NVCM server in the topology.

        If using an existing server (use_existing_server), just logs the config.
        If creating a new node (attach_switch/attach_interface), adds the node
        and creates a link to the specified switch.

        Args:
            topology: The topology dict to modify in place
        """
        if not self.nvcm_server:
            return

        server_name = self.nvcm_server.server_name

        # Option 1: Use existing server from simulation
        if self.nvcm_server.use_existing_server:
            if server_name not in topology["nodes"]:
                raise ValueError(
                    f"Server '{server_name}' not found in topology. "
                    f"Available nodes: {list(topology['nodes'].keys())[:10]}..."
                )
            topology["nodes"][server_name].update(
                {
                    "os": self.nvcm_server.os,
                    "cpu": self.nvcm_server.cpu,
                    "memory": self.nvcm_server.memory,
                    "storage": self.nvcm_server.storage,
                }
            )
            LOG.info(
                f"Overriding existing server '{server_name}' with nvcm-box image "
                f"({self.nvcm_server.os}, {self.nvcm_server.cpu} CPU, "
                f"{self.nvcm_server.memory}MB RAM, {self.nvcm_server.storage}GB storage)"
            )
            return

        # Option 2: Create new node attached to a switch
        if not self.nvcm_server.attach_switch or not self.nvcm_server.attach_interface:
            raise ValueError(
                "Must specify either --use-existing-server OR both "
                "--attach-switch and --attach-interface"
            )

        # Validate the attach switch exists
        if self.nvcm_server.attach_switch not in topology["nodes"]:
            LOG.warning(
                f"Switch '{self.nvcm_server.attach_switch}' not found in topology. "
                f"Available switches: {list(self.devices.keys())[:10]}..."
            )
            raise ValueError(f"Switch '{self.nvcm_server.attach_switch}' not found in topology")

        # Add the NVCM server node (nvcm-box with extra resources)
        topology["nodes"][server_name] = {
            "memory": self.nvcm_server.memory,
            "cpu": self.nvcm_server.cpu,
            "storage": self.nvcm_server.storage,
            "os": self.nvcm_server.os,
        }

        # If the server is already connected from the topology, do not add a duplicate link.
        server_interface = self.nvcm_server.server_interface
        for link in topology["links"]:
            if not isinstance(link, list) or len(link) < 2:
                continue
            for ep in (link[0], link[1]):
                if not (isinstance(ep, dict) and ep.get("node") == server_name):
                    continue
                if ep.get("interface") == server_interface:
                    LOG.info(
                        f"NVCM server '{server_name}' already has link on {server_interface} "
                        "from topology; skipping duplicate link"
                    )
                    return

        # Add the link between nvcm-server and the specified switch/interface
        # First, remove any existing unconnected link for that interface
        attach_switch = self.nvcm_server.attach_switch
        attach_interface = self.nvcm_server.attach_interface
        server_interface = self.nvcm_server.server_interface

        topology["links"] = [
            link
            for link in topology["links"]
            if not (
                isinstance(link[1], str)
                and link[1] == "unconnected"
                and link[0]["node"] == attach_switch
                and link[0]["interface"] == attach_interface
            )
        ]

        # Add the connection between nvcm-server and the switch
        topology["links"].append(
            [
                {"node": server_name, "interface": server_interface},
                {"node": attach_switch, "interface": attach_interface},
            ]
        )

        LOG.info(
            f"Added NVCM server: {server_name} "
            f"({self.nvcm_server.cpu} CPU, {self.nvcm_server.memory}MB RAM) "
            f"connected to {attach_switch}:{attach_interface}"
        )

    def _build_minimal_topology(self) -> dict[str, Any]:
        """Build minimal topology by grouping similar devices.

        Groups devices by (model, role, firmware_version) to reduce simulation size.
        Useful for testing configuration rendering without full topology.
        """
        if self.nvcm_server and self.nvcm_server.attach_switch:
            attach_switch = self.nvcm_server.attach_switch
            attach_device = self.devices.get(attach_switch)
            if attach_device is not None and not attach_device.air_enabled:
                raise ValueError(f"Switch '{attach_switch}' is disabled for AIR")

        topology: dict[str, Any] = {
            "oob": False,
            "nodes": {},
            "links": [],
        }

        # Group devices by model-role-version
        device_groups: dict[str, dict[str, Any]] = {}

        for device in self.devices.values():
            if not device.air_enabled:
                continue
            # Create group key
            key = f"{device.model}-{device.role}-{device.firmware_version}".replace(
                ".", "-"
            ).replace(" ", "-")

            if key not in device_groups:
                device_groups[key] = {
                    "firmware_version": device.firmware_version,
                    "interfaces": set(),
                    "devices": [],
                }

            # Merge interfaces
            device_groups[key]["interfaces"].update(device.interfaces)
            device_groups[key]["devices"].append(device.name)

        # Create nodes from groups
        for group_key, group_data in device_groups.items():
            topology["nodes"][group_key] = {
                "memory": DEFAULT_NODE_MEMORY,
                "cpu": DEFAULT_NODE_CPU,
                "os": self._cumulus_air_image(group_data["firmware_version"]),
            }

            # Add interfaces as unconnected
            for intf in group_data["interfaces"]:
                topology["links"].append([{"node": group_key, "interface": intf}, "unconnected"])

        # Add NVCM server if configured (attach to first group that matches attach_switch)
        if self.nvcm_server:
            # Find which group contains the attach_switch
            attach_group = None
            for group_key, group_data in device_groups.items():
                if self.nvcm_server.attach_switch in group_data["devices"]:
                    attach_group = group_key
                    break

            if attach_group:
                # Temporarily modify nvcm_server to use group key
                original_switch = self.nvcm_server.attach_switch
                self.nvcm_server.attach_switch = attach_group
                # Add nvcm server (need to add to devices temporarily)
                self.devices[attach_group] = DeviceInfo(
                    name=attach_group,
                    platform="Cumulus Linux",
                    role="grouped",
                    model="virtual",
                    firmware_version=device_groups[attach_group]["firmware_version"],
                )
                self._add_nvcm_server_to_topology(topology)
                self.nvcm_server.attach_switch = original_switch
            else:
                LOG.warning(
                    f"Could not find switch '{self.nvcm_server.attach_switch}' "
                    f"in minimal topology groups"
                )

        LOG.info(
            f"Built minimal topology: {len(topology['nodes'])} nodes from "
            f"{len(self.devices)} devices"
        )
        return topology
