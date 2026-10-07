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
"""Pluggable DCIM providers for the installer."""

from nv_config_manager_installer.providers.api import (
    INSTALLER_DCIM_ENTRY_POINT_GROUP,
    INSTALLER_PROVIDER_API_VERSION,
    AirContribution,
    DCIMInstallerProvider,
    DeployStepsContribution,
    ImageContribution,
    InstallerProviderMetadata,
    ProviderDeployContext,
    ProviderSettings,
    SecretGroupSpec,
    SecretsContribution,
    TUIContribution,
)
from nv_config_manager_installer.providers.errors import (
    InstallerProviderCompatibilityError,
    InstallerProviderConfigurationError,
    InstallerProviderDiscoveryError,
    InstallerProviderDuplicateError,
    InstallerProviderError,
    InstallerProviderNotFoundError,
)
from nv_config_manager_installer.providers.registry import (
    discover_installer_providers,
    get_installer_provider,
    installed_provider,
    resolve_provider,
)

__all__ = [
    "INSTALLER_DCIM_ENTRY_POINT_GROUP",
    "INSTALLER_PROVIDER_API_VERSION",
    "AirContribution",
    "DCIMInstallerProvider",
    "DeployStepsContribution",
    "ImageContribution",
    "InstallerProviderCompatibilityError",
    "InstallerProviderConfigurationError",
    "InstallerProviderDiscoveryError",
    "InstallerProviderDuplicateError",
    "InstallerProviderError",
    "InstallerProviderMetadata",
    "InstallerProviderNotFoundError",
    "ProviderDeployContext",
    "ProviderSettings",
    "SecretGroupSpec",
    "SecretsContribution",
    "TUIContribution",
    "discover_installer_providers",
    "get_installer_provider",
    "installed_provider",
    "resolve_provider",
]
