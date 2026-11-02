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
"""Tests for package-owned InfiniBand PKey host mixins."""

from __future__ import annotations

from typing import cast

from nv_config_manager_dcim import DCIMClient, IBHostSite
from pydantic import BaseModel

from nv_config_manager_workflows.mixins.ib_pkey import (
    UFMHostLockMixin,
    UFMHostSiteValidationMixin,
)
from nv_config_manager_workflows.runtime import configure_dcim_client

_DEVICE_NAME = "ufm01"
_DEVICE_IP = "10.0.0.5"
_SITE_ID = "354dae20-64ef-4a7f-b1ca-2b584d20fa94"
_SITE_NAME = "site-a"


class _HostInput(BaseModel):
    host: str
    pkey: str = "0x0100"
    request_id: str


class _HostAndSiteInput(BaseModel):
    host: str
    site: str | None = None
    request_id: str


class StubIBDCIMClient:
    """Minimal runtime-injected client used by both package mixins."""

    async def __aenter__(self) -> StubIBDCIMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    async def canonicalize_ib_host(self, host: str) -> str:
        assert host == _DEVICE_NAME
        return _DEVICE_IP

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        assert host == _DEVICE_NAME
        return IBHostSite(
            device_id="device-1",
            device_name=_DEVICE_NAME,
            device_primary_ip=_DEVICE_IP,
            site_id=_SITE_ID,
            site_name=_SITE_NAME,
        )


def _configure_client() -> None:
    client = StubIBDCIMClient()
    configure_dcim_client(lambda: cast(DCIMClient, client))


async def test_host_lock_mixin_mutates_and_returns_the_same_model() -> None:
    """Only the canonical host changes before lock-key construction."""
    _configure_client()
    body = _HostInput(host=_DEVICE_NAME, pkey="0x0100", request_id="request-1")

    result = await UFMHostLockMixin.canonicalize_input(body)

    assert result is body
    assert body.host == _DEVICE_IP
    assert body.pkey == "0x0100"
    assert body.request_id == "request-1"


async def test_site_validation_mixin_mutates_and_returns_the_same_model() -> None:
    """Host validation preserves the Site and every unrelated input field."""
    _configure_client()
    body = _HostAndSiteInput(host=_DEVICE_NAME, site=_SITE_NAME, request_id="request-2")

    result = await UFMHostSiteValidationMixin.canonicalize_input(body)

    assert result is body
    assert body.host == _DEVICE_IP
    assert body.site == _SITE_NAME
    assert body.request_id == "request-2"
