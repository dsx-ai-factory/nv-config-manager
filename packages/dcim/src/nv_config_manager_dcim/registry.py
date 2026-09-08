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
"""Configuration-free discovery and construction of DCIM providers."""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from nv_config_manager_dcim.api import (
    DCIM_PROVIDER_API_VERSION,
    DCIMClient,
    DCIMProvider,
    ProviderSettings,
)
from nv_config_manager_dcim.errors import (
    DCIMProviderCompatibilityError,
    DCIMProviderConfigurationError,
    DCIMProviderDiscoveryError,
    DCIMProviderDuplicateError,
    DCIMProviderInitializationError,
    DCIMProviderNotFoundError,
)

DCIM_PROVIDER_ENTRY_POINT_GROUP = "nv_config_manager.dcim"


def _entry_points_for_group() -> tuple[EntryPoint, ...]:
    return tuple(entry_points(group=DCIM_PROVIDER_ENTRY_POINT_GROUP))


def _major(version: str) -> int:
    major, separator, _ = version.partition(".")
    if not separator or not major.isdecimal():
        raise DCIMProviderCompatibilityError(f"Invalid provider API version {version!r}")
    return int(major)


def discover_dcim_providers() -> dict[str, DCIMProvider]:
    """Discover installed SDK providers without reading application configuration."""
    providers: dict[str, DCIMProvider] = {}
    required_major = _major(DCIM_PROVIDER_API_VERSION)
    for entry_point in _entry_points_for_group():
        if entry_point.name in providers:
            raise DCIMProviderDuplicateError(
                f'Multiple DCIM providers are registered as "{entry_point.name}"'
            )
        try:
            loaded: Any = entry_point.load()
            provider = loaded() if callable(loaded) else loaded
        except Exception as exc:  # noqa: BLE001 - external provider code
            raise DCIMProviderDiscoveryError(
                f'Unable to load DCIM provider entry point "{entry_point.name}": {exc}'
            ) from exc
        if not isinstance(provider, DCIMProvider):
            raise DCIMProviderDiscoveryError(
                f'DCIM provider entry point "{entry_point.name}" does not implement DCIMProvider'
            )
        if provider.metadata.name != entry_point.name:
            raise DCIMProviderDiscoveryError(
                f'DCIM provider entry point "{entry_point.name}" declares '
                f'"{provider.metadata.name}"'
            )
        supported = {_major(version) for version in provider.metadata.supported_api_versions}
        if required_major not in supported:
            raise DCIMProviderCompatibilityError(
                f'DCIM provider "{entry_point.name}" does not support SDK '
                f"{DCIM_PROVIDER_API_VERSION}"
            )
        providers[entry_point.name] = provider
    return providers


def get_dcim_provider(name: str) -> DCIMProvider:
    """Return an installed provider by explicit entry-point name."""
    providers = discover_dcim_providers()
    try:
        return providers[name]
    except KeyError as exc:
        installed = ", ".join(sorted(providers)) or "none"
        raise DCIMProviderNotFoundError(
            f'DCIM provider "{name}" is not installed; installed providers: {installed}'
        ) from exc


def create_dcim_client(name: str, settings: ProviderSettings) -> DCIMClient:
    """Validate explicit settings and construct the selected provider client."""
    provider = get_dcim_provider(name)
    try:
        provider.validate_settings(settings)
        client = provider.create_client(settings)
    except DCIMProviderConfigurationError:
        raise
    except Exception as exc:  # noqa: BLE001 - external provider code
        raise DCIMProviderInitializationError(
            f'DCIM provider "{provider.metadata.name}" could not initialize: {exc}'
        ) from exc
    if not callable(getattr(client, "close", None)):
        raise DCIMProviderInitializationError(
            f'DCIM provider "{provider.metadata.name}" returned a client without close()'
        )
    return client
