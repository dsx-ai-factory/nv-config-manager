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
"""Service INI factory and compatibility exports for config_store."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigFile as ConfigFile
from nv_config_manager_clients.config_store import ConfigFileMetadata as ConfigFileMetadata
from nv_config_manager_clients.config_store import ConfigStoreClient as _ConfigStoreClient
from nv_config_manager_clients.config_store import ConfigStoreException as ConfigStoreException
from nv_config_manager_clients.config_store import (
    ConfigStoreFileNotFound as ConfigStoreFileNotFound,
)


class ConfigStoreClient(_ConfigStoreClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        file_type: ConfigStoreType | str = "intended",
        section: str = "config_store.client",
    ) -> ConfigStoreClient:
        """Create ConfigStoreClient from INI configuration.

        Args:
            config: ConfigParser with config_store.client section
            file_type: File type - ConfigStoreType enum or "intended"/"backup" string
            section: Config section name

        Returns:
            Configured ConfigStoreClient instance
        """
        # Avoid circular import: common.config imports these service client factories.
        from nv_config_manager.common.config import (
            get_internal_auth_headers,
            get_mtls_cert_paths,
            parse_verify_param,
        )

        if hasattr(file_type, "value"):
            file_type_str = str(file_type.value)
        else:
            file_type_str = str(file_type)

        config_section = config[section]
        use_internal = config_section.getboolean("use_internal_endpoint", fallback=False)
        ui_url = config_section["ui_url"]

        if use_internal:
            return cls(
                target=config_section["api_service"],
                file_type=file_type_str,
                ui_url=ui_url,
                verify=False,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            return cls(
                target=config_section["api_url"],
                file_type=file_type_str,
                ui_url=ui_url,
                verify=parse_verify_param(config_section),
                client_certificate=get_mtls_cert_paths(config),
            )
