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
"""Preserve Redfish vendor dispatch with explicit credentials."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection,
    DellRedfishConnection,
    LenovoRedfishConnection,
    RedfishConnection,
    RedfishDpu,
    RedfishHost,
    RedfishServer,
    RedfishVendor,
    get_config_manager_connection,
    get_default_connection,
)


def _host(kind: str, vendor: RedfishVendor) -> RedfishHost:
    if kind == "server":
        return RedfishServer(
            address="192.0.2.10",
            vendor=vendor,
            serial="SERVER-1",
            nics=[],
        )
    if kind == "dpu":
        return RedfishDpu(
            address="192.0.2.10",
            vendor=vendor,
            ports=[],
            base_mac="AA-BB-CC-DD-EE-FF",
            serial="DPU-1",
        )
    return RedfishHost(address="192.0.2.10", vendor=vendor)


@pytest.mark.parametrize(
    "factory",
    [get_default_connection, get_config_manager_connection],
    ids=["default", "managed"],
)
@pytest.mark.parametrize("kind", ["host", "server", "dpu"])
@pytest.mark.parametrize(
    ("vendor", "expected_type"),
    [
        (RedfishVendor.LENOVO, LenovoRedfishConnection),
        (RedfishVendor.BLUEFIELD, Bluefield3RedfishConnection),
        (RedfishVendor.DELL, DellRedfishConnection),
    ],
)
def test_vendor_dispatch_for_every_host_type(
    factory: Callable[..., RedfishConnection],
    kind: str,
    vendor: RedfishVendor,
    expected_type: type[RedfishConnection],
) -> None:
    host = _host(kind, vendor)

    def managed_password() -> str:
        return "placeholder-managed-password"

    connection = factory(
        host,
        username="placeholder-user",
        password="placeholder-login-password",
        config_manager_password=managed_password,
    )

    assert type(connection) is expected_type
    assert connection.host is host
    with connection.get_session() as session:
        assert session.auth == ("placeholder-user", "placeholder-login-password")
    if isinstance(connection, (LenovoRedfishConnection, Bluefield3RedfishConnection)):
        assert connection.config_manager_password is managed_password


@pytest.mark.parametrize(
    "data", [{"address": "192.0.2.10"}, {"address": "192.0.2.10", "vendor": "Other"}]
)
def test_missing_or_unknown_vendor_is_rejected_by_host_model(data: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        RedfishHost.model_validate(data)
