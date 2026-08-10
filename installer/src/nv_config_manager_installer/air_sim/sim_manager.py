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
"""DSX Air simulation manager for nvcm-air-simulation."""

from __future__ import annotations

import ipaddress
import logging
import os
import platform
import re
import select
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from air_sdk import AirApi
from air_sdk.endpoints.user_configs import UserConfig as UserConfigModel

from nv_config_manager_installer.air_sim.constants import (
    AGGRESSIVE_DHCLIENT_CONF,
    CONFIG_MANAGER_DHCP_DEPLOYMENT,
    CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT,
    CONFIG_MANAGER_HOSTNAME,
    CONFIG_MANAGER_NAMESPACE,
    CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    CONFIG_MANAGER_RENDER_API_DEPLOYMENT,
    CONFIG_MANAGER_TEMPORAL_FRONTEND_DEPLOYMENT,
    CONFIG_MANAGER_TEMPORAL_WORKER_DEPLOYMENT,
    CONFIG_MANAGER_ZTP_DEPLOYMENT,
    DEFAULT_AIR_API_URL,
    DEFAULT_AIR_INTERNAL_URL,
    DEFAULT_AIR_ORG,
    DEFAULT_CONFIG_MANAGER_REPO,
    DEFAULT_NAUTOBOT_DEMO_PASSWORD,
    DEFAULT_NAUTOBOT_DEMO_USERNAME,
    NVCM_BOX_USER,
    NVCM_KIND_CONFIG,
    NVCM_SECRETS,
    NVCM_SERVER_SETUP_SCRIPT,
)
from nv_config_manager_installer.air_sim.models import NVCMServerConfig

LOG = logging.getLogger(__name__)
_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _cumulus_vx_image_name(version: str) -> str:
    """Return the preferred DSX Air image name for a Cumulus VX version."""
    return f"cumulus-vx-{version}"


def _image_name(image: Any) -> str:
    """Return a DSX Air image name from SDK model-like data."""
    return str(getattr(image, "name", "") or "").strip()


def _image_timestamp(image: Any) -> float:
    """Return a sortable timestamp for newest-image selection."""
    value = getattr(image, "modified", None) or getattr(image, "created", None)
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    else:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.timestamp()


def _is_close_cumulus_vx_match(image: Any, version: str) -> bool:
    """Return True when an image looks like a Cumulus VX build for *version*."""
    name = _image_name(image).lower()
    image_version = str(getattr(image, "version", "") or "").lower()
    version = version.lower()
    return (
        "cumulus" in name
        and "vx" in name
        and (version in name or image_version.startswith(version))
    )


def _recent_cumulus_vx_image_names(images: list[Any], limit: int = 8) -> list[str]:
    """Return recent Cumulus VX image names for error context."""
    candidates = [image for image in images if _is_close_cumulus_vx_match(image, "")]
    newest = sorted(candidates, key=_image_timestamp, reverse=True)
    return [_image_name(image) for image in newest[:limit] if _image_name(image)]


_NAUTOBOT_PROVISIONING_NBSHELL = (
    "from nautobot.dcim.models import Device;"
    "qs=Device.objects.filter(platform__name='Cumulus Linux');"
    "total=qs.count();"
    "status_field=Device._meta.get_field('status');"
    "status_lookup='status__name' if getattr(status_field,'remote_field',None) else 'status';"
    "status_filter={status_lookup:'Provisioned'};"
    "prov=qs.filter(**status_filter).count();"
    "pending=qs.exclude(**status_filter);"
    "names=[d.name for d in pending.order_by('name')[:5]];"
    "print(f'{prov}/{total}|{chr(44).join(names)}')"
)

_NAUTOBOT_INTENDED_CONFIG_NBSHELL = (
    "from nv_config_manager.models import ConfigManagerDeviceStatus, IntendedConfig;"
    "total=ConfigManagerDeviceStatus.objects.filter(render_enabled=True).count();"
    "ready=IntendedConfig.objects.filter(device_id__render_enabled=True).count();"
    "print(f'{ready}/{total}')"
)


