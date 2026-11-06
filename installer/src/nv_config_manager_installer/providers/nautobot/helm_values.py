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
"""Helm values for the bundled Nautobot stack."""

from __future__ import annotations

from typing import Any

from nv_config_manager_installer.helm_values import cnpg_cluster_values
from nv_config_manager_installer.providers.nautobot.settings import (
    JOBS_PVC_NAME,
    NautobotSettings,
)
from nv_config_manager_installer.schema import NVConfigManagerInstallConfig

LOCAL_SERVER = "http://nautobot-nv-config-manager"


def _build_nautobot(config: NVConfigManagerInstallConfig, settings: NautobotSettings) -> dict:
    """Build the ``nautobot`` section."""
    section: dict[str, Any] = {"enabled": config.dcim.local}
    if not config.dcim.local:
        return section
    section.update(
        {
            "admin": {"username": "admin", "email": "admin@example.com"},
            "server": {"db": {"host": "cluster-nautobot-rw", "port": 5432, "name": "nautobot"}},
            "celery": {"enabled": True},
            "nginx": {"enabled": True},
            "initJob": {"enabled": True, "singleInit": True},
            "persistence": {"staticFiles": {"enabled": True, "size": "1Gi"}},
        }
    )
    if settings.jobs:
        jobs_config = settings.jobs_config
        section["customJobs"] = {
            "enabled": True,
            "createPvc": False,
            "pvcName": JOBS_PVC_NAME,
            "accessMode": jobs_config.access_mode or "ReadWriteOnce",
        }
        if jobs_config.storage_class:
            section["customJobs"]["storageClass"] = jobs_config.storage_class
        if jobs_config.node_selector:
            section["customJobs"]["nodeSelector"] = dict(jobs_config.node_selector)
    return section


def build_values(config: NVConfigManagerInstallConfig, settings: NautobotSettings) -> dict:
    """Return the Nautobot values merged over the installer-generated values."""
    local = config.dcim.local
    external: dict[str, Any] = (
        {"local": True, "localServer": LOCAL_SERVER}
        if local
        else {"local": False, "server": config.dcim.server}
    )
    values: dict[str, Any] = {
        "nautobot": _build_nautobot(config, settings),
        "externalServices": {"nautobot": external},
        "cnpg": {"nautobot": cnpg_cluster_values(config, local)},
    }
    pg = config.external_services.postgres
    if pg.enabled and pg.dcim_host:
        values["externalServices"]["postgres"] = {
            "nautobot": {"host": pg.dcim_host, "database": "nautobot"}
        }
    return values
