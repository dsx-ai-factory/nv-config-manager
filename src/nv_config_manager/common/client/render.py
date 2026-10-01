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
"""Service INI factory and compatibility exports for render."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_clients.render import FileCommit as FileCommit
from nv_config_manager_clients.render import RenderClient as _RenderClient
from nv_config_manager_clients.render import RenderClientException as RenderClientException


class RenderClient(_RenderClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "render",
    ) -> RenderClient:
        """Create RenderClient from INI configuration.

        Args:
            config: ConfigParser with render section
            section: Config section name

        Returns:
            Configured RenderClient instance
        """
        # Avoid circular import: common.config imports these service client factories.
        from nv_config_manager.common.config import get_internal_auth_headers, get_mtls_cert_paths

        render_config = config[section]
        use_internal = render_config.getboolean("use_internal_endpoint", fallback=False)

        if use_internal:
            return cls(
                base_url=render_config["api_service"],
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            return cls(
                base_url=render_config["api_url"],
                client_certificate=get_mtls_cert_paths(config),
            )
