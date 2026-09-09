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
"""Secrets configuration loading for Temporal services.

Provides shared logic for loading site-specific secrets from the config-secrets
file with fallback to the main nv-config-manager.ini configuration.

The secrets config file path is specified by the NV_CONFIG_MANAGER_CONFIG_SECRET_PATH environment
variable. This file contains site-specific sections like [site.site-name] with
credentials that can be rotated independently of the main configuration.

Lookup order for credentials:
1. Secrets config: [site.{site_slug}] section (if site provided)
2. Main config: [{section}] section (global fallback)
"""

from __future__ import annotations

import os
from configparser import ConfigParser
from functools import lru_cache

from nv_config_manager.common.ini import FileFingerprint, file_fingerprint
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager_workflows.secrets import (
    CredentialConfig,
    CredentialSection,
    get_site_slug,  # noqa: F401  # re-exported for callers of this module
    select_credential_source,
)
from nv_config_manager_workflows.secrets import (
    get_rotation_passwords as _get_rotation_passwords,
)

logger = get_logger(__name__, category=LogCategory.AUTH)


@lru_cache(maxsize=1)
def _load_secrets_config(
    secrets_path: str | None,
    fingerprint: FileFingerprint | None,
) -> tuple[ConfigParser, bool]:
    """Parse one version of the site-specific secrets INI file."""
    secrets_config = ConfigParser(interpolation=None)

    if secrets_path and fingerprint is not None:
        _ = secrets_config.read(secrets_path)
        logger.debug("Loaded secrets config from: %s", secrets_path)
        return secrets_config, True

    if secrets_path:
        logger.debug("Secrets config path set but file not found: %s", secrets_path)
    return secrets_config, False


def load_secrets_config() -> tuple[ConfigParser, bool]:
    """Load the secrets config file if available.

    Reads from the path specified by NV_CONFIG_MANAGER_CONFIG_SECRET_PATH environment variable.
    The parsed result is reused while the file is unchanged and invalidated
    automatically after direct writes or Kubernetes Secret-volume updates.

    Returns:
        Tuple of (ConfigParser, found) where found indicates if the file exists
    """
    secrets_path = os.environ.get("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH")
    return _load_secrets_config(secrets_path, file_fingerprint(secrets_path))


def clear_secrets_cache() -> None:
    """Clear the secrets config cache.

    Useful for testing or when the secrets file has been updated.
    """
    _load_secrets_config.cache_clear()


def _as_credential_config(config: ConfigParser) -> CredentialConfig:
    """Convert a parsed service configuration into nested plain mappings."""
    section_names: list[str] = config.sections()
    return {section: dict(config[section]) for section in section_names}


def resolve_credential_source(
    main_config: ConfigParser,
    section: str,
    site: str | None = None,
) -> tuple[ConfigParser, str]:
    """Determine the config object and section for credential lookup.

    Checks the secrets config file first for site-specific sections,
    then falls back to the main config. Lookup order:
    1. Secrets config: [site.{site_slug}] section (if site provided)
    2. Main config: [{section}] section (global fallback)

    Args:
        main_config: Main configuration object (from load_config())
        section: The section name to look for (e.g., "device", "ufm")
        site: Site name for site-specific lookup (optional)

    Returns:
        Tuple of (config_to_use, section_name) for credential lookup
    """
    secrets_config, secrets_found = load_secrets_config()
    main_mapping = _as_credential_config(main_config)
    secrets_mapping = _as_credential_config(secrets_config) if secrets_found else None
    selected, resolved_section = select_credential_source(
        main_mapping,
        secrets_mapping,
        section,
        site,
    )

    if secrets_mapping is not None and selected is secrets_mapping:
        logger.debug("Using site-specific secrets config section: [%s]", resolved_section)
        return secrets_config, resolved_section

    logger.debug("Using global [%s] section from main config", section)
    return main_config, section

def resolve_credentials(
    main_config: ConfigParser,
    section: str,
    site: str | None = None,
) -> CredentialSection:
    """Return resolved credentials while preserving service-side file loading."""
    selected, resolved_section = resolve_credential_source(main_config, section, site)
    if selected.has_section(resolved_section):
        return dict(selected[resolved_section])
    return {}


def get_rotation_passwords(
    config: ConfigParser,
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
    return _get_rotation_passwords(
        _as_credential_config(config),
        section,
        key_prefix=key_prefix,
        max_passwords=max_passwords,
    )


def get_credential(
    main_config: ConfigParser,
    section: str,
    key: str,
    site: str | None = None,
    default: str = "",
) -> str:
    """Get a single credential value with site-specific fallback.

    Checks secrets config first for site-specific value, then falls back
    through the standard resolution order.

    Args:
        main_config: Main configuration object (from load_config())
        section: The section name to look for (e.g., "device", "ufm")
        key: The key name to retrieve (e.g., "password", "ufm_api_user")
        site: Site name for site-specific lookup (optional)
        default: Default value if key not found

    Returns:
        The credential value or default if not found
    """
    selected, resolved_section = resolve_credential_source(main_config, section, site)
    if selected.has_section(resolved_section):
        value = selected[resolved_section].get(key, "")
        if value:
            return value

    if selected is not main_config and main_config.has_section(section):
        return main_config[section].get(key, default)
    return default
