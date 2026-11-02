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
"""Shared INI settings adapter for public HTTP service clients."""

from __future__ import annotations

from collections.abc import Callable
from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config.http import get_internal_auth_headers, get_mtls_cert_paths
from nv_config_manager.common.config.loader import resolve_config

type HeaderProvider = dict[str, str] | Callable[[], dict[str, str]] | None


class HTTPServiceClientSettings(TypedDict):
    """Constructor settings shared by public HTTP service clients."""

    base_url: str
    client_certificate: tuple[str, str] | None
    headers: HeaderProvider
    verify: bool | str


def http_service_client_settings(
    config: ConfigParser | None = None,
    *,
    section: str,
) -> HTTPServiceClientSettings:
    """Translate a standard service INI section into constructor settings."""
    resolved = resolve_config(config)
    service = resolved[section]

    if service.getboolean("use_internal_endpoint", fallback=False):
        return {
            "base_url": service["api_service"],
            "client_certificate": None,
            "headers": get_internal_auth_headers,
            "verify": True,
        }

    return {
        "base_url": service["api_url"],
        "client_certificate": get_mtls_cert_paths(resolved),
        "headers": None,
        "verify": True,
    }
