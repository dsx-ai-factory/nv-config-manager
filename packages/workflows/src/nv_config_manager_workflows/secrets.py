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
"""Pure credential selection over caller-supplied mappings."""

from __future__ import annotations

from collections.abc import Mapping

from nv_config_manager_logging import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.AUTH)


type CredentialSection = Mapping[str, str]
type CredentialConfig = Mapping[str, CredentialSection]


def get_site_slug(site: str) -> str:
    """Return the legacy site-section slug for ``site``."""
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
            return secrets_config, site_section
    return main_config, section


def get_credential(
    main_config: CredentialConfig,
    secrets_config: CredentialConfig | None,
    section: str,
    key: str,
    site: str | None = None,
    default: str = "",
) -> str:
    """Return one value with site-specific then global fallback semantics."""
    selected, selected_section = select_credential_source(
        main_config,
        secrets_config,
        section,
        site,
    )
    if selected_section in selected:
        value = selected[selected_section].get(key, "")
        if value:
            return value

    if site and selected_section.startswith("site."):
        global_section = main_config[section] if section in main_config else {}
        return global_section.get(key, default)
    return default


def get_rotation_passwords(
    config: CredentialConfig,
    section: str,
    key_prefix: str = "api_user_key_r",
    max_passwords: int = 2,
) -> list[str]:
    """Return numbered rotation values ordered by descending revision."""
    if section not in config:
        return []

    rotations: list[tuple[int, str]] = []
    for key, value in config[section].items():
        if not key.startswith(key_prefix):
            continue
        try:
            revision = int(key[len(key_prefix) :])
        except (ValueError, IndexError):
            logger.debug("Skipping invalid rotation key: %s", key)
            continue
        rotations.append((revision, value))
        logger.debug(
            "Found rotation key: %s (revision %d) in [%s]",
            key,
            revision,
            section,
        )

    rotations.sort(reverse=True, key=lambda item: item[0])
    return [password for _, password in rotations[:max_passwords]]


__all__ = [
    "CredentialConfig",
    "CredentialSection",
    "get_credential",
    "get_rotation_passwords",
    "get_site_slug",
    "select_credential_source",
]
