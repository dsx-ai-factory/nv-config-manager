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
"""Exceptions exposed by the public DCIM provider SDK."""


class DCIMError(Exception):
    """Base exception for provider-neutral DCIM failures."""


class DCIMAuthenticationError(DCIMError):
    """The provider could not authenticate with the configured DCIM."""


class DCIMAuthorizationError(DCIMError):
    """The authenticated principal lacks permission for the operation."""


class DCIMConnectivityError(DCIMError):
    """The DCIM could not be reached or returned an unavailable response."""


class DCIMConflictError(DCIMError):
    """The requested write conflicts with the current DCIM state."""


class DCIMNotFoundError(DCIMError):
    """The requested DCIM record does not exist."""


class DCIMInvalidDataError(DCIMError):
    """A provider returned data that does not satisfy the SDK contract."""


class DCIMOperationNotSupportedError(DCIMError):
    """The selected provider does not implement the requested SDK operation."""


class DCIMProviderError(DCIMError):
    """Base exception for provider discovery and lifecycle failures."""


class DCIMProviderDiscoveryError(DCIMProviderError):
    """Installed provider entry points could not be discovered or loaded."""


class DCIMProviderNotFoundError(DCIMProviderDiscoveryError):
    """The requested provider name is not installed."""


class DCIMProviderDuplicateError(DCIMProviderDiscoveryError):
    """More than one installed provider registered the same name."""


class DCIMProviderCompatibilityError(DCIMProviderError):
    """A provider does not support this SDK provider API version."""


class DCIMProviderConfigurationError(DCIMProviderError):
    """The selected provider has invalid or incomplete settings."""


class DCIMProviderInitializationError(DCIMProviderError):
    """The selected provider could not create its client."""
