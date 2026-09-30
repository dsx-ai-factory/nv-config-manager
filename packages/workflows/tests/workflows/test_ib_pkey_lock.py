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
"""Tests for the InfiniBand PKey resource-lock host canonicalization mixins."""

from typing import Any

import pytest
from pydantic import BaseModel

from nv_config_manager_workflows.mixins.ib_pkey import (
    UFMHostLockMixin,
    UFMHostSiteValidationMixin,
)


class _HostInput(BaseModel):
    host: str
    pkey: str = "0x0100"


class _HostAndSiteInput(BaseModel):
    host: str
    site: str | None = None


@pytest.mark.asyncio
async def test_canonicalizes_host_before_run(mocker: Any) -> None:
    """The mixin rewrites host so name and IP collapse to one lock key."""
    mocker.patch(
        "nv_config_manager_workflows.mixins.ib_pkey._canonicalize_ufm_host",
        new=mocker.AsyncMock(return_value="10.0.0.5"),
    )
    body = _HostInput(host="ufm01")

    result = await UFMHostLockMixin.canonicalize_input(body)

    assert result is body
    assert body.host == "10.0.0.5"
    assert body.pkey == "0x0100"


@pytest.mark.asyncio
async def test_canonicalizes_host_and_validates_site_before_run(mocker: Any) -> None:
    """The API-only mixin validates the host/Site pair in one provider lookup."""
    canonicalize = mocker.patch(
        "nv_config_manager_workflows.mixins.ib_pkey._canonicalize_ufm_host_for_site",
        new=mocker.AsyncMock(return_value="10.0.0.5"),
    )
    body = _HostAndSiteInput(host="ufm01", site="site-a")

    result = await UFMHostSiteValidationMixin.canonicalize_input(body)

    assert result is body
    assert body.host == "10.0.0.5"
    assert body.site == "site-a"
    canonicalize.assert_awaited_once_with("ufm01", "site-a")
