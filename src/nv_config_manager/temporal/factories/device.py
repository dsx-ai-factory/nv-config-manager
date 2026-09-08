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
"""INI adapter for network-device connections."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.temporal.common.secrets import (
    get_credential,
    get_rotation_passwords,
    resolve_config_section,
)
from nv_config_manager.temporal.factories._config import resolve_config


class DeviceConnectionSettings(TypedDict):
    """Configuration values consumed by the network-connection hierarchy."""

    username: str
    passwords: list[str]
    mock: bool


def device_connection_settings(
    config: ConfigParser | None = None,
    *,
    site: str | None = None,
) -> DeviceConnectionSettings:
    """Translate device and site-specific credentials into plain settings."""
    resolved = resolve_config(config)
    password_config, password_section = resolve_config_section(resolved, "device", site)
    passwords = get_rotation_passwords(password_config, password_section)
    if not passwords:
        fallback = get_credential(resolved, "device", "password", site)
        passwords = [fallback] if fallback else []

    return {
        "username": resolved["device"]["username"],
        "passwords": passwords,
        "mock": resolved["device"].getboolean("mock", fallback=False),
    }
