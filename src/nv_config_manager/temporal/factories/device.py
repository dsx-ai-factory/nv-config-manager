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

from nv_config_manager.common.config_loader import resolve_config
from nv_config_manager.temporal.common.secrets import (
    get_credential,
    get_rotation_passwords,
    resolve_credential_source,
)
from nv_config_manager_workflows.clients.device import DeviceConnectionSettings


def device_connection_settings(
    config: ConfigParser | None = None,
    *,
    site: str | None = None,
    username: str | None = None,
    password: str | None = None,
    mock: bool | None = None,
) -> DeviceConnectionSettings:
    """Translate service configuration and legacy overrides into plain settings.

    Truthy explicit credentials take precedence, matching NetworkConnection.
    Site-specific secrets affect passwords only; username falls back to the
    main device section. Secret-file loading remains owned by the service.
    An explicit mock flag lets direct constructors skip factory-only INI parsing.
    """
    resolved = resolve_config(config)
    resolved_username = username or resolved["device"]["username"]
    if password:
        passwords = [password]
    else:
        password_config, password_section = resolve_credential_source(resolved, "device", site)
        passwords = get_rotation_passwords(password_config, password_section)
        if not passwords:
            fallback = get_credential(resolved, "device", "password", site)
            passwords = [fallback] if fallback else []

    return {
        "username": resolved_username,
        "passwords": passwords,
        "mock": mock if mock is not None else resolved["device"].getboolean("mock", fallback=False),
    }
