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
"""Pure credential resolution over pre-parsed configuration mappings.

File discovery, parsing, caching, and logging belong to the host service. This
module only selects values from mappings supplied by that service.
"""

from __future__ import annotations

from collections.abc import Mapping

type CredentialSection = Mapping[str, str]
type CredentialConfig = Mapping[str, CredentialSection]


def get_site_slug(site: str) -> str:
    """Convert a site name to the slug used by site-specific secret sections."""
    return site.lower().replace(" ", "-")


def resolve_config_section(
    main_config: CredentialConfig,
    secrets_config: CredentialConfig | None,
    section: str,
    site: str | None = None,
) -> tuple[CredentialConfig, str]:
    """Select a site-specific secrets section or the global main section."""
    if site and secrets_config is not None:
        site_section = f"site.{get_site_slug(site)}"
        if site_section in secrets_config:
            return secrets_config, site_section
    return main_config, section


def resolve_credentials(
    main_config: CredentialConfig,
    secrets_config: CredentialConfig | None,
    section: str,
    site: str | None = None,
) -> CredentialSection:
    """Return the selected credential section, or an empty mapping when absent."""
    config, resolved_section = resolve_config_section(
        main_config,
        secrets_config,
        section,
        site,
    )
    return config[resolved_section] if resolved_section in config else {}


def get_credential(
    main_config: CredentialConfig,
    secrets_config: CredentialConfig | None,
    section: str,
    key: str,
    site: str | None = None,
    default: str = "",
) -> str:
    """Return one credential with site-specific then global fallback semantics."""
    config, resolved_section = resolve_config_section(
        main_config,
        secrets_config,
        section,
        site,
    )
    if resolved_section in config:
        value = config[resolved_section].get(key, "")
        if value:
            return value

    if site and resolved_section.startswith("site."):
        global_section: CredentialSection = main_config[section] if section in main_config else {}
        return global_section.get(key, default)
    return default


def get_rotation_passwords(
    config: CredentialConfig,
    section: str,
    key_prefix: str = "api_user_key_r",
    max_passwords: int = 2,
) -> list[str]:
    """Return numbered rotation values ordered from newest to oldest."""
    if section not in config:
        return []

    rotations: list[tuple[int, str]] = []
    for key, value in config[section].items():
        if not key.startswith(key_prefix):
            continue
        try:
            revision = int(key[len(key_prefix) :])
        except ValueError:
            continue
        rotations.append((revision, value))

    rotations.sort(reverse=True, key=lambda item: item[0])
    return [password for _, password in rotations[:max_passwords]]
