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
"""Backward-compatible adapter for the relocated Render client."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.common.http_config import get_internal_auth_headers, get_mtls_cert_paths
from nv_config_manager_workflows.clients.render import (
    FileCommit,
    RenderClientException,
)
from nv_config_manager_workflows.clients.render import RenderClient as BaseRenderClient


class RenderClient(BaseRenderClient):
    """Legacy client surface with configuration-based construction."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        section: str = "render",
    ) -> RenderClient:
        """Create a client from the legacy INI configuration."""
        render_config = config[section]
        if render_config.getboolean("use_internal_endpoint", fallback=False):
            return cls(
                base_url=render_config["api_service"],
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        return cls(
            base_url=render_config["api_url"],
            client_certificate=get_mtls_cert_paths(config),
        )


__all__ = ["FileCommit", "RenderClient", "RenderClientException"]
