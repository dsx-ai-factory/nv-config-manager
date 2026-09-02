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
"""Configuration-driven PKI provider registry and client factory."""

from __future__ import annotations

import re
from collections.abc import Callable
from configparser import ConfigParser, SectionProxy

from nv_config_manager.common.config import load_config, parse_verify_param
from nv_config_manager.pki.base import PKIClient, PKIConfigurationError
from nv_config_manager.pki.vault import VaultPKIClient, VaultPKISource

PKIClientFactory = Callable[[ConfigParser], PKIClient]
_PATH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_PROVIDERS: dict[str, PKIClientFactory] = {}


def _required(section: SectionProxy, key: str) -> str:
    value = section.get(key, "").strip()
    if not value:
        raise PKIConfigurationError(f"[{section.name}] {key} is required")
    return value


def _vault_client(config: ConfigParser) -> PKIClient:
    if not config.has_section("pki.vault"):
        raise PKIConfigurationError("[pki.vault] is required for the vault provider")
    vault = config["pki.vault"]
    pki_mount = _required(vault, "pki_mount").strip("/")
    auth_mount = _required(vault, "auth_mount").strip("/")
    if not _PATH_PATTERN.fullmatch(pki_mount) or not _PATH_PATTERN.fullmatch(auth_mount):
        raise PKIConfigurationError("Vault mount paths contain unsupported characters")

    sources: dict[str, VaultPKISource] = {}
    for section_name in config.sections():
        if not section_name.startswith("pki.source."):
            continue
        source_name = section_name.removeprefix("pki.source.")
        if not source_name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", source_name):
            raise PKIConfigurationError(f"Invalid PKI source section: {section_name}")
        section = config[section_name]
        issue_role = section.get("issue_role", "").strip() or None
        ca_path = section.get("ca_path", "").strip().strip("/") or None
        if issue_role and not _PATH_PATTERN.fullmatch(issue_role):
            raise PKIConfigurationError(f"[{section_name}] issue_role is invalid")
        if ca_path and not _PATH_PATTERN.fullmatch(ca_path):
            raise PKIConfigurationError(f"[{section_name}] ca_path is invalid")
        if not issue_role and not ca_path:
            raise PKIConfigurationError(f"[{section_name}] requires issue_role, ca_path, or both")
        sources[source_name] = VaultPKISource(
            issue_role=issue_role,
            ca_path=ca_path,
            ttl=section.get("ttl", "168h").strip(),
            common_name_template=section.get(
                "common_name_template",
                "device-{device_id}.switches.dev.dsx.nvidia.com",
            ).strip(),
        )
    if not sources:
        raise PKIConfigurationError("At least one [pki.source.<name>] section is required")

    return VaultPKIClient(
        address=_required(vault, "address"),
        namespace=vault.get("namespace", "").strip() or None,
        auth_mount=auth_mount,
        auth_role=_required(vault, "auth_role"),
        token_path=_required(vault, "token_path"),
        pki_mount=pki_mount,
        sources=sources,
        verify=parse_verify_param(vault),
        timeout_seconds=vault.getfloat("timeout_seconds", fallback=30.0),
    )


def register_pki_provider(name: str, factory: PKIClientFactory) -> None:
    """Register or replace a PKI provider factory."""
    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("PKI provider name must not be empty")
    _PROVIDERS[normalized_name] = factory


def create_pki_client(config: ConfigParser | None = None) -> PKIClient:
    """Create the configured provider-neutral PKI client."""
    if config is None:
        config = load_config()
    if not config.has_section("pki"):
        raise PKIConfigurationError("[pki] is not configured")
    provider = config.get("pki", "provider", fallback="").strip().lower()
    if not provider:
        raise PKIConfigurationError("[pki] provider is required")
    try:
        factory = _PROVIDERS[provider]
    except KeyError as exc:
        raise PKIConfigurationError(f"Unknown PKI provider: {provider}") from exc
    return factory(config)


register_pki_provider("vault", _vault_client)
