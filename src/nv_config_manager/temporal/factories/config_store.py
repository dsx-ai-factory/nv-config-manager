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
"""INI adapter for config-store clients."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.common.config_loader import resolve_config
from nv_config_manager.common.http_config import (
    get_internal_auth_headers,
    get_mtls_cert_paths,
    parse_verify_param,
)
from nv_config_manager_workflows.clients.config_store import (
    ConfigStoreClientSettings,
    ConfigStoreType,
)


def config_store_client_settings(
    config: ConfigParser | None = None,
    *,
    file_type: ConfigStoreType | str = "intended",
    section: str = "config_store.client",
) -> ConfigStoreClientSettings:
    """Translate the config-store INI section into constructor settings."""
    resolved = resolve_config(config)
    config_section = resolved[section]
    file_type_value = ConfigStoreType(file_type)
    ui_url = config_section["ui_url"]
    use_internal = config_section.getboolean("use_internal_endpoint", fallback=False)

    if use_internal:
        return {
            "target": config_section["api_service"],
            "file_type": file_type_value,
            "ui_url": ui_url,
            "verify": False,
            "client_certificate": None,
            "headers": get_internal_auth_headers,
        }
    return {
        "target": config_section["api_url"],
        "file_type": file_type_value,
        "ui_url": ui_url,
        "verify": parse_verify_param(config_section),
        "client_certificate": get_mtls_cert_paths(resolved),
        "headers": None,
    }
