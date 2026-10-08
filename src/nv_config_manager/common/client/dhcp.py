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
"""Service INI factory and compatibility exports for dhcp."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_clients.dhcp import DHCPClient as _DHCPClient
from nv_config_manager_clients.dhcp import DHCPClientException as DHCPClientException


class DHCPClient(_DHCPClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "dhcp",
    ) -> DHCPClient:
        """Create a DHCP client from INI configuration."""
        # Imported lazily because nv_config_manager.common.config imports common clients.
        # Avoid circular import: common.config imports these service client factories.
        from nv_config_manager.common.config import (
            get_internal_auth_headers,
            get_mtls_cert_paths,
            parse_verify_param,
        )

        dhcp_config = config[section]
        use_internal = dhcp_config.getboolean("use_internal_endpoint", fallback=False)
        if use_internal:
            return cls(
                base_url=dhcp_config["api_service"],
                verify=False,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        return cls(
            base_url=dhcp_config["api_url"],
            verify=parse_verify_param(dhcp_config),
            client_certificate=get_mtls_cert_paths(config),
        )
