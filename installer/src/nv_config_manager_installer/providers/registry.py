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
    InstallerProviderError,
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


# Loaded providers keyed by entry point; a provider class is instantiated once.
_LOADED: dict[Any, DCIMInstallerProvider | InstallerProviderError] = {}


def _load(entry_point: EntryPoint) -> DCIMInstallerProvider:
    """Load and check one entry point, raising an installer provider error on failure."""
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
    required_major = _major(INSTALLER_PROVIDER_API_VERSION)
    supported = {_major(version) for version in provider.metadata.supported_api_versions}
    if required_major not in supported:
        raise InstallerProviderCompatibilityError(
            f'Installer provider "{entry_point.name}" does not support installer API '
            f"{INSTALLER_PROVIDER_API_VERSION}"
        )
    return provider


def _discover() -> tuple[dict[str, DCIMInstallerProvider], dict[str, InstallerProviderError]]:
    """Return loadable providers and per-name errors for the rest."""
    providers: dict[str, DCIMInstallerProvider] = {}
    errors: dict[str, InstallerProviderError] = {}
    seen: set[str] = set()
    for entry_point in _entry_points_for_group():
        name = entry_point.name
        if name in seen:
            providers.pop(name, None)
            errors[name] = InstallerProviderDuplicateError(
                f'Multiple installer providers are registered as "{name}"'
            )
            continue
        seen.add(name)
        try:
            cached = _LOADED[entry_point]
        except (KeyError, TypeError):
            try:
                cached = _load(entry_point)
            except InstallerProviderError as exc:
                cached = exc
            try:
                _LOADED[entry_point] = cached
            except TypeError:
                pass  # unhashable entry point; load again next time
        if isinstance(cached, InstallerProviderError):
            errors[name] = cached
        else:
            providers[name] = cached
    return providers, errors


def discover_installer_providers() -> dict[str, DCIMInstallerProvider]:
    """Return the installed providers that load; broken ones are left out.

    Use :func:`installer_provider_errors` to report the ones that failed.
    """
    return _discover()[0]


def installer_provider_errors() -> dict[str, InstallerProviderError]:
    """Return load errors for installed providers, keyed by entry-point name."""
    return _discover()[1]


def get_installer_provider(name: str) -> DCIMInstallerProvider:
    """Return an installed provider by entry-point name."""
    providers, errors = _discover()
    if name in providers:
        return providers[name]
    if name in errors:
        raise errors[name]
    installed = ", ".join(sorted(providers)) or "none"
    raise InstallerProviderNotFoundError(
        f'DCIM provider "{name}" is not installed; installed providers: {installed}. '
        "Install the provider's package with its installer extra."
    )


def installed_provider(config: NVConfigManagerInstallConfig) -> DCIMInstallerProvider | None:
    """Return the configured provider, or None when it cannot be loaded."""
    try:
        return get_installer_provider(config.dcim.provider)
    except Exception:  # noqa: BLE001 - UI callers degrade to core-only behavior
        return None


def provider_display_name(config: NVConfigManagerInstallConfig) -> str:
    """Return the configured provider's display name, or "DCIM" when it cannot be loaded."""
    provider = installed_provider(config)
    return provider.metadata.display_name if provider is not None else "DCIM"


def provider_secret_groups(provider: DCIMInstallerProvider) -> dict[str, SecretGroupSpec]:
    """Return the provider's secret groups keyed by schema field name."""
    return {spec.name: spec for spec in provider.secrets.groups}


def resolve_provider(config: NVConfigManagerInstallConfig) -> DCIMInstallerProvider:
    """Return the configured provider after validating the configuration against it."""
    provider = get_installer_provider(config.dcim.provider)
    meta = provider.metadata
    if config.dcim.local and not meta.supports_local:
        raise InstallerProviderConfigurationError(
            f'DCIM provider "{meta.name}" cannot be deployed in the cluster; '
            "set dcim.local to false and dcim.server to an existing instance"
        )
    if not config.dcim.local and not meta.supports_external:
        raise InstallerProviderConfigurationError(
            f'DCIM provider "{meta.name}" must be deployed in the cluster; set dcim.local to true'
        )
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
