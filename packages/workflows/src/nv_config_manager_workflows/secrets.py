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

from nv_config_manager_workflows.log import AUTH_LOG_CATEGORY, get_logger

type CredentialSection = Mapping[str, str]
type CredentialConfig = Mapping[str, CredentialSection]

logger = get_logger(__name__, category=AUTH_LOG_CATEGORY)


def get_site_slug(site: str) -> str:
    """Convert a site name to the slug used by site-specific secret sections."""
    return site.lower().replace(" ", "-")


def select_credential_source[T: CredentialConfig](
    main_config: T,
    secrets_config: T | None,
    section: str,
    site: str | None = None,
) -> tuple[T, str]:
    """Select a site-specific secrets section or the global main section."""
    if site and secrets_config:
        site_section = f"site.{get_site_slug(site)}"
        if site_section in secrets_config:
            logger.debug("Using site-specific secrets config section: [%s]", site_section)
            return secrets_config, site_section

    # Fallback: main config section
    logger.debug("Using global [%s] section from main config", section)
    return main_config, section


def get_credential(
    main_config: CredentialConfig,
    secrets_config: CredentialConfig | None,
    section: str,
    key: str,
    site: str | None = None,
    default: str = "",
) -> str:
    """Return one credential with site-specific then global fallback semantics."""
    config, resolved_section = select_credential_source(
        main_config,
        secrets_config,
        section,
        site,
    )
    if resolved_section in config:
        value = config[resolved_section].get(key, "")
        if value:
            return value

    # If we got a site-specific section but key wasn't there, try global section
    if site and resolved_section.startswith("site."):
        config, resolved_section = select_credential_source(
            main_config, secrets_config, section, None
        )
        if resolved_section in config:
            return config[resolved_section].get(key, default)

    return default


def get_rotation_passwords(
    config: CredentialConfig,
    section: str,
    key_prefix: str = "api_user_key_r",
    max_passwords: int = 2,
) -> list[str]:
    """Get rotation passwords from a config section.

    Parses keys matching the pattern {key_prefix}{revision_number} and returns
    the passwords sorted by revision number (newest first).

    Args:
        config: Configuration object
        section: The config section to read from
        key_prefix: Prefix for rotation keys (default: "api_user_key_r")
        max_passwords: Maximum number of passwords to return (default: 2)

    Returns:
        List of passwords sorted by revision (newest first), up to max_passwords
    """
    if section not in config:
        return []

    rotations: list[tuple[int, str]] = []
    for key, value in config[section].items():
        if not key.startswith(key_prefix):
            continue
        try:
            revision = int(key[len(key_prefix) :])
            logger.debug("Found rotation key: %s (revision %d) in [%s]", key, revision, section)
            rotations.append((revision, value))
        except (ValueError, IndexError):
            logger.debug("Skipping invalid rotation key: %s", key)

    # Sort by revision number (highest first = most recent)
    rotations.sort(reverse=True, key=lambda item: item[0])
    return [password for _, password in rotations[:max_passwords]]
