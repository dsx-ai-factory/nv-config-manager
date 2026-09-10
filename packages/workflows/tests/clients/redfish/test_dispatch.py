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
"""Tests for pure Redfish vendor dispatch."""

import logging
from enum import StrEnum

import pytest
from pydantic import ValidationError

from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection,
    DellRedfishConnection,
    LenovoRedfishConnection,
    RedfishClientSettings,
    RedfishConnection,
    RedfishHost,
    RedfishVendor,
    get_config_manager_connection,
    get_default_connection,
)

SETTINGS: RedfishClientSettings = {
    "username": "explicit-user",
    "password": "explicit-password",
    "config_manager_password": "explicit-managed-password",
}


class UnknownVendor(StrEnum):
    """Unsupported vendor used to exercise the legacy dispatch fallback."""

    UNKNOWN = "Unknown"


@pytest.mark.parametrize(
    ("vendor", "connection_type"),
    [
        (RedfishVendor.LENOVO, LenovoRedfishConnection),
        (RedfishVendor.BLUEFIELD, Bluefield3RedfishConnection),
        (RedfishVendor.DELL, DellRedfishConnection),
    ],
)
@pytest.mark.parametrize(
    "factory",
    [get_default_connection, get_config_manager_connection],
)
def test_vendor_dispatch_uses_explicit_settings(
    vendor: RedfishVendor,
    connection_type: type[RedfishConnection],
    factory,
) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=vendor)

    connection = factory(host, SETTINGS)

    assert isinstance(connection, connection_type)
    assert connection.host is host
    assert connection.username == SETTINGS["username"]
    assert connection.password == SETTINGS["password"]
    assert connection.config_manager_password == SETTINGS["config_manager_password"]


def test_unknown_vendor_preserves_not_implemented_error() -> None:
    host = RedfishHost.model_construct(
        address="192.0.2.10",
        port=443,
        vendor=UnknownVendor.UNKNOWN,
        mac=None,
    )

    with pytest.raises(
        NotImplementedError,
        match="No Redfish connection implemented for vendor Unknown/None/192.0.2.10:443",
    ):
        get_default_connection(host, SETTINGS)


@pytest.mark.parametrize("vendor", [None, "Unknown"])
def test_missing_or_unknown_vendor_preserves_model_validation(vendor) -> None:
    values = {"address": "192.0.2.10"}
    if vendor is not None:
        values["vendor"] = vendor

    with pytest.raises(ValidationError):
        RedfishHost.model_validate(values)


def test_dispatch_does_not_log_credentials(caplog: pytest.LogCaptureFixture) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.LENOVO)

    with caplog.at_level(logging.DEBUG):
        get_default_connection(host, SETTINGS)

    assert SETTINGS["username"] not in caplog.text
    assert SETTINGS["password"] not in caplog.text
    assert SETTINGS["config_manager_password"] not in caplog.text
