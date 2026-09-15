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
"""Generate nv-config-manager-install.yaml from DSX Air sim config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from nv_config_manager_installer.air_sim.constants import (
    CONFIG_MANAGER_HOSTNAME,
    CONFIG_MANAGER_INSTALL_CONFIG,
    CONFIG_MANAGER_KIND_CLUSTER,
    CONFIG_MANAGER_NAMESPACE,
    CONFIG_MANAGER_RELEASE,
    CONFIG_MANAGER_REMOTE_DIR,
    DEFAULT_AIR_DEMO_TEMPLATE_PLUGIN_PATH,
    NVCM_BOX_USER,
    NVCM_NETWORK_SECRETS,
    NVCM_SECRETS,
    PROJECT_ROOT,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig

_NETWORK_SECRET_KEYS = [
    ("root_password", "Switch root / cumulus user password"),
    ("api_user_key", "NVUE REST API key"),
    ("bgp_password", "BGP MD5 authentication password"),
    ("isis_password", "IS-IS authentication password"),
    ("tacacs_key", "TACACS+ shared key"),
]

_MOCK_TOPOLOGY_JOB = "custom.mock_topology.jobs.mock_topology_design.MockTopologyDesign"
_DEMO_TEMPLATE_BLUEPRINTS = {"air_trial", "air_superpod"}


def _remote_repo_path(path: str) -> str:
    """Map repo-local paths on the workstation to paths in the DSX Air server clone."""
    if not path:
        return path
    path_obj = Path(path).expanduser()
    if path_obj.is_absolute():
        try:
            rel = path_obj.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            return str(path_obj)
        return f"{CONFIG_MANAGER_REMOTE_DIR}/{rel.as_posix()}"
    return f"{CONFIG_MANAGER_REMOTE_DIR}/{path_obj.as_posix()}"


def _normalize_post_deploy_job(job_spec: dict[str, Any]) -> dict[str, str]:
    job = str(job_spec.get("job", "")).strip()
    raw_input = job_spec.get("input", "")
    if isinstance(raw_input, str):
        job_input = raw_input
    else:
        job_input = json.dumps(raw_input)
    return {"job": job, "input": job_input}


def build_content_jobs(cfg: SimConfig) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return installer content.jobs and content.run_after_deploy entries."""
    jobs: list[dict[str, str]] = []
    run_after_deploy: list[dict[str, str]] = []

    if cfg.run_mock_topology_job:
        jobs.append(
            {"path": _remote_repo_path(cfg.mock_topology_path or "development/mock_topology")}
        )
        run_after_deploy.append(
            {
                "job": _MOCK_TOPOLOGY_JOB,
                "input": json.dumps(
                    {"blueprint": cfg.mock_blueprint, "deployment_name": cfg.deployment_name}
                ),
            }
        )

    for path in cfg.extra_job_paths:
        if path:
            jobs.append({"path": _remote_repo_path(path)})

    for job_spec in cfg.extra_run_after_deploy:
        normalized = _normalize_post_deploy_job(job_spec)
        if normalized["job"]:
            run_after_deploy.append(normalized)

    return jobs, run_after_deploy


def build_template_plugins(cfg: SimConfig) -> list[dict[str, str]]:
    """Return installer content.template_plugins entries."""
    paths = [path for path in cfg.template_plugin_paths if path]

    if cfg.mock_blueprint in _DEMO_TEMPLATE_BLUEPRINTS:
        default_path = DEFAULT_AIR_DEMO_TEMPLATE_PLUGIN_PATH.as_posix()
        default_remote_path = _remote_repo_path(default_path)
        if all(_remote_repo_path(path) != default_remote_path for path in paths):
            paths.append(default_path)

    return [{"path": _remote_repo_path(path)} for path in paths]


def generate_air_sim_install_config(
    cfg: SimConfig,
    site_name: str,
    lb_allowed_prefixes: list[str],
) -> dict[str, Any]:
    """Build the installer config structure for NVIDIA Config Manager."""
    content_jobs, run_after_deploy = build_content_jobs(cfg)
    template_plugins = build_template_plugins(cfg)

    network_secrets = [
        {
            "name": description,
            "secret_key": key,
            "source": "manual",
            "value": NVCM_NETWORK_SECRETS[key],
            "rotation": "r1",
        }
        for key, description in _NETWORK_SECRET_KEYS
    ]

    return {
        "version": "1",
        "cluster": {
            "hostname": CONFIG_MANAGER_HOSTNAME,
            "environment": "air-sim",
            "namespace": CONFIG_MANAGER_NAMESPACE,
            "release_name": CONFIG_MANAGER_RELEASE,
            "mock_devices": False,
            "size": cfg.size,
        },
        "secrets": {
            "method": "kubernetes",
            "config_manager_service_username": NVCM_SECRETS["nvcm_user"],
        },
        "network_secrets": network_secrets,
        "git_tokens": [],
        "sites": [{"name": site_name}],
        "sso": {"enabled": False},
        "spiffe": {"enabled": False},
        "services": {
            "render": True,
            "ztp": True,
            "dhcp": True,
            "temporal": True,
            "config_store": True,
            "nautobot": True,
        },
        "content": {
            "jobs": content_jobs,
            "template_plugins": template_plugins,
            "run_after_deploy": run_after_deploy,
        },
        "infrastructure": {
            "gateway": "envoyGateway",
            "tls": True,
            "load_balancer": {
                "provider": "metallb",
                "ztp_lb_ip": "172.18.255.201",
                "dhcp_lb_ip": "172.18.255.202",
                "allowed_prefixes": lb_allowed_prefixes,
            },
            "ztp_storage": {"type": "file", "pvc_size": "10Gi"},
        },
        "images": {"source": "local"},
        "rbac": {
            "admin_roles": ["all"],
            "default_read_roles": ["all"],
            "default_execute_roles": ["all"],
        },
        "redfish": {"enabled": False},
    }


def generate_air_sim_install_yaml(
    cfg: SimConfig,
    site_name: str,
    lb_allowed_prefixes: list[str],
) -> str:
    """Return nv-config-manager-install.yaml as YAML."""
    data = generate_air_sim_install_config(cfg, site_name, lb_allowed_prefixes)
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def build_deploy_command(_cfg: SimConfig) -> str:
    """Return the remote command that runs nv-config-manager-installer deploy."""
    user = NVCM_BOX_USER
    kube = f"KUBECONFIG=/home/{user}/.kube/config"
    config_path = f"/home/{user}/{CONFIG_MANAGER_INSTALL_CONFIG}"

    return (
        f"sudo NO_COLOR=1 {kube} uv run"
        f" --directory {CONFIG_MANAGER_REMOTE_DIR}"
        f" --project {CONFIG_MANAGER_REMOTE_DIR}/installer"
        f" nv-config-manager-installer deploy {config_path}"
        f" --chart-dir {CONFIG_MANAGER_REMOTE_DIR}/deploy/helm"
        f" --kind-cluster {CONFIG_MANAGER_KIND_CLUSTER}"
        f" --install-envoy-gateway --install-cert-manager --install-cnpg-operator"
        f" --image-source local --build-images --load-kind"
    )
