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
"""INI settings adapter for UFM clients."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config.loader import resolve_config
from nv_config_manager.temporal.common.secrets import get_rotation_passwords, resolve_config_section


class UFMClientSettings(TypedDict):
    """Resolved authentication settings for a UFM client."""

    username: str
    passwords: list[str]


def ufm_client_settings(
    config: ConfigParser | None = None,
    *,
    site: str | None = None,
    max_passwords: int = 2,
) -> UFMClientSettings:
    """Resolve a whole UFM credential section and its token rotations."""
    resolved = resolve_config(config)
    credential_config, credential_section = resolve_config_section(resolved, "ufm", site)
    username = (
        credential_config[credential_section].get("ufm_api_user", "")
        if credential_config.has_section(credential_section)
        else ""
    )
    passwords = get_rotation_passwords(
        credential_config,
        credential_section,
        key_prefix="ufm_api_token_r",
        max_passwords=max_passwords,
    )
    return {"username": username, "passwords": passwords}
