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
"""INI adapter for Redfish connections."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from configparser import ConfigParser
from functools import partial
from typing import Literal, cast

from temporalio.exceptions import ApplicationError

from nv_config_manager.common.config_loader import load_config, resolve_section
from nv_config_manager_workflows.clients.redfish import (
    RedfishClientSettings,
    RedfishConnection,
    RedfishHost,
    RedfishVendor,
)
from nv_config_manager_workflows.clients.redfish import (
    get_config_manager_connection as create_config_manager_connection,
)
from nv_config_manager_workflows.clients.redfish import (
    get_default_connection as create_default_connection,
)

type RedfishCredentialKind = Literal["default", "config_manager"]
type RedfishCredentials = Mapping[str, str]
type BmcCredentials = Mapping[str, RedfishCredentials]

logger = logging.getLogger(__name__)

# Vendors mapped to None have no INI fallback; the caller must supply host credentials.
_INI_PREFIXES: Mapping[RedfishVendor, str | None] = {
    RedfishVendor.LENOVO: "lenovo",
    RedfishVendor.BLUEFIELD: "bluefield",
    RedfishVendor.DELL: None,
}


def redfish_client_settings(
    *,
    vendor: RedfishVendor,
    config: ConfigParser,
    credentials: RedfishCredentials | None = None,
    credential_kind: RedfishCredentialKind = "default",
) -> RedfishClientSettings:
    """Select host-specific or INI fallback credentials for a Redfish vendor."""
    try:
        prefix = _INI_PREFIXES[vendor]
    except KeyError as exc:
        raise NotImplementedError(f"No Redfish connection implemented for vendor {vendor}") from exc

    use_config_manager_password = (
        credential_kind == "config_manager" and vendor != RedfishVendor.DELL
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

    # Rotation reads the current INI value, independently of the login credentials.
    managed_password = partial(_rotation_password, prefix) if prefix is not None else password

    return {
        "username": username,
        "password": password,
        "config_manager_password": managed_password,
    }


def _rotation_password(prefix: str) -> str:
    """Read the vendor rotation target at the time of the password change."""
    return resolve_section("redfish")[f"{prefix}_config_manager_password"]


def get_bmc_creds() -> dict[str, dict[str, str]]:
    """Read the host-specific BMC credentials owned by the service."""
    path = os.environ.get("BMC_CREDS_PATH", "/etc/vault/bmc-creds.json")
    with open(path, encoding="utf-8") as credentials_file:
        return cast(dict[str, dict[str, str]], json.load(credentials_file))


def _host_credentials(
    host: RedfishHost,
    bmc_credentials: BmcCredentials | None,
) -> RedfishCredentials | None:
    credentials = get_bmc_creds() if bmc_credentials is None else bmc_credentials
    return credentials.get(host.mac) if host.mac is not None else None


def _connection_settings(
    host: RedfishHost,
    credential_kind: RedfishCredentialKind,
    bmc_credentials: BmcCredentials | None,
) -> RedfishClientSettings:
    if host.vendor not in _INI_PREFIXES:
        raise NotImplementedError(f"No Redfish connection implemented for vendor {host.vendor}")

    config = load_config()
    credentials = _host_credentials(host, bmc_credentials)

    if not credentials:
        if host.vendor == RedfishVendor.DELL:
            raise ApplicationError(f"No password found for host {host}")

        credential_label = (
            "NVIDIA Config Manager" if credential_kind == "config_manager" else "default"
        )
        logger.info(
            "No %s creds found for redfish host %s, trying fallback values",
            credential_label,
            host,
        )

    return redfish_client_settings(
        vendor=host.vendor,
        config=config,
        credentials=credentials,
        credential_kind=credential_kind,
    )


def get_default_connection(
    host: RedfishHost,
    *,
    bmc_credentials: BmcCredentials | None = None,
) -> RedfishConnection:
    """Create a vendor connection using host-specific or fallback default credentials."""
    settings = _connection_settings(host, "default", bmc_credentials)
    return create_default_connection(host, settings)


def get_config_manager_connection(
    host: RedfishHost,
    *,
    bmc_credentials: BmcCredentials | None = None,
) -> RedfishConnection:
    """Create a vendor connection using host-specific or fallback managed credentials."""
    settings = _connection_settings(host, "config_manager", bmc_credentials)
    return create_config_manager_connection(host, settings)
