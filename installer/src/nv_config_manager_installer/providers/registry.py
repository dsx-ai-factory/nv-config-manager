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
"""Discovery and resolution of installer DCIM providers."""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from nv_config_manager_installer.providers.api import (
    INSTALLER_DCIM_ENTRY_POINT_GROUP,
    INSTALLER_PROVIDER_API_VERSION,
    DCIMInstallerProvider,
    SecretGroupSpec,
)
from nv_config_manager_installer.providers.errors import (
    InstallerProviderCompatibilityError,
    InstallerProviderConfigurationError,
    InstallerProviderDiscoveryError,
    InstallerProviderDuplicateError,
    InstallerProviderNotFoundError,
)

if TYPE_CHECKING:
    from nv_config_manager_installer.schema import NVConfigManagerInstallConfig


def _entry_points_for_group() -> tuple[EntryPoint, ...]:
    return tuple(entry_points(group=INSTALLER_DCIM_ENTRY_POINT_GROUP))


def _major(version: str) -> int:
    major, separator, _ = version.partition(".")
    if not separator or not major.isdecimal():
        raise InstallerProviderCompatibilityError(f"Invalid installer API version {version!r}")
    return int(major)


def discover_installer_providers() -> dict[str, DCIMInstallerProvider]:
    """Discover installed providers without reading any configuration."""
    providers: dict[str, DCIMInstallerProvider] = {}
    required_major = _major(INSTALLER_PROVIDER_API_VERSION)
    for entry_point in _entry_points_for_group():
        if entry_point.name in providers:
            raise InstallerProviderDuplicateError(
                f'Multiple installer providers are registered as "{entry_point.name}"'
            )
        try:
            loaded: Any = entry_point.load()
            provider = loaded() if isinstance(loaded, type) else loaded
        except Exception as exc:  # noqa: BLE001 - external provider code
            raise InstallerProviderDiscoveryError(
                f'Unable to load installer provider entry point "{entry_point.name}": {exc}'
            ) from exc
        if not isinstance(provider, DCIMInstallerProvider):
            raise InstallerProviderDiscoveryError(
                f'Installer provider entry point "{entry_point.name}" does not implement '
                "DCIMInstallerProvider"
            )
        if provider.metadata.name != entry_point.name:
            raise InstallerProviderDiscoveryError(
                f'Installer provider entry point "{entry_point.name}" declares '
                f'"{provider.metadata.name}"'
            )
        supported = {_major(version) for version in provider.metadata.supported_api_versions}
        if required_major not in supported:
            raise InstallerProviderCompatibilityError(
                f'Installer provider "{entry_point.name}" does not support installer API '
                f"{INSTALLER_PROVIDER_API_VERSION}"
            )
        providers[entry_point.name] = provider
    return providers


def get_installer_provider(name: str) -> DCIMInstallerProvider:
    """Return an installed provider by entry-point name."""
    providers = discover_installer_providers()
    try:
        return providers[name]
    except KeyError as exc:
        installed = ", ".join(sorted(providers)) or "none"
        raise InstallerProviderNotFoundError(
            f'DCIM provider "{name}" is not installed; installed providers: {installed}. '
            "Install the provider's package with its installer extra."
        ) from exc


def installed_provider(config: NVConfigManagerInstallConfig) -> DCIMInstallerProvider | None:
    """Return the configured provider, or None when it cannot be loaded."""
    try:
        return get_installer_provider(config.dcim.provider)
    except Exception:  # noqa: BLE001 - UI callers degrade to core-only behavior
        return None


def provider_secret_groups(provider: DCIMInstallerProvider) -> dict[str, SecretGroupSpec]:
    """Return the provider's secret groups keyed by schema field name."""
    return {spec.name: spec for spec in provider.secrets.groups}


def resolve_provider(config: NVConfigManagerInstallConfig) -> DCIMInstallerProvider:
    """Return the configured provider after validating the configuration against it."""
    provider = get_installer_provider(config.dcim.provider)
    try:
        provider.settings(config)
    except ValidationError as exc:
        raise InstallerProviderConfigurationError(f"dcim.settings is invalid: {exc}") from exc
    try:
        config.secrets.k8s.bind_groups(provider_secret_groups(provider))
        config.secrets.vault.paths.bind_groups(provider_secret_groups(provider))
    except ValueError as exc:
        raise InstallerProviderConfigurationError(str(exc)) from exc
    errors = provider.validate_config(config)
    if errors:
        raise InstallerProviderConfigurationError("; ".join(errors))
    return provider
