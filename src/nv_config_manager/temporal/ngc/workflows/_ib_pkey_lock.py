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
"""Shared per-resource lock wiring for the IB PKey member workflows.

Import-safe inside the Temporal workflow sandbox: it declares a mixin whose
canonicalization runs only at the API boundary (never in the workflow), so the
The DCIM lookup is imported lazily.
"""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel


class _HasHost(Protocol):
    host: str


class _HasHostAndSite(_HasHost, Protocol):
    site: str | None


class UFMHostLockMixin:
    """Canonicalize ``host`` before the run so the per-resource lock keys on one
    identifier whether the caller passed a UFM device name or its IP."""

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        """Rewrite ``host`` to its canonical UFM identifier at the API boundary."""
        from nv_config_manager.temporal.ngc.activities.ib_dcim import canonicalize_ufm_host

        typed = cast("_HasHost", body)
        typed.host = await canonicalize_ufm_host(typed.host)
        return body


class UFMHostSiteValidationMixin:
    """Validate that an API-supplied UFM host and Site belong together."""

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        """Use the DCIM endpoint and reject a mismatched credential Site."""
        from nv_config_manager.temporal.ngc.activities.ib_dcim import (
            canonicalize_ufm_host_for_site,
        )

        typed = cast("_HasHostAndSite", body)
        typed.host = await canonicalize_ufm_host_for_site(typed.host, typed.site)
        return body
