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
"""INI adapter for render clients."""

from __future__ import annotations

from collections.abc import Callable
from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config import get_internal_auth_headers, get_mtls_cert_paths
from nv_config_manager.temporal.factories._config import resolve_config

type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class RenderClientSettings(TypedDict):
    """Constructor settings for the render client."""

    base_url: str
    client_certificate: tuple[str, str] | None
    headers: HeaderProvider


def render_client_settings(
    config: ConfigParser | None = None,
    *,
    section: str = "render",
) -> RenderClientSettings:
    """Translate the render INI section into constructor settings."""
    resolved = resolve_config(config)
    render_config = resolved[section]
    if render_config.getboolean("use_internal_endpoint", fallback=False):
        return {
            "base_url": render_config["api_service"],
            "client_certificate": None,
            "headers": get_internal_auth_headers,
        }
    return {
        "base_url": render_config["api_url"],
        "client_certificate": get_mtls_cert_paths(resolved),
        "headers": None,
    }
