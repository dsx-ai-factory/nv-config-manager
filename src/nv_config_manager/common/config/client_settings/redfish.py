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
from collections.abc import Callable, Mapping
from configparser import ConfigParser
from functools import partial
from typing import Literal, TypedDict, cast

from temporalio.exceptions import ApplicationError

from nv_config_manager.common.config.loader import resolve_config, resolve_section
from nv_config_manager_workflows.clients.redfish.models import RedfishVendor

type RedfishCredentialKind = Literal["default", "config_manager"]
type RedfishCredentials = Mapping[str, str]
type BmcCredentials = Mapping[str, RedfishCredentials]

_INI_PREFIXES: Mapping[RedfishVendor, str | None] = {
    RedfishVendor.LENOVO: "lenovo",
    RedfishVendor.BLUEFIELD: "bluefield",
    RedfishVendor.DELL: None,
}

_VENDOR_ALIASES: Mapping[str, RedfishVendor] = {
    "lenovo": RedfishVendor.LENOVO,
    "nvidia": RedfishVendor.BLUEFIELD,
    "bluefield": RedfishVendor.BLUEFIELD,
    "dell": RedfishVendor.DELL,
}


class RedfishClientSettings(TypedDict):
    """Resolved login and managed-password settings for a Redfish client."""

    username: str
    password: str
    config_manager_password: Callable[[], str]


def get_bmc_credentials() -> dict[str, dict[str, str]]:
    """Read the host-specific BMC credential mapping owned by the service."""
    path = os.environ.get("BMC_CREDS_PATH", "/etc/vault/bmc-creds.json")
    with open(path, encoding="utf-8") as credentials_file:
        return cast(dict[str, dict[str, str]], json.load(credentials_file))


def _rotation_password(prefix: str, config_loader: Callable[[], ConfigParser]) -> str:
    """Read the current vendor password used for credential rotation."""
    return config_loader()["redfish"][f"{prefix}_config_manager_password"]


def redfish_client_settings(
    config: ConfigParser | None = None,
    *,
    vendor: RedfishVendor | str,
    mac: str | None,
    credential_kind: RedfishCredentialKind = "default",
    bmc_credentials: BmcCredentials | None = None,
    config_loader: Callable[[], ConfigParser] | None = None,
) -> RedfishClientSettings:
    """Resolve host-specific or INI fallback credentials for a Redfish vendor."""
    try:
        normalized_vendor = _VENDOR_ALIASES[vendor.lower()]
    except KeyError as exc:
        raise NotImplementedError(f"No Redfish settings implemented for vendor {vendor!r}") from exc
    prefix = _INI_PREFIXES[normalized_vendor]
    if credential_kind not in ("default", "config_manager"):
        raise ValueError(f"Unknown Redfish credential kind: {credential_kind!r}")

    credentials_by_mac = get_bmc_credentials() if bmc_credentials is None else bmc_credentials
    credentials = credentials_by_mac.get(mac) if mac is not None else None
    use_config_manager_password = (
        credential_kind == "config_manager" and normalized_vendor != RedfishVendor.DELL
    )
    password_key = "config_manager_password" if use_config_manager_password else "default_password"

    if credentials:
        username = credentials["default_user"]
        password = credentials[password_key]
    else:
        if prefix is None:
            raise ApplicationError(f"{vendor} Redfish credentials require a host-specific mapping")
        redfish = resolve_section("redfish", config)
        username = redfish[f"{prefix}_default_user"]
        password = redfish[f"{prefix}_{password_key}"]

    rotation_config_loader = config_loader or partial(resolve_config, config)
    managed_password = (
        partial(_rotation_password, prefix, rotation_config_loader)
        if prefix is not None
        else lambda: password
    )

    return {
        "username": username,
        "password": password,
        "config_manager_password": managed_password,
    }
