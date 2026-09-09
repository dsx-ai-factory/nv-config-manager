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

from collections.abc import Mapping
from configparser import ConfigParser
from typing import Literal, TypedDict

from nv_config_manager.common.config_loader import resolve_config

type RedfishCredentialKind = Literal["default", "config_manager"]

# Vendors mapped to None have no INI fallback; the caller must supply host credentials.
_INI_PREFIXES: Mapping[str, str | None] = {
    "lenovo": "lenovo",
    "nvidia": "bluefield",
    "bluefield": "bluefield",
    "dell": None,
}


class RedfishClientSettings(TypedDict):
    """Credentials consumed by a Redfish connection."""

    username: str
    password: str
    config_manager_password: str


def redfish_client_settings(
    config: ConfigParser | None = None,
    *,
    vendor: str,
    credentials: Mapping[str, str] | None = None,
    credential_kind: RedfishCredentialKind = "default",
) -> RedfishClientSettings:
    """Select host-specific or INI fallback credentials for a Redfish vendor."""
    normalized_vendor = vendor.lower()
    if normalized_vendor not in _INI_PREFIXES:
        raise NotImplementedError(f"No Redfish settings implemented for vendor {vendor!r}")

    if credentials is None:
        prefix = _INI_PREFIXES[normalized_vendor]
        if prefix is None:
            raise ValueError(f"{vendor} Redfish credentials require a host-specific mapping")
        redfish = resolve_config(config)["redfish"]
        credentials = {
            "default_user": redfish[f"{prefix}_default_user"],
            "default_password": redfish[f"{prefix}_default_password"],
            "config_manager_password": redfish[f"{prefix}_config_manager_password"],
        }

    password = credentials[
        "config_manager_password" if credential_kind == "config_manager" else "default_password"
    ]
    return {
        "username": credentials["default_user"],
        "password": password,
        "config_manager_password": credentials.get("config_manager_password", password),
    }
