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
"""Errors raised while discovering or resolving installer DCIM providers."""

from __future__ import annotations


class InstallerProviderError(RuntimeError):
    """Base class for installer provider failures."""


class InstallerProviderDiscoveryError(InstallerProviderError):
    """An entry point could not be loaded or does not implement the contract."""


class InstallerProviderDuplicateError(InstallerProviderError):
    """Two distributions register the same provider name."""


class InstallerProviderCompatibilityError(InstallerProviderError):
    """The provider targets an unsupported installer API version."""


class InstallerProviderNotFoundError(InstallerProviderError):
    """``dcim.provider`` names a provider that is not installed."""


class InstallerProviderConfigurationError(InstallerProviderError):
    """The installer configuration is invalid for the selected provider."""
