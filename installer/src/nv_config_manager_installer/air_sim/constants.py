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
"""Constants and configuration for the NVCM DSX Air simulation helper."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


class _BlockStyleDumper(yaml.SafeDumper):
    """YAML dumper that uses block scalar style for multi-line strings."""

    pass


def _block_str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockStyleDumper.add_representer(str, _block_str_representer)

_RELEASE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+")


def _is_release_tag(ref: str) -> bool:
    """Return True if *ref* looks like a release tag or is main."""
    return ref == "main" or bool(_RELEASE_TAG_RE.match(ref))


DEFAULT_AIR_API_URL = "https://api.air-ngc.nvidia.com/api/"
DEFAULT_AIR_INTERNAL_URL = "https://api.air-inside.nvidia.com/api/"
DEFAULT_AIR_FRONTEND_URL = "https://dsx-air.nvidia.com"
DEFAULT_AIR_INTERNAL_FRONTEND_URL = "https://ngc.air-inside.nvidia.com"
DEFAULT_AIR_ORG = ""
DEFAULT_AIR_SIM_CONFIG_PATH = Path.home() / ".nvcm-air-sim.yaml"

DEFAULT_CUMULUS_VERSION = "5.14.0"

DEFAULT_NODE_CPU = 2
DEFAULT_NODE_MEMORY = 4096
DEFAULT_NODE_STORAGE = 10
DEFAULT_SERVER_OS = "generic/ubuntu2404"

NVCM_SERVER_CPU = 16
NVCM_SERVER_MEMORY = 32768
NVCM_SERVER_STORAGE = 100
NVCM_SERVER_OS = "generic/ubuntu2404"
DEFAULT_NVCM_SERVER_NAME = "nvcm-server"

NVCM_BOX_USER = "nvcm"
NVCM_BOX_PASSWORD = "NVCMDemo1!"  # trufflehog:ignore - predictable DSX Air demo/device password
NVCM_BOX_DIR = "/opt/nvcm-box"
DEFAULT_NAUTOBOT_DEMO_USERNAME = "demo"
DEFAULT_NAUTOBOT_DEMO_PASSWORD = "demo"  # trufflehog:ignore - public DSX Air demo user password

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MOCK_TOPOLOGY_PATH = PROJECT_ROOT / "development" / "mock_topology"
DEFAULT_MOCK_CONTEXT_ROOT = DEFAULT_MOCK_TOPOLOGY_PATH / "context"
DEFAULT_AIR_TRIAL_CONFIG = PROJECT_ROOT / "development" / "air_sim" / "configs" / "air_trial.yaml"
DEFAULT_AIR_DEMO_TEMPLATE_PLUGIN_PATH = (
    Path("development") / "air_sim" / "template_plugins" / "superpod-template-plugin"
)

CONFIG_MANAGER_REPO_DIR = "nv-config-manager"
CONFIG_MANAGER_REMOTE_DIR = f"/home/{NVCM_BOX_USER}/{CONFIG_MANAGER_REPO_DIR}"
CONFIG_MANAGER_INSTALL_CONFIG = "nv-config-manager-install.yaml"
CONFIG_MANAGER_NAMESPACE = "nv-config-manager"
CONFIG_MANAGER_RELEASE = "nv-config-manager"
CONFIG_MANAGER_HOSTNAME = "nvcm.air"
CONFIG_MANAGER_KIND_CLUSTER = "nvcm"
CONFIG_MANAGER_COMPONENT_PREFIX = "nv-config-manager"
CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT = f"{CONFIG_MANAGER_COMPONENT_PREFIX}-nautobot"
CONFIG_MANAGER_DHCP_DEPLOYMENT = f"{CONFIG_MANAGER_COMPONENT_PREFIX}-dhcp"
CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT = f"{CONFIG_MANAGER_DHCP_DEPLOYMENT}-refresh"
CONFIG_MANAGER_RENDER_API_DEPLOYMENT = f"{CONFIG_MANAGER_COMPONENT_PREFIX}-render-api"
CONFIG_MANAGER_ZTP_DEPLOYMENT = f"{CONFIG_MANAGER_COMPONENT_PREFIX}-ztp"
CONFIG_MANAGER_TEMPORAL_DEPLOYMENT = f"{CONFIG_MANAGER_COMPONENT_PREFIX}-temporal"
CONFIG_MANAGER_TEMPORAL_FRONTEND_DEPLOYMENT = f"{CONFIG_MANAGER_TEMPORAL_DEPLOYMENT}-frontend"
CONFIG_MANAGER_TEMPORAL_WORKER_DEPLOYMENT = (
    f"{CONFIG_MANAGER_TEMPORAL_DEPLOYMENT}-{CONFIG_MANAGER_COMPONENT_PREFIX}-worker"
)
DEFAULT_CONFIG_MANAGER_REPO = "https://github.com/dsx-ai-factory/nv-config-manager"

AGGRESSIVE_DHCLIENT_CONF = """\
option rfc3442-classless-static-routes code 121 = array of unsigned integer 8;
option cumulus-provision-url code 239 = text;

send host-name = gethostname();
request subnet-mask, broadcast-address, time-offset, routers,
        domain-name, domain-name-servers, domain-search, host-name,
        dhcp6.name-servers, dhcp6.domain-search, dhcp6.fqdn, dhcp6.sntp-servers,
        netbios-name-servers, netbios-scope, interface-mtu,
        rfc3442-classless-static-routes, ntp-servers, cumulus-provision-url;

send dhcp-lease-time 7200;
timeout 30;
retry 30;
reboot 5;
backoff-cutoff 2;
initial-interval 1;
send vendor-class-identifier "cumulus-linux  x86_64";
"""

NVCM_SECRETS = {
    "cumulus_user": "cumulus",
    "cumulus_password": NVCM_BOX_PASSWORD,
    "nvcm_user": "nvConfigManager",
    "nvcm_password": NVCM_BOX_PASSWORD,
    "nautobot_superuser": "admin",
    "nautobot_password": "admin",
    "nautobot_db_password": "nautobot-db-password",
    "temporal_db_password": "temporal-db-password",
    "temporal_visibility_db_password": "temporal-vis-db-password",
    "config_store_db_password": "config-store-db-password",
    "dhcp_db_password": "dhcp-db-password",
    "nautobot_secret_key": "air-sim-secret-key-not-for-production-use-1234567890",
    "redis_password": "redis-password",
}

NVCM_NETWORK_SECRETS = {
    "root_password": NVCM_BOX_PASSWORD,
    "api_user_key": NVCM_BOX_PASSWORD,
    "bgp_password": "NVCMBgp1!",  # trufflehog:ignore - public DSX Air demo BGP password
    "isis_password": "NVCMIsis1!",  # trufflehog:ignore - public DSX Air demo ISIS password
    "tacacs_key": "NVCMTacacs1!",  # trufflehog:ignore - public DSX Air demo TACACS key
}

NVCM_SERVER_SETUP_SCRIPT = """#!/bin/bash
set -euo pipefail
echo "Install prerequisites for the NVCM DSX Air simulation server."
"""

NVCM_KIND_CONFIG = """
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30080
    hostPort: 80
    protocol: TCP
  - containerPort: 30443
    hostPort: 443
    protocol: TCP
networking:
  disableDefaultCNI: false
"""


def find_ssh_pubkey(path: str | None = None) -> str:
    """Locate and read an SSH public key."""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    candidates.extend(
        [Path.home() / ".ssh" / "id_ed25519.pub", Path.home() / ".ssh" / "id_rsa.pub"]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text().strip()
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"No SSH public key found (tried: {tried})")
