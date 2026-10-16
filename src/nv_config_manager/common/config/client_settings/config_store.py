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
"""INI settings adapter for Config Store clients."""

from __future__ import annotations

from collections.abc import Callable
from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager_clients import ConfigStoreType

from nv_config_manager.common.config.http import (
    get_internal_auth_headers,
    get_mtls_cert_paths,
    parse_verify_param,
)
from nv_config_manager.common.config.loader import resolve_config

type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class ConfigStoreClientSettings(TypedDict):
    """Constructor settings for ``nv_config_manager_clients.ConfigStoreClient``."""

    target: str
    file_type: str
    ui_url: str
    verify: bool | str
    client_certificate: tuple[str, str] | None
    headers: HeaderProvider


def config_store_client_settings(
    config: ConfigParser | None = None,
    *,
    file_type: ConfigStoreType | str = ConfigStoreType.INTENDED,
    section: str = "config_store.client",
) -> ConfigStoreClientSettings:
    """Translate a Config Store INI section into constructor settings."""
    resolved = resolve_config(config)
    client = resolved[section]
    file_type_value = file_type if isinstance(file_type, str) else file_type.value

    if client.getboolean("use_internal_endpoint", fallback=False):
        return {
            "target": client["api_service"],
            "file_type": file_type_value,
            "ui_url": client["ui_url"],
            "verify": False,
            "client_certificate": None,
            "headers": get_internal_auth_headers,
        }

    return {
        "target": client["api_url"],
        "file_type": file_type_value,
        "ui_url": client["ui_url"],
        "verify": parse_verify_param(client),
        "client_certificate": get_mtls_cert_paths(resolved),
        "headers": None,
    }
