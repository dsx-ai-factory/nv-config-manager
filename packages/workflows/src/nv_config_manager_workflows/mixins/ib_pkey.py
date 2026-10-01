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
"""API-boundary UFM host canonicalization for InfiniBand PKey workflows."""

from __future__ import annotations

from typing import Protocol, cast
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.exceptions import ApplicationError

# The DCIM client dependency must pass through Temporal's workflow import sandbox.
with workflow.unsafe.imports_passed_through():
    from nv_config_manager_workflows.dcim_session import dcim_client_session


class _HasHost(Protocol):
    host: str


class _HasHostAndSite(_HasHost, Protocol):
    site: str | None


async def _canonicalize_ufm_host(host: str) -> str:
    """Resolve a UFM host (device name or IPv4) to one identifier."""
    async with dcim_client_session() as client:
        return str(await client.canonicalize_ib_host(host))


async def _canonicalize_ufm_host_for_site(host: str, site_reference: str | None) -> str:
    """Resolve an API-supplied UFM host and verify its optional Site reference."""
    async with dcim_client_session() as client:
        host_site = await client.resolve_ib_host_site(host)
    canonical_host = str(host_site.device_primary_ip or host_site.device_name)

    normalized_reference = site_reference
    if site_reference is not None:
        try:
            normalized_reference = str(UUID(site_reference))
        except ValueError:
            pass
    if normalized_reference is not None and normalized_reference not in {
        host_site.site_id,
        host_site.site_name,
    }:
        raise ApplicationError(
            f"UFM device {host_site.device_name!r} belongs to Site {host_site.site_name!r}, "
            f"not {site_reference!r}",
            non_retryable=True,
        )
    return canonical_host


class UFMHostLockMixin:
    """Canonicalize ``host`` before the run so the per-resource lock keys on one
    identifier whether the caller passed a UFM device name or its IP."""

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        """Rewrite ``host`` to its canonical UFM identifier at the API boundary."""
        typed = cast("_HasHost", body)
        typed.host = await _canonicalize_ufm_host(typed.host)
        return body


class UFMHostSiteValidationMixin:
    """Validate that an API-supplied UFM host and Site belong together."""

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        """Use the DCIM endpoint and reject a mismatched credential Site."""
        typed = cast("_HasHostAndSite", body)
        typed.host = await _canonicalize_ufm_host_for_site(typed.host, typed.site)
        return body
