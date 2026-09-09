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

from __future__ import annotations

import os
from configparser import ConfigParser, SectionProxy
from functools import lru_cache

from nv_config_manager.common.ini import FileFingerprint, file_fingerprint


@lru_cache(maxsize=1)
def _load_config(
    config_path: str,
    _fingerprint: FileFingerprint | None,
) -> ConfigParser:
    """Parse one version of the unified INI file."""
    config = ConfigParser(interpolation=None, delimiters=("=",))
    config.read(config_path)
    return config


def load_config() -> ConfigParser:
    """Load the unified nv-config-manager.ini configuration.

    All services use the same INI file. The path is determined by:
    1. NV_CONFIG_MANAGER_INI environment variable
    2. Default: /etc/vault/nv-config-manager.ini

    The parsed result is reused while the file is unchanged. Direct writes and
    Kubernetes Secret-volume symlink swaps invalidate the cache automatically.

    Returns:
        Loaded ConfigParser instance for the current file version
    """
    config_path = os.getenv("NV_CONFIG_MANAGER_INI", "/etc/vault/nv-config-manager.ini")
    return _load_config(config_path, file_fingerprint(config_path))


def clear_config_cache() -> None:
    """Clear the parsed INI cache without reading the file again."""
    _load_config.cache_clear()


def reload_config() -> ConfigParser:
    """Force reload the configuration (clears cache)."""
    clear_config_cache()
    return load_config()


def resolve_config(config: ConfigParser | None) -> ConfigParser:
    """Return an injected configuration, or load the current service configuration."""
    return config if config is not None else load_config()


def resolve_config_section(section: str, config: ConfigParser | None = None) -> SectionProxy:
    """Return the configuration section from the resolved configuration."""
    return resolve_config(config)[section]
