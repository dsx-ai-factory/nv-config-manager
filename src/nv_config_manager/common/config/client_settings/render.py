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
"""INI settings adapter for Render clients."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.common.config.client_settings.http_service import (
    HTTPServiceClientSettings,
    http_service_client_settings,
)


class RenderClientSettings(HTTPServiceClientSettings):
    """Constructor settings for ``nv_config_manager_clients.RenderClient``."""


def render_client_settings(
    config: ConfigParser | None = None,
    *,
    section: str = "render",
) -> RenderClientSettings:
    """Translate a Render INI section into constructor settings."""
    return http_service_client_settings(config, section=section)
