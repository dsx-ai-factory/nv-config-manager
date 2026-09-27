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
"""Provider-neutral tests for InfiniBand host canonicalization."""

from __future__ import annotations

from typing import cast

import pytest
from nv_config_manager_dcim import DCIMClient, IBHostSite
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_dcim import (
    canonicalize_ufm_host,
    canonicalize_ufm_host_for_site,
)
from nv_config_manager_workflows.runtime import configure_dcim_client

_DEVICE_NAME = "ufm01"
_DEVICE_IP = "10.0.0.5"
_SITE_ID = "354dae20-64ef-4a7f-b1ca-2b584d20fa94"
_SITE_NAME = "site-a"


class StubIBDCIMClient:
    """Minimal runtime-injected client for canonicalization tests."""

    def __init__(self, *, primary_ip: str | None = _DEVICE_IP) -> None:
        self.primary_ip = primary_ip
        self.canonicalized_hosts: list[str] = []
        self.resolved_hosts: list[str] = []

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
        self.canonicalized_hosts.append(host)
        return self.primary_ip or _DEVICE_NAME

    async def resolve_ib_host_site(self, host: str) -> IBHostSite:
        self.resolved_hosts.append(host)
        return IBHostSite(
            device_id="device-1",
            device_name=_DEVICE_NAME,
            device_primary_ip=self.primary_ip,
            site_id=_SITE_ID,
            site_name=_SITE_NAME,
        )


def _configure_client(*, primary_ip: str | None = _DEVICE_IP) -> StubIBDCIMClient:
    client = StubIBDCIMClient(primary_ip=primary_ip)
    configure_dcim_client(lambda: cast(DCIMClient, client))
    return client


async def test_hostname_and_ip_canonicalize_to_the_same_identifier() -> None:
    """Equivalent UFM identifiers collapse before workflow lock construction."""
    client = _configure_client()

    assert await canonicalize_ufm_host(_DEVICE_NAME) == _DEVICE_IP
    assert await canonicalize_ufm_host(_DEVICE_IP) == _DEVICE_IP
    assert client.canonicalized_hosts == [_DEVICE_NAME, _DEVICE_IP]


async def test_site_canonicalization_falls_back_to_device_name_without_primary_ip() -> None:
    """A managed UFM without a primary IP still has a stable identifier."""
    client = _configure_client(primary_ip=None)

    assert await canonicalize_ufm_host_for_site(_DEVICE_NAME, None) == _DEVICE_NAME
    assert client.resolved_hosts == [_DEVICE_NAME]


@pytest.mark.parametrize("site_reference", [_SITE_ID, _SITE_NAME])
async def test_site_canonicalization_accepts_site_id_and_name(site_reference: str) -> None:
    """Both public Site reference forms validate against the provider result."""
    _configure_client()

    assert await canonicalize_ufm_host_for_site(_DEVICE_NAME, site_reference) == _DEVICE_IP


async def test_site_canonicalization_preserves_mismatch_error_contract() -> None:
    """Site mismatches remain permanent failures with the existing message."""
    _configure_client()

    with pytest.raises(ApplicationError) as exc_info:
        await canonicalize_ufm_host_for_site(_DEVICE_NAME, "site-b")

    assert exc_info.value.message == ("UFM device 'ufm01' belongs to Site 'site-a', not 'site-b'")
    assert exc_info.value.non_retryable is True
