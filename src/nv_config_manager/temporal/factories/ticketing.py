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
"""INI adapter for ticketing clients."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config_loader import resolve_config
from nv_config_manager.temporal.common.secrets import get_credential


class TicketingClientSettings(TypedDict):
    """Constructor settings for the selected ticketing provider."""

    base_url: str
    api_token: str


def ticketing_client_settings(
    config: ConfigParser | None = None,
    *,
    platform: str,
    site: str | None = None,
) -> TicketingClientSettings:
    """Translate a supported ticketing section into constructor settings."""
    if platform != "jira":
        raise ValueError(f"Unknown ticketing platform: {platform!r}")
    resolved = resolve_config(config)
    return {
        "base_url": get_credential(resolved, platform, "base_url", site),
        "api_token": get_credential(resolved, platform, "api_token", site),
    }
