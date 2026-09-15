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
"""Diagnostics activities — command catalog, models, and activity functions."""

import asyncio
import time
from contextlib import closing
from datetime import timedelta

from pydantic import BaseModel
from temporalio import activity

from nv_config_manager.common.config import load_config
from nv_config_manager.temporal.client.device import NetworkConnection, NetworkDeviceData
from nv_config_manager.temporal.client.redis import RedisClient
from nv_config_manager.temporal.common.mixins.device import Platform

# Master list of all diagnostic commands and their human-readable descriptions.
# Adding a new command only requires an entry here — no per-platform duplication.
COMMAND_DESCRIPTIONS: dict[str, str] = {
    # Shared across platforms
    "show_version": "System version, hostname, and uptime",
    "show_interfaces": "All interface states and statistics",
    "show_bgp_summary": "BGP neighbor summary across all VRFs",
    "show_lldp_neighbors": "LLDP neighbor table per interface",
    "show_platform": "Hardware model, serial, and component firmware",
    "show_route_table": "IP routing table across all VRFs",
    # Shared: Arista + Cumulus
    "show_vlan": "VLAN table with member ports",
    "show_mac_table": "MAC address table by interface",
    "show_mlag": "MLAG / CLAG bond state and peer info",
    "show_platform_environment": "Fan, PSU, and temperature sensor readings",
    "show_platform_transceiver": "Optic transceiver DOM values per port",
    "show_system_health": "System health checks and component status",
    # Arista-only
    "show_vrf": "VRF list and routing instance detail",
    "show_arp_table": "ARP / neighbor resolution table",
    "show_spanning_tree": "Spanning tree bridge and port states",
    "show_port_channels": "Port-channel / LAG member ports and state",
    "show_isis_neighbors": "IS-IS adjacency table",
    "show_isis_interfaces": "IS-IS enabled interfaces and metric",
    "show_isis_database": "IS-IS LSP database",
    "show_mpls_interfaces": "MPLS enabled interfaces",
    "show_mpls_rsvp_neighbors": "MPLS RSVP neighbor summary",
    "show_mac_security": "MACsec session state per interface",
    "show_mac_security_counters": "MACsec encrypted/unencrypted byte counters",
    "show_vrrp": "VRRP group states and priorities",
    "show_inventory": "Full hardware inventory (chassis, modules, SFPs)",
    # Cumulus-only
    "show_interface_counters": "Interface traffic counters and error statistics",
    "show_interface_mac": "MAC addresses learned per interface",
}

# Per-platform support sets — each platform declares which commands it supports.
# NV-OS exposes the same NVUE API as Cumulus but does not do IP routing,
# so BGP summary and route table are excluded.
PLATFORM_COMMANDS: dict[Platform, set[str]] = {
    Platform.CUMULUS_LINUX: {
        # Existing
        "show_version",
        "show_interfaces",
        "show_bgp_summary",
        "show_lldp_neighbors",
        "show_platform",
        "show_route_table",
        # New — NVUE REST API
        "show_system_health",
        "show_interface_counters",
        "show_interface_mac",
        "show_mac_table",
        "show_mlag",
        "show_platform_environment",
        "show_vlan",
        "show_platform_transceiver",
    },
    Platform.NV_OS: {
        "show_version",
        "show_interfaces",
        "show_lldp_neighbors",
        "show_platform",
        # New — NVUE REST API (same endpoints as Cumulus, port 443)
        "show_system_health",
        "show_interface_counters",
        "show_interface_mac",
        "show_mac_table",
        "show_mlag",
        "show_platform_environment",
        "show_vlan",
        "show_platform_transceiver",
    },
    Platform.ARISTA_EOS: {
        # All via eAPI JSON-RPC (HTTPS port 443)
        "show_version",
        "show_interfaces",
        "show_vlan",
        "show_lldp_neighbors",
        "show_vrf",
        "show_port_channels",
        "show_spanning_tree",
        "show_route_table",
        "show_bgp_summary",
        "show_isis_neighbors",
        "show_isis_interfaces",
        "show_isis_database",
        "show_mpls_interfaces",
        "show_mpls_rsvp_neighbors",
        "show_mac_security",
        "show_mac_security_counters",
        "show_vrrp",
        "show_arp_table",
        "show_mac_table",
        "show_inventory",
        "show_mlag",
    },
    Platform.JUNIPER_JUNOS: {
        "show_version",
        "show_interfaces",
        "show_lldp_neighbors",
        "show_route_table",
        "show_arp_table",
    },
    Platform.MLNX_OS: set(),
    Platform.UFM: set(),
}


