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
"""Service-owned settings and credential-file adapter for Redfish clients."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from configparser import ConfigParser
from typing import Literal, TypedDict, cast

from nv_config_manager.common.config.loader import resolve_config

type RedfishCredentialKind = Literal["default", "config_manager"]
type RedfishCredentials = Mapping[str, str]
type BmcCredentials = Mapping[str, RedfishCredentials]

_INI_PREFIXES: Mapping[str, str | None] = {
    "lenovo": "lenovo",
    "nvidia": "bluefield",
    "bluefield": "bluefield",
    "dell": None,
}


class RedfishClientSettings(TypedDict):
    """Resolved login and managed-password settings for a Redfish client."""

    username: str
    password: str
    config_manager_password: str


def get_bmc_credentials() -> dict[str, dict[str, str]]:
    """Read the host-specific BMC credential mapping owned by the service."""
    path = os.environ.get("BMC_CREDS_PATH", "/etc/vault/bmc-creds.json")
    with open(path, encoding="utf-8") as credentials_file:
        return cast(dict[str, dict[str, str]], json.load(credentials_file))


def redfish_client_settings(
    config: ConfigParser | None = None,
    *,
    vendor: str,
    mac: str | None,
    credential_kind: RedfishCredentialKind = "default",
    bmc_credentials: BmcCredentials | None = None,
) -> RedfishClientSettings:
    """Resolve host-specific or INI fallback credentials for a Redfish vendor."""
    normalized_vendor = vendor.lower()
    if normalized_vendor not in _INI_PREFIXES:
        raise NotImplementedError(f"No Redfish settings implemented for vendor {vendor!r}")
    if credential_kind not in ("default", "config_manager"):
        raise ValueError(f"Unknown Redfish credential kind: {credential_kind!r}")

    credentials = get_bmc_credentials() if bmc_credentials is None else bmc_credentials
    host_credentials = credentials.get(mac) if mac is not None else None
    prefix = _INI_PREFIXES[normalized_vendor]

    if host_credentials:
        username = host_credentials["default_user"]
        password_key = (
            "config_manager_password"
            if credential_kind == "config_manager" and prefix is not None
            else "default_password"
        )
        password = host_credentials[password_key]
    else:
        if prefix is None:
            raise ValueError(f"{vendor} Redfish credentials require a host-specific mapping")
        redfish = resolve_config(config)["redfish"]
        username = redfish[f"{prefix}_default_user"]
        password_key = (
            "config_manager_password" if credential_kind == "config_manager" else "default_password"
        )
        password = redfish[f"{prefix}_{password_key}"]

    if prefix is None:
        managed_password = password
    else:
        managed_password = resolve_config(config)["redfish"][f"{prefix}_config_manager_password"]

    return {
        "username": username,
        "password": password,
        "config_manager_password": managed_password,
    }
