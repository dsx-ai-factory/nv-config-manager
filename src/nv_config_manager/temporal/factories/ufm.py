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
"""INI adapter for UFM clients."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config_loader import resolve_config
from nv_config_manager.temporal.common.secrets import (
    get_rotation_passwords,
    resolve_credential_source,
)


class UFMClientSettings(TypedDict):
    """Credential values consumed by a UFM client."""

    username: str
    passwords: list[str]


def ufm_client_settings(
    config: ConfigParser | None = None,
    *,
    site: str | None = None,
    max_passwords: int = 2,
) -> UFMClientSettings:
    """Translate UFM and site-specific credentials into plain settings."""
    resolved = resolve_config(config)
    credential_config, section = resolve_credential_source(resolved, "ufm", site)
    username = (
        credential_config[section].get("ufm_api_user", "")
        if credential_config.has_section(section)
        else ""
    )
    passwords = get_rotation_passwords(
        credential_config,
        section,
        key_prefix="ufm_api_token_r",
        max_passwords=max_passwords,
    )
    return {"username": username, "passwords": passwords}