class AirSimulationManager:
    """Manage DSX Air simulations for e2e testing."""

    def __init__(
        self,
        api_url: str | None = None,
        ngc_api_key: str | None = None,
        org_id: str | None = None,
        use_internal: bool = False,
        ssh_password: str = "",
    ) -> None:
        """Initialize the DSX Air simulation manager.

        Args:
            api_url: DSX Air API URL (auto-detected if not provided)
            ngc_api_key: NGC API key (Starfleet API Key / SAK) for auth
            org_id: DSX Air organization ID for the simulation
            use_internal: Use internal DSX Air instance (api.air-inside.nvidia.com)
            ssh_password: Password for the nvcm account on the OOB management server
        """
        self.api_url = api_url or (
            DEFAULT_AIR_INTERNAL_URL if use_internal else DEFAULT_AIR_API_URL
        )

        self.ngc_api_key = ngc_api_key or os.environ.get("NGC_API_KEY")
        self.org_id = org_id or os.environ.get("AIR_ORG_ID", DEFAULT_AIR_ORG)
        self.ssh_password = ssh_password

        if not self.ngc_api_key:
            LOG.error("No NGC API key found. Set NGC_API_KEY env var or pass --ngc-api-key.")
            raise ValueError("Missing NGC API key for DSX Air authentication")

        LOG.info("Authenticating with NGC API key (Bearer token)...")
        self.client = AirApi.with_api_key(
            api_key=self.ngc_api_key,
            api_url=self.api_url,
        )

    def create_simulation(
        self,
        name: str,
        topology: dict[str, Any],
    ) -> str:
        """Create a new DSX Air simulation.

        Args:
            name: Simulation name
            topology: DSX Air topology JSON

        Returns:
            Simulation ID
        """
        LOG.info(f"Creating simulation: {name}")

        simulation = self.client.simulations.import_from_data(
            format="JSON",
            content=topology,
            name=name,
        )

        LOG.info(f"Created simulation: {simulation.id}")
        return simulation.id

    def resolve_cumulus_vx_images(self, versions: Iterable[str]) -> dict[str, str]:
        """Validate and resolve Cumulus VX DSX Air image names by firmware version.

        Exact ``cumulus-vx-<version>`` image names are preferred. If the exact
        name is unavailable, the newest visible Cumulus VX image containing the
        requested version is selected.
        """
        required_versions = sorted({version for version in versions if version})
        if not required_versions:
            return {}

        LOG.info(
            "Validating Cumulus VX DSX Air image(s): %s",
            ", ".join(required_versions),
        )
        images = list(self.client.images.list())
        if not images:
            raise RuntimeError("DSX Air image list is empty; cannot validate Cumulus VX images")

        resolved: dict[str, str] = {}
        for version in required_versions:
            image = self._select_cumulus_vx_image(images, version)
            expected_name = _cumulus_vx_image_name(version)
            if image is None:
                recent = ", ".join(_recent_cumulus_vx_image_names(images)) or "none"
                raise RuntimeError(
                    f"DSX Air image not found for Cumulus Linux {version}. "
                    f"Expected '{expected_name}' or a Cumulus VX image containing "
                    f"'{version}'. Recent Cumulus VX images: {recent}"
                )

            image_name = _image_name(image)
            resolved[version] = image_name
            if image_name == expected_name:
                LOG.info("Found DSX Air image %s for Cumulus Linux %s", image_name, version)
            else:
                LOG.info(
                    "Using DSX Air image %s for Cumulus Linux %s (preferred %s was not present)",
                    image_name,
                    version,
                    expected_name,
                )

        return resolved

    @staticmethod
    def _select_cumulus_vx_image(images: list[Any], version: str) -> Any | None:
        """Select the preferred DSX Air image for a Cumulus firmware version."""
        expected_name = _cumulus_vx_image_name(version)
        exact_matches = [image for image in images if _image_name(image) == expected_name]
        if exact_matches:
            return max(exact_matches, key=_image_timestamp)

        close_matches = [image for image in images if _is_close_cumulus_vx_match(image, version)]
        if close_matches:
            return max(close_matches, key=_image_timestamp)
        return None

    # ------------------------------------------------------------------
    # Cloud-init UserConfig attach / cleanup
    # ------------------------------------------------------------------

    def attach_cloud_init(
        self,
        simulation_id: str,
        node_name: str,
        cloud_init_content: str,
    ) -> None:
        """Create a cloud-init UserConfig and attach it to a node.

        Uses a fixed, deterministic name so that repeated runs reuse
        the same UserConfig rather than creating orphans.  Must be
        called *before* the simulation is started so that cloud-init
        runs on first boot.
        """
        target_node = None
        for attempt in range(6):
            for node in self.client.nodes.list(simulation=simulation_id):
                if node.name == node_name:
                    target_node = node
                    break
            if target_node:
                break
            LOG.info(
                "Node '%s' not yet visible (attempt %d/6), retrying...",
                node_name,
                attempt + 1,
            )
            time.sleep(5)

        if not target_node:
            raise ValueError(f"Node '{node_name}' not found in simulation {simulation_id}")

        config_name = f"{node_name}-cloud-init"

        user_config = None
        for cfg in self.client.user_configs.list():
            if getattr(cfg, "name", None) == config_name:
                cfg.update(content=cloud_init_content)
                user_config = cfg
                LOG.info("Updated existing UserConfig '%s': %s", config_name, cfg.id)
                break

        if user_config is None:
            user_config = self.client.user_configs.create(
                name=config_name,
                kind=UserConfigModel.KIND_CLOUD_INIT_USER_DATA,
                organization=self.org_id or None,
                content=cloud_init_content,
            )
            LOG.info("Created UserConfig '%s': %s", config_name, user_config.id)

        target_node.set_cloud_init_assignment({"user_data": user_config.id})
        LOG.info("Attached cloud-init to node '%s'", node_name)

    def prepare_nvcm_server(self, simulation_id: str, server_name: str) -> None:
        """Ensure the nvcm server node has an eth0 outbound interface for SSH.

        The switch nodes are left unconfigured - they will get their configuration
        via ZTP from the NVCM server running inside the simulation.

        Args:
            simulation_id: ID of the simulation
            server_name: Name of the server node (existing or newly created)
        """
        LOG.info(f"Preparing {server_name} for external access...")

        target_node = None
        for attempt in range(6):
            for node in self.client.nodes.list(simulation=simulation_id):
                if node.name == server_name:
                    target_node = node
                    break
            if target_node:
                break
            LOG.info(
                "Node '%s' not yet visible (attempt %d/6), retrying...",
                server_name,
                attempt + 1,
            )
            time.sleep(5)

        if not target_node:
            LOG.warning("Node '%s' not found; skipping OOB prep", server_name)
            return

        has_eth0 = any(
            iface.name == "eth0" for iface in self.client.interfaces.list(node=target_node)
        )
        if not has_eth0:
            LOG.info(f"Creating eth0 outbound interface for {server_name}")
            self.client.interfaces.create(
                name="eth0",
                node=target_node,
                interface_type="OOB_INTF",
                link_up=True,
                outbound=True,
            )

    def create_ssh_service(
        self,
        simulation_id: str,
        node_name: str,
        interface_name: str = "eth0",
    ) -> tuple[str, int] | None:
        """Create an SSH service for a node and return (host, port).

        Checks for an existing SSH service first to avoid duplicates.

        Args:
            simulation_id: Simulation ID
            node_name: Name of the node
            interface_name: Interface to attach the service to

        Returns:
            (host, port) tuple, or None if the node/interface wasn't found
        """
        for node in self.client.nodes.list(simulation=simulation_id):
            if node.name != node_name:
                continue
            target_iface = None
            for iface in self.client.interfaces.list(node=node):
                if iface.name == interface_name:
                    target_iface = iface
                    break
            if not target_iface:
                LOG.warning(f"Interface {interface_name} not found on {node_name}")
                return None

            # Check for existing SSH service on this interface
            existing = self.client.services.list(simulation=simulation_id)
            for svc in existing:
                if svc.interface.id == target_iface.id and svc.node_port == 22:
                    LOG.info(
                        f"SSH service already exists for "
                        f"{node_name}:{interface_name} "
                        f"-> {svc.worker_fqdn}:{svc.worker_port}"
                    )
                    return (svc.worker_fqdn, svc.worker_port)

            # Create new SSH service
            svc = self.client.services.create(
                name=f"{node_name} SSH",
                interface=target_iface,
                node_port=22,
                service_type="SSH",
            )
            LOG.info(
                f"Created SSH service for {node_name}:{interface_name} "
                f"-> {svc.worker_fqdn}:{svc.worker_port}"
            )
            return (svc.worker_fqdn, svc.worker_port)

        LOG.warning(f"Node '{node_name}' not found in simulation")
        return None

    # ------------------------------------------------------------------
    # Server netplan via node instructions
    # ------------------------------------------------------------------

    def attach_server_netplan(
        self,
        simulation_id: str,
        server_name: str,
        ssh_mac: str,
    ) -> None:
        """Configure the SSH interface on the nvcm-box via node instructions.

        Uses the ``file`` executor to write a netplan config that matches
        the outbound interface by MAC and enables DHCP.  Runs before the
        simulation is started; the DSX Air agent delivers the file on first
        boot and then runs ``netplan apply``.

        Args:
            simulation_id: Simulation ID.
            server_name: Name of the oob-mgmt-server node.
            ssh_mac: MAC address of the outbound (SSH) interface.
        """
        netplan_content = (
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    oob-ssh:\n"
            "      match:\n"
            f"        macaddress: {ssh_mac.lower()}\n"
            "      dhcp4: true\n"
        )

        data_payload = {
            "files": [
                {
                    "path": "/etc/netplan/99-oob-ssh.yaml",
                    "content": netplan_content,
                }
            ],
            "post_commands": [
                "#!/bin/bash\nnetplan apply",
            ],
        }

        target_node = None
        for attempt in range(6):
            for node in self.client.nodes.list(
                simulation=simulation_id,
            ):
                if node.name == server_name:
                    target_node = node
                    break
            if target_node:
                break
            LOG.info(
                "Node '%s' not yet visible (attempt %d/6)...",
                server_name,
                attempt + 1,
            )
            time.sleep(5)

        if not target_node:
            LOG.error(
                "Node '%s' not found -- cannot attach netplan",
                server_name,
            )
            return

        target_node.instructions.create(
            executor="file",
            data=data_payload,
            wait_for_network=False,
        )
        LOG.info(
            "Attached netplan instruction to %s (SSH MAC %s)",
            server_name,
            ssh_mac,
        )

    def resolve_iface_by_mac(
        self,
        host: str,
        port: int,
        mac: str,
    ) -> str | None:
        """Resolve an interface name by its MAC address via SSH."""
        ssh_base = self._ssh_cmd(host, port)
        cmd = ssh_base + [f"ip -o link | grep -i '{mac}' | awk -F': ' '{{print $2}}' | head -1"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            iface = result.stdout.strip()
            if iface:
                LOG.info("Resolved MAC %s -> %s", mac, iface)
                return iface
        except Exception:
            pass
        LOG.warning("Could not resolve interface for MAC %s", mac)
        return None

    def prepare_server(
        self,
        host: str,
        port: int,
        *,
        internal_mac: str,
        internal_ip: str,
        site_name: str,
        oob_gateway: str | None,
        relay_return_networks: list[str] | None = None,
        bgp_asn: str = "4266000000",
    ) -> str | None:
        """Configure the nvcm-box server for DSX Air after --setup completes.

        Runs via SSH after ``nvcm-box-setup.sh --setup`` finishes on boot.
        Sets up everything the old cloud-init setup script used to do:

        1. Internal interface IP + routes (resolved by MAC address)
        2. FRR/BGP config for OOB switch peering
        3. IP forwarding + MASQUERADE

        Args:
            host: SSH hostname (from DSX Air service).
            port: SSH port.
            internal_mac: MAC of the internal interface (oob-mgmt-switch).
            internal_ip: IP/CIDR for the internal interface.
            site_name: Site name for secrets.ini.
            oob_gateway: Peer IP for BGP / next-hop for relay-return routes.
            relay_return_networks: Prefixes that need return routes via OOB gw.
            bgp_asn: BGP AS number for FRR.

        Returns:
            Resolved internal interface name on success, ``None`` on failure.
        """
        LOG.info("Preparing nvcm-box server via SSH...")
        ssh_base = self._ssh_cmd(host, port)
        gw = oob_gateway or "UNSET"
        rr_nets = relay_return_networks or []
        ztp_url_host = internal_ip.split("/")[0]

        def _ssh(cmd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        try:
            # -- 0. Resolve internal interface name by MAC ---------------------
            iface_name = _ssh(
                f"ip -o link | grep -i '{internal_mac.lower()}'"
                " | awk -F': ' '{print $2}' | head -1"
            ).stdout.strip()
            if not iface_name:
                LOG.error(
                    "Could not find interface with MAC %s",
                    internal_mac,
                )
                return None
            LOG.info(
                "Internal interface: %s (MAC %s)",
                iface_name,
                internal_mac,
            )

            # -- 1. Internal interface IP + routes -----------------------------
            LOG.info(
                "Configuring %s internal network (%s)...",
                iface_name,
                internal_ip,
            )
            _ssh(f"sudo ip addr add {internal_ip} dev {iface_name} 2>/dev/null || true")
            _ssh(f"sudo ip link set {iface_name} up")
            internal_network = str(ipaddress.ip_network(internal_ip, strict=False))
            _ssh(f"sudo ip route add {internal_network} dev {iface_name} 2>/dev/null || true")
            for rr_net in rr_nets:
                _ssh(
                    f"sudo ip route replace {rr_net} via {gw} dev {iface_name} 2>/dev/null || true"
                )
            LOG.info("  %s configured", iface_name)

            # -- 2. FRR/BGP ----------------------------------------------------
            LOG.info("Configuring FRR/BGP (ASN %s, neighbor %s)...", bgp_asn, gw)
            kind_subnet = _ssh(
                "sudo docker network inspect kind"
                " -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null"
                " | grep -oE '([0-9]+\\.){3}[0-9]+/[0-9]+' | head -1"
            ).stdout.strip()
            kind_prefix = ".".join(kind_subnet.split(".")[:2]) if kind_subnet else "172.18"
            frr_metallb_prefix = f"{kind_prefix}.255.0/24"

            bridge_id = _ssh(
                "sudo docker network inspect kind -f '{{.Id}}' 2>/dev/null | cut -c1-12"
            ).stdout.strip()
            if bridge_id:
                _ssh(
                    f"sudo ip route add {frr_metallb_prefix} dev br-{bridge_id} 2>/dev/null || true"
                )

            _ssh("sudo sed -i 's/^bgpd=no/bgpd=yes/' /etc/frr/daemons")

            frr_conf = (
                "frr version 10\n"
                "frr defaults traditional\n"
                "hostname nvcm-box\n"
                "log syslog informational\n"
                "service integrated-vtysh-config\n"
                "!\n"
                f"ip prefix-list PL-METALLB seq 10 permit {frr_metallb_prefix}\n"
                "ip prefix-list PL-METALLB seq 9999 deny any\n"
                "!\n"
                "route-map RM-EXPORT permit 10\n"
                " match ip address prefix-list PL-METALLB\n"
                "route-map RM-EXPORT deny 9999\n"
                "!\n"
                f"router bgp {bgp_asn}\n"
                f" bgp router-id {ztp_url_host}\n"
                " no bgp ebgp-requires-policy\n"
                f" neighbor {gw} remote-as external\n"
                " !\n"
                " address-family ipv4 unicast\n"
                "  redistribute kernel route-map RM-EXPORT\n"
                f"  neighbor {gw} route-map RM-EXPORT out\n"
                " exit-address-family\n"
                "!\n"
            )
            _ssh(f"sudo tee /etc/frr/frr.conf > /dev/null << 'FRREOF'\n{frr_conf}FRREOF")
            _ssh("sudo systemctl enable frr")
            _ssh("sudo systemctl restart frr")
            LOG.info("  FRR BGP configured, advertising %s", frr_metallb_prefix)

            # -- 3. IP forwarding + MASQUERADE ---------------------------------
            LOG.info("Enabling IP forwarding and MASQUERADE...")
            _ssh("sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null")
            _ssh(
                "grep -q 'net.ipv4.ip_forward=1' /etc/sysctl.conf"
                " || echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf > /dev/null"
            )
            _ssh(
                "sudo iptables -t nat -C POSTROUTING -d 172.18.0.0/16 -j MASQUERADE 2>/dev/null"
                " || sudo iptables -t nat -A POSTROUTING -d 172.18.0.0/16 -j MASQUERADE"
            )
            LOG.info("  Forwarding enabled")

            LOG.info("Server preparation complete (internal iface: %s)", iface_name)
            return iface_name

        except Exception as exc:
            LOG.error("Failed to prepare server: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Aggressive dhclient tuning for Cumulus switches
    # ------------------------------------------------------------------

    def attach_dhclient_tuning(
        self,
        simulation_id: str,
        cumulus_device_names: list[str],
    ) -> list[Any]:
        """Push aggressive dhclient.conf onto Cumulus switches via node instructions.

        Uses the Air SDK ``file`` executor to overwrite
        ``/etc/dhcp/dhclient.conf`` with shorter retry/timeout values so
        switches acquire DHCP leases faster in simulations.

        Must be called *before* the simulation is started.

        Args:
            simulation_id: Simulation ID.
            cumulus_device_names: Names of Cumulus nodes to configure.

        Returns:
            List of created NodeInstruction objects.
        """
        target_names = set(cumulus_device_names)
        instructions: list[Any] = []

        data_payload = {
            "files": [
                {
                    "path": "/etc/dhcp/dhclient.conf",
                    "content": AGGRESSIVE_DHCLIENT_CONF,
                }
            ],
            "post_commands": [
                "#!/bin/bash\npkill -HUP dhclient 2>/dev/null || true",
            ],
        }

        for node in self.client.nodes.list(simulation=simulation_id):
            if node.name not in target_names:
                continue

            instr = node.instructions.create(
                executor="file",
                data=data_payload,
                wait_for_network=False,
            )
            instructions.append(instr)
            LOG.debug(f"Created dhclient tuning instruction for {node.name}")

        LOG.info(
            f"Attached dhclient tuning to {len(instructions)}/{len(target_names)} Cumulus switches"
        )
        return instructions

    # ------------------------------------------------------------------
    # SSH poll + log tail for cloud-init progress
    # ------------------------------------------------------------------

    _SSH_OPTS = [
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
    ]

    def _ssh_cmd(
        self,
        host: str,
        port: int,
        command: str | None = None,
    ) -> list[str]:
        """Build an SSH command list with sshpass for password auth."""
        ssh_password = self._require_ssh_password()
        base = [
            "sshpass",
            "-p",
            ssh_password,
            "ssh",
            *AirSimulationManager._SSH_OPTS,
            "-p",
            str(port),
            f"{NVCM_BOX_USER}@{host}",
        ]
        if command is not None:
            base.append(command)
        return base

    def _require_ssh_password(self) -> str:
        if not self.ssh_password:
            raise RuntimeError("OOB SSH password not configured")
        return self.ssh_password

    _SETUP_COMPLETE_MARKER = "NVCM DSX Air Setup Complete"
    _SETUP_LOG_PATHS = ("/var/log/cloud-init-output.log", "/var/log/nvcm-setup.log")
    _DEPLOY_COMPLETE_MARKER = "Deployment completed successfully!"

    _SOCKS_PORT = 8080

    def _remote_setup_marker_exists(self, ssh_base: list[str]) -> bool:
        grep_cmd = " ".join(
            shlex.quote(part)
            for part in (
                "sudo",
                "grep",
                "-F",
                "-q",
                "--",
                self._SETUP_COMPLETE_MARKER,
                *self._SETUP_LOG_PATHS,
            )
        )
        try:
            result = subprocess.run(
                [*ssh_base, grep_cmd],
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    @staticmethod
    def _terminate_process(proc: subprocess.Popen[str]) -> None:
        """Stop a child process without letting cleanup timeouts mask the result."""
        if proc.poll() is not None:
            return

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                LOG.warning("SSH log process did not exit after being killed.")

    def _ssh_run_and_tail(
        self,
        host: str,
        port: int,
        command: str,
        *,
        marker: str,
        label: str = "oob-mgmt-server",
        timeout: int = 3600,
    ) -> bool:
        """Run a command over SSH and stream its output locally.

        Watches for *marker* in the output to detect completion.

        Args:
            host: SSH hostname.
            port: SSH port.
            command: Shell command to execute on the remote host.
            marker: String that signals successful completion.
            label: Prefix for each output line.
            timeout: Max seconds to wait.

        Returns:
            True if marker was seen, False on timeout or drop.
        """
        deadline = time.monotonic() + timeout
        ssh_base = self._ssh_cmd(host, port)

        try:
            proc = subprocess.Popen(
                [*ssh_base, command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            assert proc.stdout is not None
            for line in proc.stdout:
                line = _ANSI_ESCAPE.sub("", line).rstrip("\n").rstrip("\r")
                LOG.info("[%s] %s", label, line)

                if marker in line:
                    LOG.info(f"\n{marker}")
                    proc.terminate()
                    proc.wait(timeout=5)
                    return True

                if time.monotonic() >= deadline:
                    LOG.warning(
                        "\nTimed out waiting for command to complete. Check the server manually."
                    )
                    proc.terminate()
                    proc.wait(timeout=5)
                    return False

            rc = proc.wait(timeout=5)
            if rc == 0:
                LOG.info("\nCommand finished (exit 0).")
                return True
            LOG.warning(f"\nCommand exited with code {rc}. Check the server manually.")
            return False

        except KeyboardInterrupt:
            LOG.info("\nTailing interrupted.")
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            return False

    _RSYNC_EXCLUDES = [
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        "*.pyc",
        ".mypy_cache",
        ".pytest_cache",
    ]

    def upload_to_server(
        self,
        host: str,
        port: int,
        local_path: str,
        remote_path: str,
        *,
        excludes: list[str] | None = None,
        timeout: int = 300,
    ) -> bool:
        """Upload a local directory to the remote server via rsync.

        Args:
            host: SSH hostname.
            port: SSH port.
            local_path: Local directory to upload.
            remote_path: Destination path on the remote server.
            excludes: Extra rsync exclude patterns (merged with defaults).
            timeout: Max seconds for the transfer.

        Returns:
            True if rsync succeeded, False otherwise.
        """
        local_resolved = Path(local_path).resolve()
        # rsync trailing slash means "copy contents of dir"; files must NOT have it
        local = str(local_resolved) + ("/" if local_resolved.is_dir() else "")

        ssh_password = self._require_ssh_password()
        ssh_opts_flat = f"sshpass -p {shlex.quote(ssh_password)} ssh -p {port} " + " ".join(
            f"{self._SSH_OPTS[i]} {self._SSH_OPTS[i + 1]}" for i in range(0, len(self._SSH_OPTS), 2)
        )

        all_excludes = list(self._RSYNC_EXCLUDES)
        if excludes:
            all_excludes.extend(excludes)

        cmd = [
            "rsync",
            "-az",
            "--delete",
            "-e",
            ssh_opts_flat,
        ]
        for exc in all_excludes:
            cmd.extend(["--exclude", exc])

        cmd.extend(
            [
                local,
                f"{NVCM_BOX_USER}@{host}:{remote_path}",
            ]
        )

        LOG.info("Uploading %s -> %s:%s ...", local_path, host, remote_path)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                LOG.error("rsync failed (exit %d): %s", result.returncode, result.stderr)
                return False
            LOG.info("Upload complete: %s", remote_path)
            return True
        except subprocess.TimeoutExpired:
            LOG.error("rsync timed out after %ds", timeout)
            return False

    def run_deploy(
        self,
        host: str,
        port: int,
        deploy_cmd: str,
        timeout: int = 3600,
        horizontal: bool = False,
    ) -> bool:
        """Stream nv-config-manager-installer deploy output via SSH, return True on success.

        Args:
            host: SSH hostname (from DSX Air service).
            port: SSH port (from DSX Air service).
            deploy_cmd: Full installer command string.
            timeout: Max seconds to wait (default 60 min).
            horizontal: Accepted for CLI compatibility; streaming here is plain text.

        Returns:
            True if deploy completed successfully, False otherwise.
        """
        LOG.info("Running installer (this may take 15-30 min)...")
        return self._ssh_run_and_tail(
            host,
            port,
            deploy_cmd,
            marker=self._DEPLOY_COMPLETE_MARKER,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Post-deploy helpers
    # ------------------------------------------------------------------

    _NVCM_HOSTS = (
        f"{CONFIG_MANAGER_HOSTNAME} nautobot.{CONFIG_MANAGER_HOSTNAME}"
        f" render.{CONFIG_MANAGER_HOSTNAME}"
        f" ztp.{CONFIG_MANAGER_HOSTNAME} dhcp.{CONFIG_MANAGER_HOSTNAME}"
        f" workflow.{CONFIG_MANAGER_HOSTNAME}"
        f" config-store.{CONFIG_MANAGER_HOSTNAME} temporal.{CONFIG_MANAGER_HOSTNAME}"
        f" svc-workflow.{CONFIG_MANAGER_HOSTNAME}"
        f" svc-config-store.{CONFIG_MANAGER_HOSTNAME}"
        f" svc-render.{CONFIG_MANAGER_HOSTNAME} svc-ztp.{CONFIG_MANAGER_HOSTNAME}"
        f" svc-dhcp.{CONFIG_MANAGER_HOSTNAME} svc-nautobot.{CONFIG_MANAGER_HOSTNAME}"
    )

    def configure_etc_hosts(
        self,
        host: str,
        port: int,
    ) -> bool:
        """Add /etc/hosts entries pointing nvcm.air to the gateway MetalLB IP.

        The Envoy Gateway receives a MetalLB IP via L2 advertisement.
        We discover that IP and write it into ``/etc/hosts`` so that
        ``nvcm.air`` and its subdomains resolve on the server (used
        by the SOCKS proxy for browser access).
        """
        LOG.info("Configuring /etc/hosts for %s...", CONFIG_MANAGER_HOSTNAME)
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        try:
            gateway_ip = (
                subprocess.run(
                    [
                        *ssh_base,
                        f"{kube} kubectl get svc -n envoy-gateway-system"
                        " -l "
                        f"'gateway.envoyproxy.io/owning-gateway-namespace={CONFIG_MANAGER_NAMESPACE}'"
                        " -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}'"
                        " 2>/dev/null",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                .stdout.strip()
                .strip("'")
            )

            if not gateway_ip:
                LOG.warning("Could not discover gateway MetalLB IP; falling back to 127.0.0.1")
                gateway_ip = "127.0.0.1"

            hosts_line = f"{gateway_ip} {self._NVCM_HOSTS}"
            add_cmd = (
                f"grep -q '{CONFIG_MANAGER_HOSTNAME}' /etc/hosts"
                f" || echo '{hosts_line}'"
                f" | sudo tee -a /etc/hosts > /dev/null"
            )
            subprocess.run(
                [*ssh_base, add_cmd],
                capture_output=True,
                timeout=15,
            )
            LOG.info(
                "Added /etc/hosts: %s -> %s (+ subdomains)",
                gateway_ip,
                CONFIG_MANAGER_HOSTNAME,
            )
            return True
        except Exception as exc:
            LOG.warning("Failed to configure /etc/hosts: %s", exc)
            return False

    _ZTP_LB_IP = "172.18.255.201"
    _DHCP_LB_IP = "172.18.255.202"

    def configure_nat_rules(
        self,
        host: str,
        port: int,
        oob_gateway: str | None = None,
        relay_return_networks: list[str] | None = None,
        internal_iface: str = "eth1",
    ) -> bool:
        """Set up forwarding, routing, MASQUERADE, and isc-dhcp-relay.

        Mirrors ``nvcm-box-setup.sh configure_forwarding()`` (standard
        mode) with DSX Air-specific additions for relay-return networks.

        1. DOCKER-USER  -- allow forwarding internal <-> Kind bridge
        2. ZTP DNAT     -- TCP 80/443 from internal -> ZTP MetalLB IP
                           (needed before switches have BGP routes)
        3. MASQUERADE   -- general to 172.18.0.0/16, with exemptions
                           for relay source IP (UDP 67) and per-rr_net
                           ZTP client IP preservation
        4. DHCP reply SNAT -- per relay-return network, to Kea MetalLB IP
        5. Kind node routes -- relay-return prefixes via host
        6. Host route   -- relay-return prefixes via OOB switch
        7. isc-dhcp-relay -- broadcast DHCP on internal -> Kea MetalLB IP

        Args:
            internal_iface: Resolved name of the internal interface
                (facing the oob-mgmt-switch).
        """
        if oob_gateway is None:
            LOG.warning("No OOB gateway provided; skipping NAT/routing setup")
            return False
        gw = oob_gateway
        rr_nets = relay_return_networks or []
        LOG.info("Configuring forwarding rules and routing for DHCP/ZTP...")
        ssh_base = self._ssh_cmd(host, port)

        kube = "KUBECONFIG=/home/nvcm/.kube/config"

        def _ssh(cmd: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        # Discover ZTP and DHCP MetalLB IPs
        dhcp_ip = (
            _ssh(
                f"{kube} kubectl get svc -n {CONFIG_MANAGER_NAMESPACE}"
                f" {CONFIG_MANAGER_DHCP_DEPLOYMENT}-service"
                " -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null"
            ).stdout.strip()
            or self._DHCP_LB_IP
        )
        ztp_ip = (
            _ssh(
                f"{kube} kubectl get svc -n {CONFIG_MANAGER_NAMESPACE}"
                f" {CONFIG_MANAGER_ZTP_DEPLOYMENT}-service"
                " -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null"
            ).stdout.strip()
            or self._ZTP_LB_IP
        )
        LOG.info("ZTP service IP: %s", ztp_ip)
        LOG.info("DHCP service IP: %s", dhcp_ip)

        try:
            # -- 1. DOCKER-USER forwarding (internal <-> Kind bridge) ----------
            bridge_name = _ssh(
                "sudo docker network inspect kind -f '{{.Id}}' 2>/dev/null | cut -c1-12"
            ).stdout.strip()
            br_iface = f"br-{bridge_name}" if bridge_name else ""
            if br_iface:
                for direction in [
                    f"-i {internal_iface} -o {br_iface}",
                    f"-i {br_iface} -o {internal_iface}",
                ]:
                    _ssh(
                        f"sudo iptables -C DOCKER-USER {direction} -j ACCEPT"
                        f" 2>/dev/null ||"
                        f" sudo iptables -I DOCKER-USER 1 {direction} -j ACCEPT"
                    )

            # -- 2. ZTP DNAT (TCP 80/443 from internal iface) -----------------
            # Before switches have BGP routes to the MetalLB prefix, ZTP
            # traffic arrives on the internal iface destined for the server IP.
            # DNAT redirects it to the ZTP service.  No DNAT for UDP 67 --
            # isc-dhcp-relay handles initial broadcast DHCP and preserves giaddr.
            _ssh("sudo iptables -t nat -N ZTP-FWD 2>/dev/null || true")
            _ssh("sudo iptables -t nat -F ZTP-FWD")
            _ssh(
                f"sudo iptables -t nat -A ZTP-FWD -p tcp --dport 443"
                f" -j DNAT --to-destination {ztp_ip}:443"
            )
            _ssh(
                f"sudo iptables -t nat -A ZTP-FWD -p tcp --dport 80"
                f" -j DNAT --to-destination {ztp_ip}:80"
            )
            _ssh(
                f"sudo iptables -t nat -D PREROUTING -i {internal_iface}"
                " -j ZTP-FWD 2>/dev/null || true"
            )
            _ssh(f"sudo iptables -t nat -I PREROUTING 1 -i {internal_iface} -j ZTP-FWD")

            # -- 3. MASQUERADE exemptions (preserve client IP) ----------------
            for rr_net in rr_nets:
                _ssh(
                    f"sudo iptables -t nat -C POSTROUTING -s {rr_net}"
                    f" -d {ztp_ip} -j RETURN 2>/dev/null ||"
                    f" sudo iptables -t nat -I POSTROUTING 1 -s {rr_net}"
                    f" -d {ztp_ip} -j RETURN"
                )
            # DHCP: preserve relay source IP so Kea sees giaddr source
            _ssh(
                "sudo iptables -t nat -C POSTROUTING -d 172.18.0.0/16"
                " -p udp --dport 67 -j RETURN 2>/dev/null ||"
                " sudo iptables -t nat -I POSTROUTING -d 172.18.0.0/16"
                " -p udp --dport 67 -j RETURN"
            )
            # General MASQUERADE for all traffic to the Kind network
            _ssh(
                "sudo iptables -t nat -C POSTROUTING -d 172.18.0.0/16"
                " -j MASQUERADE 2>/dev/null ||"
                " sudo iptables -t nat -A POSTROUTING -d 172.18.0.0/16"
                " -j MASQUERADE"
            )

            # -- 4. SNAT for DHCP replies (pod -> relay-return nets) ----------
            for rr_net in rr_nets:
                _ssh(
                    f"while sudo iptables -t nat -D POSTROUTING -d {rr_net}"
                    " -p udp --dport 67 -j MASQUERADE 2>/dev/null; do true; done"
                )
                _ssh(
                    f"sudo iptables -t nat -C POSTROUTING -d {rr_net}"
                    f" -p udp --dport 67 -j SNAT --to-source {dhcp_ip} 2>/dev/null ||"
                    f" sudo iptables -t nat -I POSTROUTING 1 -d {rr_net}"
                    f" -p udp --dport 67 -j SNAT --to-source {dhcp_ip}"
                )

            # -- 5. Kind node routes (return path for DHCP/ZTP replies) -------
            kind_nodes = (
                _ssh(
                    "sudo docker ps --filter 'label=io.x-k8s.kind.cluster=nvcm'"
                    " --format '{{.Names}}'"
                )
                .stdout.strip()
                .splitlines()
            )
            for node_name in kind_nodes:
                if not node_name:
                    continue
                for rr_net in rr_nets:
                    _ssh(f"sudo docker exec {node_name} ip route replace {rr_net} via 172.18.0.1")

            # -- 6. Host route (reply path out internal to OOB switch) ---------
            for rr_net in rr_nets:
                _ssh(f"sudo ip route replace {rr_net} via {gw} dev {internal_iface}")

            # -- 7. isc-dhcp-relay (broadcast DHCP -> Kea) --------------------
            # The first OOB switch has no config yet, so it broadcasts DHCP on
            # its server-facing link. The server relay bootstraps that switch;
            # later switch-side relays handle downstream device DHCP.
            if br_iface:
                _ssh(
                    f'printf \'SERVERS="{dhcp_ip}"\\n'
                    f'INTERFACES="{internal_iface} {br_iface}"\\n'
                    f'OPTIONS=""\\n\''
                    " | sudo tee /etc/default/isc-dhcp-relay"
                )
                _ssh("sudo systemctl enable isc-dhcp-relay")
                _ssh("sudo systemctl restart isc-dhcp-relay")

            # Flush stale DHCP conntrack
            _ssh("sudo conntrack -D -p udp --dport 67 2>/dev/null || true")

            rr_str = ", ".join(rr_nets) if rr_nets else "(none)"
            ifc = internal_iface
            LOG.info(
                "Forwarding + routing configured:"
                "\n  DOCKER-USER:     %s <-> Kind bridge (ACCEPT)"
                "\n  ZTP DNAT:        %s TCP 80/443 -> %s"
                "\n  MASQUERADE skip: -s %s -d %s (ZTP client IP preserved)"
                "\n  MASQUERADE skip: -d 172.18.0.0/16 UDP 67 (relay source)"
                "\n  MASQUERADE:      -d 172.18.0.0/16 (general Kind traffic)"
                "\n  SNAT:            -d %s UDP 67 -> %s (DHCP reply)"
                "\n  Kind routes:     %s via 172.18.0.1"
                "\n  Host route:      %s via %s dev %s"
                "\n  isc-dhcp-relay:  %s + %s -> %s",
                ifc,
                ifc,
                ztp_ip,
                rr_str,
                ztp_ip,
                rr_str,
                dhcp_ip,
                rr_str,
                rr_str,
                gw,
                ifc,
                ifc,
                br_iface or "(no bridge)",
                dhcp_ip,
            )
            return True

        except Exception as exc:
            LOG.warning("Failed to configure forwarding/routing: %s", exc)
            return False

    def queue_render_all(
        self,
        host: str,
        port: int,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_RENDER_API_DEPLOYMENT,
        timeout: int = 90,
    ) -> bool:
        """Queue renders for every render-enabled Config Manager device."""
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        payload = b'{"commit_message":"DSX Air demo render"}'
        python_code = (
            "import urllib.request;"
            "req=urllib.request.Request("
            "'http://127.0.0.1:9000/v1/render/all',"
            f"data={payload!r},"
            "headers={'Content-Type':'application/json','X-Forwarded-User':'admin'},"
            "method='POST');"
            "print(urllib.request.urlopen(req, timeout=60).read().decode())"
        )
        cmd = (
            f"{kube} kubectl exec -n {namespace} deployment/{deployment} -- "
            f"python -c {shlex.quote(python_code)}"
        )
        try:
            LOG.info("Queueing render-all for Config Manager devices...")
            result = subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode != 0:
                LOG.warning("Failed to queue render-all: %s", output)
                return False
            if output:
                LOG.info("[render-all] %s", output.splitlines()[-1])
            return True
        except subprocess.TimeoutExpired:
            LOG.warning("Timed out queueing render-all")
            return False
        except Exception as exc:
            LOG.warning("Failed to queue render-all: %s", exc)
            return False

    def wait_for_intended_configs(
        self,
        host: str,
        port: int,
        expected_total: int | None = None,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
        timeout: int = 180,
        interval: int = 10,
    ) -> bool:
        """Wait until render-enabled devices have intended config records."""
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        cmd = (
            f"{kube} kubectl exec -i -n {namespace} deployment/{deployment}"
            f" -- nautobot-server nbshell --command "
            f"{shlex.quote(_NAUTOBOT_INTENDED_CONFIG_NBSHELL)}"
        )
        deadline = time.time() + timeout
        last_counts = "0/0"
        while time.time() < deadline:
            try:
                result = subprocess.run(
                    [*ssh_base, cmd],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines():
                        clean = line.strip()
                        if not re.match(r"^\d+/\d+$", clean):
                            continue
                        ready_str, _, total_str = clean.partition("/")
                        ready = int(ready_str)
                        total = int(total_str)
                        last_counts = clean
                        target = expected_total if expected_total is not None else total
                        if target and ready >= target:
                            LOG.info("Intended configs ready: %s", clean)
                            return True
                        break
            except Exception:
                pass
            time.sleep(interval)

        LOG.warning("Timed out waiting for intended configs; last count: %s", last_counts)
        return False

    def create_nautobot_demo_user(
        self,
        host: str,
        port: int,
        username: str = DEFAULT_NAUTOBOT_DEMO_USERNAME,
        password: str = DEFAULT_NAUTOBOT_DEMO_PASSWORD,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
        timeout: int = 60,
    ) -> bool:
        """Create or update demo/demo user in Nautobot via kubectl exec and nbshell."""
        ssh_base = self._ssh_cmd(host, port)
        script = (
            "from django.contrib.auth import get_user_model\n"
            "User = get_user_model()\n"
            f"u, created = User.objects.get_or_create(\n"
            f"    username={repr(username)},\n"
            f'    defaults={{"email": "{username}@localhost"}},\n'
            ")\n"
            f"u.set_password({repr(password)})\n"
            "u.is_superuser = True\n"
            "u.is_staff = True\n"
            "u.save()\n"
            f'print("Created user {username}" if created else "Updated password for {username}")\n'
        )
        heredoc_end = "NAUTOBOT_DEMO_END"
        cmd_create = f"cat > /tmp/demo_user.py << '{heredoc_end}'\n{script}\n{heredoc_end}"
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        cmd_run = (
            f"{kube} kubectl exec -i -n {namespace} deployment/{deployment} -- "
            "nautobot-server nbshell < /tmp/demo_user.py"
        )
        try:
            LOG.info("Creating Nautobot user %s via nbshell...", username)
            r1 = subprocess.run(
                [*ssh_base, cmd_create],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r1.returncode != 0:
                LOG.warning("Failed to write demo user script: %s", r1.stderr or r1.stdout)
                return False
            r2 = subprocess.run(
                [*ssh_base, cmd_run],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if r2.returncode != 0:
                LOG.warning("Failed to create demo user: %s", r2.stderr or r2.stdout)
                return False
            for line in (r2.stdout or "").strip().splitlines():
                LOG.info("[nautobot-demo] %s", line)
            return True
        except subprocess.TimeoutExpired:
            LOG.warning("Nautobot nbshell timed out")
            return False
        except Exception as exc:
            LOG.warning("Failed to create Nautobot demo user: %s", exc)
            return False

    def reset_cumulus_nodes(
        self,
        simulation_id: str,
        cumulus_device_names: list[str],
    ) -> int:
        """Reset all Cumulus switch nodes so they restart ZTP/DHCP from scratch.

        On Cumulus 5.14+, ZTP polls DHCP for only 5 minutes after boot.
        If the DHCP server wasn't reachable in that window (common for
        switches far from the relay, e.g. tan/cin tiers), the switch stops
        requesting DHCP entirely.  Resetting the node forces a fresh boot
        and a new ZTP cycle when the DHCP infrastructure is actually ready.

        This is fire-and-forget — device state is monitored via Nautobot,
        not by polling the Air API.

        Returns:
            Number of nodes that were successfully sent a reset request.
        """
        target_names = set(cumulus_device_names)
        if not target_names:
            return 0

        nodes_to_reset = [
            node
            for node in self.client.nodes.list(simulation=simulation_id)
            if node.name in target_names
        ]

        if not nodes_to_reset:
            LOG.warning("No matching Cumulus nodes found to reset")
            return 0

        LOG.info(
            "Resetting %d Cumulus node(s) to force fresh ZTP/DHCP cycle: %s",
            len(nodes_to_reset),
            ", ".join(n.name for n in nodes_to_reset),
        )
        reset_count = 0
        for node in nodes_to_reset:
            try:
                node.reset()
                reset_count += 1
                LOG.debug("Reset requested for %s", node.name)
            except Exception as exc:
                LOG.warning("Failed to reset node %s: %s", node.name, exc)

        LOG.info(
            "Reset requested for %d/%d node(s); monitor device state in Nautobot",
            reset_count,
            len(nodes_to_reset),
        )
        return reset_count

    def restart_dhcp_refresh(
        self,
        host: str,
        port: int,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT,
    ) -> bool:
        """Restart the DHCP refresh deployment so it syncs config immediately.

        After deployment finishes, the DHCP refresh CronJob/deployment may
        not run for up to 5 minutes.  Restarting forces an immediate sync
        of DHCP configuration from Nautobot data.
        """
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        cmd = (
            f"{kube} kubectl rollout restart deployment/{deployment} -n {namespace}"
            f" && {kube} kubectl rollout status deployment/{deployment} -n {namespace}"
            " --timeout=120s"
        )
        try:
            LOG.info("Restarting %s to force immediate DHCP config sync...", deployment)
            result = subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=150,
            )
            if result.returncode != 0:
                LOG.warning(
                    "Failed to restart %s: %s",
                    deployment,
                    result.stderr or result.stdout,
                )
                return False
            LOG.info("Restarted %s and rollout completed", deployment)
            return True
        except subprocess.TimeoutExpired:
            LOG.warning("Timed out restarting %s or waiting for rollout", deployment)
            return False
        except Exception as exc:
            LOG.warning("Failed to restart %s: %s", deployment, exc)
            return False

    _TEMPORAL_SEARCH_ATTRIBUTES = {
        "User": "Keyword",
        "DeviceID": "Keyword",
        "DeviceRole": "Keyword",
        "DeviceName": "Text",
        "DevicePlatform": "Keyword",
        "Site": "Text",
        "ReadRoles": "KeywordList",
        "ExecuteRoles": "KeywordList",
    }

    def ensure_temporal_search_attributes(
        self,
        host: str,
        port: int,
        *,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        max_attempts: int = 3,
        wait_between: int = 30,
    ) -> bool:
        """Verify Temporal search attributes exist; restart the worker if not.

        The ``temporal-setup`` init container on the worker pod registers
        custom search attributes on first boot.  When the Temporal DB
        isn't ready yet, the init container can silently fail, leaving the
        worker running without attributes like ``ReadRoles`` — which then
        causes RPCErrors at workflow time.

        This method:
        1. Lists search attributes on the Temporal frontend pod.
        2. If any expected attribute is missing, restarts the worker
           deployment (so the ``temporal-setup`` init container re-runs).
        3. Re-checks after the rollout, up to *max_attempts* times.

        Returns True if all attributes are present (or became present
        after a restart), False otherwise.
        """
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"

        def _ssh(cmd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        def _check_attributes() -> list[str]:
            """Return list of missing attribute names."""
            result = _ssh(
                f"{kube} kubectl exec -n {namespace}"
                f" deploy/{CONFIG_MANAGER_TEMPORAL_FRONTEND_DEPLOYMENT} --"
                " temporal operator search-attribute list"
                " --address localhost:7233 2>/dev/null"
            )
            output = result.stdout or ""
            missing = []
            for attr_name in self._TEMPORAL_SEARCH_ATTRIBUTES:
                if attr_name not in output:
                    missing.append(attr_name)
            return missing

        LOG.info("Checking Temporal search attributes...")

        for attempt in range(1, max_attempts + 1):
            try:
                missing = _check_attributes()
            except (subprocess.TimeoutExpired, Exception) as exc:
                LOG.warning(
                    "Could not check Temporal search attributes (attempt %d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(wait_between)
                continue

            if not missing:
                LOG.info("All Temporal search attributes are registered")
                return True

            LOG.warning(
                "Missing Temporal search attributes (attempt %d/%d): %s",
                attempt,
                max_attempts,
                ", ".join(missing),
            )

            if attempt < max_attempts:
                LOG.info(
                    "Restarting %s so temporal-setup init container re-runs...",
                    CONFIG_MANAGER_TEMPORAL_WORKER_DEPLOYMENT,
                )
                _ssh(
                    f"{kube} kubectl rollout restart"
                    f" deployment/{CONFIG_MANAGER_TEMPORAL_WORKER_DEPLOYMENT}"
                    f" -n {namespace}"
                )
                LOG.info(
                    "Waiting %ds for worker rollout to complete...",
                    wait_between,
                )
                _ssh(
                    f"{kube} kubectl rollout status"
                    f" deployment/{CONFIG_MANAGER_TEMPORAL_WORKER_DEPLOYMENT} -n {namespace}"
                    f" --timeout={wait_between}s 2>/dev/null || true",
                    timeout=wait_between + 15,
                )
                time.sleep(10)

        LOG.warning(
            "Temporal search attributes still missing after %d attempts. "
            "You may need to register them manually — see docs.",
            max_attempts,
        )
        return False

    def print_socks_instructions(
        self,
        host: str,
        port: int,
        *,
        open_browser: bool = False,
    ) -> None:
        """Print SOCKS proxy instructions and optionally launch Chrome."""
        ssh_password = self._require_ssh_password()
        socks_port = self._SOCKS_PORT
        ssh_cmd = (
            "sshpass -p '<password>'"
            f" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            f" -D {socks_port} -N -p {port}"
            f" {NVCM_BOX_USER}@{host}"
        )
        chrome_cmd = (
            "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome"
            f' --proxy-server="socks5://localhost:{socks_port}"'
            f' --user-data-dir="/tmp/chrome-nvcm-proxy"'
            " --ignore-certificate-errors"
            f" https://nautobot.{CONFIG_MANAGER_HOSTNAME}"
        )

        LOG.info(
            "\n=== Access NVCM UI ==="
            "\n\nTerminal 1 (SOCKS proxy):"
            "\n  %s"
            "\n\nTerminal 2 (Chrome with proxy):"
            "\n  %s"
            "\n\nDNS resolution happens through the proxy, "
            "so %s resolves on the oob-mgmt-server.",
            ssh_cmd,
            chrome_cmd,
            CONFIG_MANAGER_HOSTNAME,
        )

        if open_browser:
            if platform.system() == "Darwin":
                # Start SOCKS tunnel in background so Chrome can use it
                try:
                    tunnel_proc = subprocess.Popen(
                        [
                            "sshpass",
                            "-p",
                            ssh_password,
                            "ssh",
                            *self._SSH_OPTS,
                            "-D",
                            str(socks_port),
                            "-N",
                            "-p",
                            str(port),
                            f"{NVCM_BOX_USER}@{host}",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(2)  # Let tunnel establish
                    if tunnel_proc.poll() is not None:
                        LOG.warning(
                            "SOCKS tunnel exited immediately. Start it manually: %s",
                            ssh_cmd,
                        )
                    else:
                        LOG.info(
                            "SOCKS tunnel started in background (PID %s). Stop with: kill %s",
                            tunnel_proc.pid,
                            tunnel_proc.pid,
                        )
                except Exception as e:
                    LOG.warning("Could not start SOCKS tunnel: %s", e)
                    LOG.info("Start it manually in another terminal: %s", ssh_cmd)

                # Find Chrome/Chromium; path has spaces so pass as list to Popen
                _chrome_paths = (
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                )
                chrome_exe = None
                for p in _chrome_paths:
                    if os.path.isfile(p):
                        chrome_exe = p
                        break
                if chrome_exe:
                    LOG.info("Launching browser with SOCKS proxy...")
                    try:
                        subprocess.Popen(
                            [
                                chrome_exe,
                                f"--proxy-server=socks5://localhost:{socks_port}",
                                "--user-data-dir=/tmp/chrome-nvcm-proxy",
                                "--ignore-certificate-errors",
                                f"https://nautobot.{CONFIG_MANAGER_HOSTNAME}",
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception as e:
                        LOG.warning("Could not launch browser: %s", e)
                else:
                    LOG.warning(
                        "No Chrome/Chromium found in Applications. "
                        "Use the commands above to connect."
                    )
            else:
                LOG.info(
                    "Auto-launch is only supported on macOS. Use the commands above to connect."
                )

    def _poll_provisioning_status(
        self,
        host: str,
        port: int,
        expected_total: int,
        status_ref: dict[str, int | str],
        stop_event: threading.Event,
        done_event: threading.Event,
        interval: int = 30,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    ) -> None:
        """Poll Nautobot for the number of Cumulus Linux devices with status Provisioned.

        Updates *status_ref* in-place with ``provisioned``, ``total``, and
        ``detail`` keys.  Sets *done_event* when provisioned == expected_total.
        Runs in a background thread; exits when *stop_event* is set.
        """
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        nbshell_script = _NAUTOBOT_PROVISIONING_NBSHELL
        cmd = (
            f"{kube} kubectl exec -i -n {namespace} deployment/{deployment}"
            f' -- nautobot-server nbshell --command "{nbshell_script}"'
        )

        while not stop_event.wait(timeout=interval):
            try:
                result = subprocess.run(
                    [*ssh_base, cmd],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines():
                        if "/" in line:
                            counts, _, remaining = line.partition("|")
                            prov_str, _, total_str = counts.partition("/")
                            prov = int(prov_str.strip())
                            total = int(total_str.strip())
                            status_ref["provisioned"] = prov
                            status_ref["total"] = total
                            if remaining.strip():
                                status_ref["detail"] = remaining.strip()
                            else:
                                status_ref["detail"] = ""
                            if prov >= expected_total:
                                done_event.set()
                                return
                            break
            except Exception:
                pass

    def get_provisioning_status(
        self,
        host: str,
        port: int,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        deployment: str = CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    ) -> tuple[int, int, list[str]]:
        """Return (provisioned, total, remaining[:5]) from Nautobot, or (0,0,[]) on error."""
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        nbshell_script = _NAUTOBOT_PROVISIONING_NBSHELL
        cmd = (
            f"{kube} kubectl exec -i -n {namespace} deployment/{deployment}"
            f' -- nautobot-server nbshell --command "{nbshell_script}"'
        )
        try:
            result = subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if "/" in line:
                        counts, _, remaining = line.partition("|")
                        prov_str, _, total_str = counts.partition("/")
                        prov = int(prov_str.strip())
                        total = int(total_str.strip())
                        names = [n.strip() for n in remaining.split(",") if n.strip()]
                        return prov, total, names
        except Exception:
            pass
        return 0, 0, []

    def stream_nautobot_logs(
        self,
        host: str,
        port: int,
        on_line: Callable[[str], None],
        stop_event: threading.Event,
        on_pod_found: Callable[[], None] | None = None,
    ) -> None:
        """Tail the nautobot pod logs over SSH, retrying until the deployment exists.

        Runs until stop_event is set.
        """
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        cmd = (
            f"sudo {kube} kubectl logs -f -n {CONFIG_MANAGER_NAMESPACE}"
            f" deployment/{CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT}"
            " -c run-migrations --since=30m 2>&1"
        )
        on_line(f"Waiting for {CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT} pod to start...")
        pod_found = False
        while not stop_event.is_set():
            try:
                proc = subprocess.Popen(
                    [*ssh_base, cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    if stop_event.is_set():
                        break
                    clean = _ANSI_ESCAPE.sub("", line).rstrip("\n").rstrip("\r")
                    if not clean:
                        continue
                    # Suppress kubectl "Error from server" noise while the pod isn't up yet.
                    if not pod_found and clean.startswith("Error from server"):
                        continue
                    if not pod_found:
                        pod_found = True
                        if on_pod_found:
                            on_pod_found()
                    on_line(clean)
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception:
                pass
            if not stop_event.is_set():
                if not pod_found:
                    on_line("  Still waiting for namespace/pod... (retrying in 10s)")
                else:
                    on_line("[nautobot] reconnecting in 10s...")
                stop_event.wait(10)

    def get_pod_status(
        self, host: str, port: int, namespace: str = CONFIG_MANAGER_NAMESPACE
    ) -> list[dict[str, str]]:
        """Return pod rows from kubectl get pods over SSH.

        Each dict has keys: name, ready, status, restarts, age.
        Returns an empty list if SSH or kubectl fails.
        """
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        cmd = f"sudo {kube} kubectl get pods -n {namespace} --no-headers 2>/dev/null"
        try:
            result = subprocess.run(
                [*ssh_base, cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            pods: list[dict[str, str]] = []
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    pods.append(
                        {
                            "name": parts[0],
                            "ready": parts[1],
                            "status": parts[2],
                            "restarts": parts[3],
                            "age": parts[4],
                        }
                    )
            return pods
        except Exception:
            return []

    def get_service_log_snapshots(
        self,
        host: str,
        port: int,
        namespace: str = CONFIG_MANAGER_NAMESPACE,
        tail: int = 200,
        since: str = "30m",
    ) -> dict[str, list[str]]:
        """Return recent DHCP and ZTP log lines without opening streaming tails."""
        ssh_base = self._ssh_cmd(host, port)
        kube = "KUBECONFIG=/home/nvcm/.kube/config"
        commands = {
            "dhcp": (
                (
                    f"sudo {kube} kubectl logs -n {namespace}"
                    f" deployment/{CONFIG_MANAGER_DHCP_DEPLOYMENT}"
                    f" -c kea --tail={tail} --since={since} 2>/dev/null"
                ),
                (
                    f"sudo {kube} kubectl logs -n {namespace}"
                    f" deployment/{CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT}"
                    f" --tail={tail} --since={since} 2>/dev/null"
                ),
            ),
            "ztp": (
                (
                    f"sudo {kube} kubectl logs -n {namespace}"
                    f" deployment/{CONFIG_MANAGER_ZTP_DEPLOYMENT}"
                    f" -c http-lb --tail={tail} --since={since} 2>/dev/null"
                ),
            ),
        }
        snapshots: dict[str, list[str]] = {"dhcp": [], "ztp": []}
        for stream, stream_commands in commands.items():
            for cmd in stream_commands:
                try:
                    result = subprocess.run(
                        [*ssh_base, cmd],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                except Exception:
                    continue
                if result.returncode != 0:
                    continue
                snapshots[stream].extend(
                    _ANSI_ESCAPE.sub("", line).rstrip("\n").rstrip("\r")
                    for line in result.stdout.splitlines()
                    if line.strip()
                )
        return snapshots

    def monitor_services(
        self,
        host: str,
        port: int,
        expected_devices: int = 0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Tail DHCP and ZTP logs (interleaved plain text via LOG).

        - DHCP deployment logs filtered to DHCP4
        - ZTP deployment logs filtered to non-health

        When *expected_devices* > 0, polls Nautobot for provisioning status and
        automatically exits once all devices are Provisioned.
        When *stop_event* is provided, the loop exits when it is set.
        """
        self._monitor_services_plain(host, port, expected_devices, stop_event)

    def _monitor_services_plain(
        self,
        host: str,
        port: int,
        expected_devices: int = 0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Plain-text fallback for monitoring DHCP + ZTP logs."""
        ssh_base = self._ssh_cmd(host, port)

        dhcp_cmd = (
            "sudo KUBECONFIG=/home/nvcm/.kube/config"
            f" kubectl logs -f deployment/{CONFIG_MANAGER_DHCP_DEPLOYMENT}"
            f" -c kea -n {CONFIG_MANAGER_NAMESPACE} 2>&1"
            " | grep --line-buffered DHCP4"
        )
        ztp_cmd = (
            "sudo KUBECONFIG=/home/nvcm/.kube/config"
            f" kubectl logs -f deployment/{CONFIG_MANAGER_ZTP_DEPLOYMENT}"
            f" -c http-lb -n {CONFIG_MANAGER_NAMESPACE} 2>&1"
            " | grep --line-buffered -v health"
        )

        outer_stop = stop_event
        stop_event = threading.Event()
        done_event = threading.Event()
        status_ref: dict[str, int | str] = {
            "provisioned": 0,
            "total": expected_devices,
            "detail": "",
        }

        if expected_devices > 0:
            poll_thread = threading.Thread(
                target=self._poll_provisioning_status,
                args=(host, port, expected_devices, status_ref, stop_event, done_event),
                daemon=True,
            )
            poll_thread.start()

        LOG.info(
            "Monitoring DHCP + ZTP logs%s (Ctrl+C to stop)...\n",
            f" — waiting for {expected_devices}/{expected_devices} Provisioned"
            if expected_devices
            else "",
        )
        procs: list[subprocess.Popen[str]] = []
        try:
            dhcp_proc = subprocess.Popen(
                [*ssh_base, dhcp_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            procs.append(dhcp_proc)
            ztp_proc = subprocess.Popen(
                [*ssh_base, ztp_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            procs.append(ztp_proc)

            fds = {
                dhcp_proc.stdout: "DHCP",
                ztp_proc.stdout: "ZTP",
            }
            last_prov = -1
            while fds:
                if done_event.is_set():
                    break
                if outer_stop is not None and outer_stop.is_set():
                    break
                readable, _, _ = select.select(list(fds.keys()), [], [], 1.0)
                for fd in readable:
                    line = fd.readline()  # type: ignore[union-attr]
                    if not line:
                        fds.pop(fd)
                        continue
                    label = fds[fd]
                    LOG.info("[%s] %s", label, line.rstrip())
                prov = status_ref["provisioned"]
                if prov != last_prov and expected_devices:
                    LOG.info(
                        ">>> Provisioning progress: %d/%d devices Provisioned",
                        prov,
                        expected_devices,
                    )
                    detail = status_ref.get("detail", "")
                    if detail:
                        LOG.info("    Waiting on: %s", detail)
                    last_prov = prov
        except KeyboardInterrupt:
            LOG.info("\nMonitoring stopped.")
        finally:
            stop_event.set()
            for p in procs:
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=5)

    def wait_for_cloud_init(
        self,
        host: str,
        port: int,
        timeout: int = 1800,
    ) -> bool:
        """Wait for SSH and cloud-init to finish completely.

        Phase 1: poll SSH until reachable (DSX Air auto-configures eth0 DHCP).
        Phase 2: poll ``cloud-init status`` until it reports ``done``.
        This ensures the full setup script (Kind, repo clones, topology
        copy, etc.) has completed before deployment is attempted.

        Args:
            host: SSH hostname (from DSX Air service).
            port: SSH port (from DSX Air service).
            timeout: Max seconds to wait (default 30 min). 0 = skip.

        Returns:
            True if setup completed, False if timed out or errored.
        """
        if timeout <= 0:
            return False

        deadline = time.monotonic() + timeout
        ssh_base = self._ssh_cmd(host, port)

        # -- Phase 1: wait for SSH ----------------------------------------
        LOG.info(f"Waiting for SSH to become reachable on {host}:{port}...")
        start = time.monotonic()
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    [*ssh_base, "true"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    elapsed = int(time.monotonic() - start)
                    LOG.info(f"SSH is reachable (after {elapsed}s)")
                    break
            except subprocess.TimeoutExpired:
                pass

            elapsed = int(time.monotonic() - start)
            if elapsed % 30 < 10:
                LOG.info(f"  [{elapsed}s] Still waiting for SSH...")
            time.sleep(10)
        else:
            LOG.warning("Timed out waiting for SSH. Log in manually to check status.")
            return False

        if self._remote_setup_marker_exists(ssh_base):
            LOG.info("\nCloud-init setup already finished successfully.")
            return True

        # -- Phase 2: tail cloud-init output --------------------------------
        LOG.info("Tailing cloud-init output ...")
        LOG.info("(Ctrl+C to stop tailing and continue)\n")

        try:
            proc = subprocess.Popen(
                [*ssh_base, "sudo", "tail", "-n", "+1", "-f", "/var/log/cloud-init-output.log"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            assert proc.stdout is not None
            last_status_check = 0.0
            done_seen_at: float | None = None

            while time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                if ready:
                    line = proc.stdout.readline()
                    if line == "":
                        LOG.warning(
                            "Log tail ended unexpectedly. SSH session "
                            "may have dropped -- check the server manually."
                        )
                        self._terminate_process(proc)
                        return False

                    line = line.rstrip("\n")
                    LOG.info("[oob-mgmt-server] %s", line)

                    if self._SETUP_COMPLETE_MARKER in line:
                        LOG.info("\nCloud-init setup finished successfully.")
                        self._terminate_process(proc)
                        return True

                now = time.monotonic()
                if now - last_status_check < 10:
                    continue
                last_status_check = now

                status = subprocess.run(
                    [*ssh_base, "cloud-init", "status", "--long"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                status_text = f"{status.stdout}\n{status.stderr}".strip()
                if "status: error" in status_text or "extended_status: error" in status_text:
                    LOG.warning("Cloud-init reported an error:\n%s", status_text)
                    self._terminate_process(proc)
                    return False

                if "status: done" in status_text:
                    done_seen_at = done_seen_at or now
                    if now - done_seen_at > 5:
                        if self._remote_setup_marker_exists(ssh_base):
                            LOG.info("\nCloud-init setup finished successfully.")
                            self._terminate_process(proc)
                            return True
                        LOG.warning(
                            "Cloud-init completed without the setup-complete marker. "
                            "Check /var/log/nvcm-setup.log on the server."
                        )
                        self._terminate_process(proc)
                        return False

            LOG.warning("\nTimed out waiting for setup to complete. Check the server manually.")
            self._terminate_process(proc)
            return False

        except KeyboardInterrupt:
            LOG.info("\nTailing interrupted. Setup may still be running on the server.")
            self._terminate_process(proc)
            return False

    def start_simulation(self, simulation_id: str, wait: bool = True) -> None:
        """Start a simulation and optionally wait for it to load.

        Args:
            simulation_id: ID of the simulation
            wait: Wait for simulation to be fully loaded
        """
        simulation = self.client.simulations.get(simulation_id)

        if simulation.state not in ["BOOTING", "ACTIVE"]:
            LOG.info(f"Starting simulation {simulation_id}...")
            simulation.start()

        if wait:
            LOG.info("Waiting for simulation to become active...")
            while simulation.state != "ACTIVE":
                LOG.info(f"  State: {simulation.state}")
                time.sleep(10)
                simulation = self.client.simulations.get(simulation_id)

            LOG.info("Simulation is active!")

    def get_nvcm_server_ssh_command(self, simulation_id: str, server_name: str) -> str | None:
        """Get the SSH command to connect to the nvcm server node.

        Args:
            simulation_id: ID of the simulation
            server_name: Name of the server node

        Returns:
            SSH command string, or None if server not found
        """
        for node in self.client.nodes.list(simulation=simulation_id):
            if node.name == server_name:
                for iface in self.client.interfaces.list(node=node):
                    if iface.name == "eth0":
                        services = self.client.services.list(simulation=simulation_id)
                        for service in services:
                            if service.interface.id == iface.id and service.service_type == "SSH":
                                return f"ssh -p {service.worker_port} nvcm@{service.worker_fqdn}"
                break
        return None

    def setup_nvcm_server(
        self,
        simulation_id: str,
        nvcm_config: NVCMServerConfig,
        config_manager_repo: str = DEFAULT_CONFIG_MANAGER_REPO,
        config_manager_ref: str = "main",
    ) -> dict[str, str]:
        """Set up the NVCM server inside the simulation.

        This method:
        1. Creates an SSH service for the nvcm server
        2. Outputs instructions to install prerequisites
        3. Outputs instructions to create Kind cluster and deploy NVCM

        Args:
            simulation_id: ID of the simulation
            nvcm_config: NVCM server configuration
            config_manager_repo: URL to the nv-config-manager git repository
            config_manager_ref: Git branch to use

        Returns:
            Dict with deployment info (hostname, metallb_ips, etc.)
        """
        server_name = nvcm_config.server_name
        LOG.info(f"Setting up NVCM on '{server_name}' inside the simulation...")

        # Find the nvcm server node
        nvcm_node = None
        for node in self.client.nodes.list(simulation=simulation_id):
            if node.name == server_name:
                nvcm_node = node
                break

        if not nvcm_node:
            raise ValueError(f"Node '{server_name}' not found in simulation")

        # Wait for node to be ready
        LOG.info(f"Waiting for {server_name} to be ready...")
        while nvcm_node.state != "RUNNING":
            LOG.info(f"  State: {nvcm_node.state}")
            time.sleep(10)
            nvcm_node.refresh()

        # Create SSH service for the server
        LOG.info(f"Creating SSH service for {server_name}...")
        ssh_service = None
        for iface in self.client.interfaces.list(node=nvcm_node):
            if iface.name == "eth0":
                existing = self.client.services.list(simulation=simulation_id)
                for svc in existing:
                    if svc.interface.id == iface.id and svc.node_port == 22:
                        ssh_service = svc
                        break

                if not ssh_service:
                    ssh_service = self.client.services.create(
                        name=f"{server_name} SSH",
                        interface=iface,
                        node_port=22,
                        service_type="SSH",
                    )
                break

        if not ssh_service:
            raise ValueError(f"Could not create SSH service for {server_name}")

        ssh_host = ssh_service.worker_fqdn
        ssh_port = ssh_service.worker_port
        LOG.info(f"SSH available at: ssh -p {ssh_port} nvcm@{ssh_host}")

        # Wait for SSH to be ready
        LOG.info("Waiting for SSH to be ready...")
        time.sleep(60)  # Give the node time to boot fully

        # Generate the deployment script
        deploy_script = self._generate_nvcm_deploy_script(
            nvcm_config=nvcm_config,
            config_manager_repo=config_manager_repo,
            config_manager_ref=config_manager_ref,
        )

        # The actual deployment would be done via SSH
        # For now, we'll output the commands needed
        LOG.info("\n" + "=" * 70)
        LOG.info("NVCM SERVER SETUP INSTRUCTIONS")
        LOG.info("=" * 70)
        LOG.info("\n1. Connect to the nvcm-server:")
        LOG.info(f"   ssh -p {ssh_port} nvcm@{ssh_host}")
        LOG.info("\n2. Run the setup script:")
        LOG.info(f"   {NVCM_SERVER_SETUP_SCRIPT}")
        LOG.info("\n3. Run the deployment script:")
        LOG.info("   Deployment script content omitted from logs because it contains credentials.")
        LOG.info("=" * 70 + "\n")

        return {
            "ssh_host": ssh_host,
            "ssh_port": str(ssh_port),
            "ssh_command": f"ssh -p {ssh_port} nvcm@{ssh_host}",
            "metallb_range": nvcm_config.metallb_ip_range,
            "nvcm_size": nvcm_config.nvcm_size,
            # Predictable credentials for e2e testing
            "switch_user": NVCM_SECRETS["nvcm_user"],
            "switch_password": NVCM_SECRETS["nvcm_password"],
            "nautobot_user": NVCM_SECRETS["nautobot_superuser"],
            "nautobot_password": NVCM_SECRETS["nautobot_password"],
            "deploy_script": deploy_script,
        }

    def _generate_nvcm_deploy_script(
        self,
        nvcm_config: NVCMServerConfig,
        config_manager_repo: str,
        config_manager_ref: str,
    ) -> str:
        """Generate the NVCM deployment script to run on the server.

        Uses predictable secrets so that Temporal workers know the credentials
        to use when connecting to switches after ZTP.

        Args:
            nvcm_config: NVCM server configuration
            config_manager_repo: Git repo URL
            config_manager_ref: Git branch

        Returns:
            Shell script as a string
        """
        metallb_start, metallb_end = nvcm_config.metallb_ip_range.split("-")
        secrets = NVCM_SECRETS

        script = f"""#!/bin/bash
set -euo pipefail

echo "=== Deploying NVCM inside DSX Air simulation ==="

# Clone the NVIDIA Config Manager repository
if [ ! -d "nv-config-manager" ]; then
    git clone -b {config_manager_ref} {config_manager_repo} nv-config-manager
fi
cd nv-config-manager

# Create Kind cluster with proper config
cat > /tmp/kind-config.yaml << 'EOF'
{NVCM_KIND_CONFIG}
EOF

# Delete existing cluster if present
kind delete cluster --name nvcm || true

# Create new cluster
kind create cluster --name nvcm --config /tmp/kind-config.yaml

# Wait for cluster to be ready
kubectl wait --for=condition=Ready nodes --all --timeout=300s

# Install MetalLB
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.5/config/manifests/metallb-native.yaml

# Wait for MetalLB to be ready
kubectl wait --namespace metallb-system \\
    --for=condition=ready pod \\
    --selector=app=metallb \\
    --timeout=120s

# Configure MetalLB IP pool
cat << 'METALLB_EOF' | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: nvcm-pool
  namespace: metallb-system
spec:
  addresses:
  - {metallb_start}-{metallb_end}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: nvcm-l2
  namespace: metallb-system
spec:
  ipAddressPools:
  - nvcm-pool
METALLB_EOF

# Get IPs from the pool for NVCM services
NVCM_IP=$(echo {metallb_start} | awk -F. '{{print $1"."$2"."$3"."$4+1}}')
ZTP_IP=$(echo {metallb_start} | awk -F. '{{print $1"."$2"."$3"."$4+2}}')
DHCP_IP=$(echo {metallb_start} | awk -F. '{{print $1"."$2"."$3"."$4+3}}')

echo "NVCM will be deployed with:"
echo "  Hostname: nvcm.air.local"
echo "  ZTP IP: $ZTP_IP"
echo "  DHCP IP: $DHCP_IP"

# Create predictable secrets file for e2e testing
# These credentials are used by Temporal workers to connect to switches
cat > /tmp/nvcm-secrets.ini << 'SECRETS_EOF'
[nautobot]
superuser_name = {secrets["nautobot_superuser"]}
superuser_password = {secrets["nautobot_password"]}
superuser_email = admin@nvcm.air.local
secret_key = {secrets["nautobot_secret_key"]}

[database.nautobot]
password = {secrets["nautobot_db_password"]}

[database.temporal]
password = {secrets["temporal_db_password"]}

[database.temporal_visibility]
password = {secrets["temporal_visibility_db_password"]}

[database.config_store]
password = {secrets["config_store_db_password"]}

[database.dhcp]
password = {secrets["dhcp_db_password"]}

[redis]
password = {secrets["redis_password"]}

[temporal]
# Credentials for Temporal workers to use when connecting to switches
# These are the credentials that ZTP will configure on the switches
device_user = {secrets["nvcm_user"]}
device_password = {secrets["nvcm_password"]}
SECRETS_EOF

echo "=== Predictable secrets for e2e testing ==="
echo "Switch credentials after ZTP:"
echo "  User: {secrets["nvcm_user"]}"
echo "  Password: {secrets["nvcm_password"]}"
echo "Nautobot admin:"
echo "  User: {secrets["nautobot_superuser"]}"
echo "  Password: {secrets["nautobot_password"]}"

# Run the NVCM deployment with predictable secrets
./deploy/deploy.sh \\
    --hostname nvcm.air.local \\
    --size {nvcm_config.nvcm_size} \\
    --lb-provider metallb \\
    --ztp-lb-ip $ZTP_IP \\
    --dhcp-lb-ip $DHCP_IP \\
    --secrets-file /tmp/nvcm-secrets.ini \\
    --yes

echo "=== NVCM deployment complete ==="
echo ""
echo "NVCM is now running inside the DSX Air simulation."
echo "Switches will receive configuration via ZTP from $ZTP_IP"
echo ""
echo "To access Nautobot UI, add this to /etc/hosts on nvcm-server:"
echo "  $NVCM_IP nvcm.air.local"
echo ""
echo "Then access: https://nvcm.air.local"
echo "  Username: {secrets["nautobot_superuser"]}"
echo "  Password: {secrets["nautobot_password"]}"
"""
        return script

    def delete_simulation(self, simulation_id: str) -> None:
        """Delete a simulation.

        Args:
            simulation_id: ID of the simulation to delete
        """
        LOG.info(f"Deleting simulation {simulation_id}...")
        self.client.simulations.delete(simulation_id)
        LOG.info("Simulation deleted")
