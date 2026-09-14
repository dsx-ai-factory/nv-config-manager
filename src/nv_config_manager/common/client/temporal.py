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
"""Service INI factory and compatibility exports for temporal."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_clients.temporal import TemporalClient as _TemporalClient
from nv_config_manager_clients.temporal import TemporalClientException as TemporalClientException


class TemporalClient(_TemporalClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "temporal",
        user_domain_section: str | None = None,
        user_domain_key: str = "user_domain",
    ) -> TemporalClient:
        """Create TemporalClient from INI configuration.

        Args:
            config: ConfigParser with temporal section
            section: Config section name for temporal settings
            user_domain_section: Section to get user_domain from (defaults to same as section)
            user_domain_key: Key for user_domain value

        Returns:
            Configured TemporalClient instance
        """
        # Avoid circular import: common.config imports these service client factories.
        from nv_config_manager.common.config import get_internal_auth_headers, get_mtls_cert_paths

        temporal_config = config[section]
        use_internal = temporal_config.getboolean("use_internal_endpoint", fallback=False)

        # Get user_domain from specified section or temporal section
        domain_section = user_domain_section or section
        if domain_section in config:
            user_domain = config[domain_section].get(user_domain_key, "nvidia.com")
        else:
            user_domain = "nvidia.com"

        if use_internal:
            return cls(
                base_url=temporal_config["api_service"],
                user_domain=user_domain,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            return cls(
                base_url=temporal_config["api_url"],
                user_domain=user_domain,
                client_certificate=get_mtls_cert_paths(config),
            )
