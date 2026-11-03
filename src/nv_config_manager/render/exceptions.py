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
"""Render Service Exceptions."""

import aiohttp

from nv_config_manager.common.client.config_store import ConfigStoreUnavailableError
from nv_config_manager.dcim import DCIMConnectivityError, DCIMReadCancelledError


class RenderException(Exception):
    """Failure to render device configuration."""


class RenderScopeMismatch(RenderException):
    """The device is outside this deployment's aggregate-management scope."""


def is_transient_dependency_error(exc: BaseException) -> bool:
    """Return whether a DCIM or Config Store failure may succeed if retried.

    Data errors are not transient: the render cannot succeed until the data is
    fixed, and fixing it publishes a new change event anyway.
    """
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status == 429 or exc.status >= 500
    return isinstance(
        exc,
        (
            DCIMConnectivityError,
            DCIMReadCancelledError,
            ConfigStoreUnavailableError,
            aiohttp.ClientConnectionError,
            TimeoutError,
        ),
    )