def get_available_commands(platform: Platform) -> dict[str, str]:
    """Return the {name: description} map for a given platform."""
    supported = PLATFORM_COMMANDS.get(platform, set())
    return {name: COMMAND_DESCRIPTIONS[name] for name in supported if name in COMMAND_DESCRIPTIONS}


def validate_commands(platform: Platform, names: list[str]) -> list[str]:
    """Return only the catalog names that are valid for the given platform.

    Normalises each input name by lower-casing and replacing spaces/hyphens
    with underscores so that ``"show version"`` resolves to ``"show_version"``.
    Names with no match in the catalog are silently dropped.
    """
    available = get_available_commands(platform)
    result = []
    for name in names:
        normalised = name.strip().lower().replace(" ", "_").replace("-", "_")
        if normalised in available:
            result.append(normalised)
    return result


# =============================================================================
# Input / Output Models
# =============================================================================


class RunDiagnosticsInput(BaseModel):
    device_data: NetworkDeviceData
    commands: list[str]  # catalog names e.g. ["show_version", "show_bgp_summary"]


class RunDiagnosticsOutput(BaseModel):
    device_name: str
    outputs: dict[str, str]  # command_name → raw text output (or "ERROR: ..." on failure)


class TechSupportInput(BaseModel):
    device_data: NetworkDeviceData


class TechSupportOutput(BaseModel):
    device_name: str
    redis_key: str = ""  # Redis key where bundle bytes are stored
    download_url: str = ""  # API URL to download the bundle
    cl_support_log: str = ""  # full text output from the cl-support command


# =============================================================================
# Activity Functions
# =============================================================================


@activity.defn
def run_diagnostic_commands(activity_input: RunDiagnosticsInput) -> RunDiagnosticsOutput:
    valid_commands = validate_commands(
        activity_input.device_data.platform,
        activity_input.commands,
    )
    outputs: dict[str, str] = {}
    with closing(NetworkConnection.from_device_data(activity_input.device_data)) as connection:
        for name in valid_commands:
            try:
                outputs[name] = connection.run_diagnostic_command(name)
            except Exception as e:
                # per-command failure captured, never aborts the device
                outputs[name] = f"ERROR: {e}"
    return RunDiagnosticsOutput(device_name=activity_input.device_data.name, outputs=outputs)


@activity.defn
def collect_tech_support_bundle(activity_input: TechSupportInput) -> TechSupportOutput:
    """Collect a cl-support bundle from a device and store its bytes in Redis.

    Sync activity — runs in the ThreadPoolExecutor so activity.heartbeat() is
    reliably delivered from the thread. The heartbeat_fn callback is called:
      - during quiet stretches of cl-support output (every ~20 s of silence)
      - during the SFTP download on each received chunk (rate-limited to 10 s)
    This keeps the 60-second heartbeat window well-satisfied throughout.

    Bytes are stored in Redis (not returned through Temporal) to avoid a ~25 MB
    JSON payload that would exceed the heartbeat window during result transmission.
    The activity returns only a Redis key and a download URL.
    """
    device_name = activity_input.device_data.name
    info = activity.info()
    start = time.monotonic()

    def _heartbeat() -> None:
        elapsed = int(time.monotonic() - start)
        activity.heartbeat(f"Generating cl-support bundle on {device_name} ({elapsed}s elapsed)...")

    with closing(NetworkConnection.from_device_data(activity_input.device_data)) as connection:
        content, cl_support_log = connection.get_tech_support_bundle(_heartbeat)

    # Store raw bytes in Redis; never transmit them through Temporal.
    redis_key = f"tech_support:{info.workflow_id}:{device_name}"
    # Use the external-facing API URL from the INI config so the download link
    # is always user-reachable (api_url, not the internal api_service).
    config = load_config()
    api_base = config.get("temporal", "api_url", fallback="").rstrip("/")
    download_url = (
        f"{api_base}/v1/workflow/{info.workflow_id}/tech-support/{device_name}" if api_base else ""
    )

    async def _save() -> None:
        cache = RedisClient.from_config(load_config())
        await cache.set(redis_key, content, ttl=timedelta(hours=24), serialize=False)

    asyncio.run(_save())

    activity.heartbeat(
        f"Bundle stored in Redis on {device_name} ({len(content)} bytes, "
        f"{int(time.monotonic() - start)}s total). Key: {redis_key}\n\n"
        f"cl-support output:\n{cl_support_log}"
    )
    return TechSupportOutput(
        device_name=device_name,
        redis_key=redis_key,
        download_url=download_url,
        cl_support_log=cl_support_log,
    )
