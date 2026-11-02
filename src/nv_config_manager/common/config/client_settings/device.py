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
"""INI settings adapter for network-device connections."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.common.config.loader import resolve_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.common.secrets import (
    get_credential,
    get_rotation_passwords,
    resolve_config_section,
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)


def device_connection_settings(
    config: ConfigParser | None = None,
    *,
    site: str | None = None,
    username: str | None = None,
    password: str | None = None,
    mock: bool | None = None,
) -> DeviceConnectionSettings:
    """Resolve service configuration and legacy overrides into plain settings.

    Truthy explicit credentials take precedence, matching the legacy service
    constructors. Site-specific secrets affect passwords only; usernames fall
    back to the global device section.
    """
    resolved = resolve_config(config)
    if password:
        passwords = [password]
        logger.debug("Using explicit device password")
    else:
        credential_config, credential_section = resolve_config_section(resolved, "device", site)
        passwords = get_rotation_passwords(credential_config, credential_section)
        if passwords:
            logger.debug("Loaded %d device rotation password(s)", len(passwords))
        else:
            fallback = get_credential(resolved, "device", "password", site)
            passwords = [fallback] if fallback else []
            logger.debug("Using fallback device password (no rotation keys found)")

    return {
        "username": username or resolved["device"]["username"],
        "passwords": passwords,
        "mock": mock if mock is not None else resolved["device"].getboolean("mock", fallback=False),
    }
