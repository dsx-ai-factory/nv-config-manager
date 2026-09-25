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
"""Redfish vendor HTTP contract tests."""

import base64
from collections.abc import Callable
from unittest.mock import Mock, patch

import pytest
import requests
import responses
from responses import matchers
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish.bluefield import Bluefield3RedfishConnection
from nv_config_manager_workflows.clients.redfish.dell import DellRedfishConnection
from nv_config_manager_workflows.clients.redfish.lenovo import LenovoRedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor

type VendorConnection = (
    LenovoRedfishConnection | Bluefield3RedfishConnection | DellRedfishConnection
)
type RotatingConnection = LenovoRedfishConnection | Bluefield3RedfishConnection

ADDRESS = "192.0.2.10"
USERNAME = "redfish-user"
LOGIN_PASSWORD = "login-password"
MANAGED_PASSWORD = "managed-password"
BASE_URL = f"https://{ADDRESS}:443/redfish/v1"


def _managed_password() -> str:
    return MANAGED_PASSWORD


def _connection(
    vendor: RedfishVendor,
    password_provider: Callable[[], str] = _managed_password,
) -> VendorConnection:
    host = RedfishHost(address=ADDRESS, vendor=vendor)
    if vendor == RedfishVendor.LENOVO:
        return LenovoRedfishConnection(host, USERNAME, LOGIN_PASSWORD, password_provider)
    if vendor == RedfishVendor.BLUEFIELD:
        return Bluefield3RedfishConnection(host, USERNAME, LOGIN_PASSWORD, password_provider)
    return DellRedfishConnection(host, USERNAME, LOGIN_PASSWORD)


def _rotating_connection(
    vendor: RedfishVendor,
    password_provider: Callable[[], str] = _managed_password,
) -> RotatingConnection:
    connection = _connection(vendor, password_provider)
    assert isinstance(connection, (LenovoRedfishConnection, Bluefield3RedfishConnection))
    return connection


def _auth_header(password: str = LOGIN_PASSWORD) -> dict[str, str]:
    token = base64.b64encode(f"{USERNAME}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.parametrize(
    ("vendor", "path"),
    [
        (RedfishVendor.LENOVO, "Managers/1"),
        (RedfishVendor.BLUEFIELD, "Managers/Bluefield_BMC"),
        (RedfishVendor.DELL, "Managers/iDRAC.Embedded.1"),
    ],
)
def test_get_redfish_data_preserves_vendor_endpoint_and_authentication(
    vendor: RedfishVendor,
    path: str,
) -> None:
    connection = _connection(vendor)
    with responses.RequestsMock() as http:
        http.add(
            responses.GET,
            f"{BASE_URL}/{path}",
            json={"vendor": vendor.value},
            match=[matchers.header_matcher(_auth_header())],
        )

        response = connection.get_redfish_data()

    assert response.json() == {"vendor": vendor.value}


@pytest.mark.parametrize(
    ("vendor", "path"),
    [
        (RedfishVendor.LENOVO, "Systems/1/Actions/ComputerSystem.Reset"),
        (RedfishVendor.BLUEFIELD, "Systems/Bluefield/Actions/ComputerSystem.Reset"),
        (
            RedfishVendor.DELL,
            "Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        ),
    ],
)
def test_power_off_preserves_vendor_endpoint_payload_and_http_errors(
    vendor: RedfishVendor,
    path: str,
) -> None:
    connection = _connection(vendor)
    request_matchers = [
        matchers.header_matcher(_auth_header()),
        matchers.json_params_matcher({"ResetType": "GracefulShutdown"}),
    ]
    with responses.RequestsMock() as http:
        http.add(
            responses.POST,
            f"{BASE_URL}/{path}",
            json={},
            match=request_matchers,
        )
        assert connection.power_off_chassis().ok

        http.add(
            responses.POST,
            f"{BASE_URL}/{path}",
            status=500,
            match=request_matchers,
        )
        with pytest.raises(requests.exceptions.HTTPError):
            connection.power_off_chassis()


@pytest.mark.parametrize(
    ("vendor", "account"),
    [
        (RedfishVendor.LENOVO, "1"),
        (RedfishVendor.BLUEFIELD, "root"),
    ],
)
def test_password_rotation_uses_lazy_provider_and_vendor_payload(
    vendor: RedfishVendor,
    account: str,
) -> None:
    password_provider = Mock(return_value=MANAGED_PASSWORD)
    connection = _rotating_connection(vendor, password_provider)
    with responses.RequestsMock() as http:
        http.add(
            responses.PATCH,
            f"{BASE_URL}/AccountService/Accounts/{account}",
            json={},
            match=[
                matchers.header_matcher(_auth_header()),
                matchers.json_params_matcher({"Password": MANAGED_PASSWORD}),
            ],
        )

        response = connection.set_config_manager_password()

    assert response.ok
    assert connection.password == MANAGED_PASSWORD
    password_provider.assert_called_once_with()


@pytest.mark.parametrize(
    ("vendor", "path", "payload"),
    [
        (
            RedfishVendor.LENOVO,
            "Managers/1/Actions/Manager.ResetToDefaults",
            {"ResetType": "ResetAll"},
        ),
        (
            RedfishVendor.BLUEFIELD,
            "Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
            {"ResetToDefaultsType": "ResetAll"},
        ),
    ],
)
def test_factory_reset_preserves_vendor_endpoint_and_payload(
    vendor: RedfishVendor,
    path: str,
    payload: dict[str, str],
) -> None:
    connection = _rotating_connection(vendor)
    with (
        responses.RequestsMock() as http,
        patch.object(connection, "wait_for_restart") as wait_for_restart,
    ):
        http.add(
            responses.POST,
            f"{BASE_URL}/{path}",
            json={},
            match=[matchers.json_params_matcher(payload)],
        )

        response = connection.factory_reset()

    assert response is not None
    assert response.ok
    assert connection.password == LOGIN_PASSWORD
    wait_for_restart.assert_called_once_with()


def test_factory_reset_preserves_vendor_error_handling() -> None:
    lenovo = _rotating_connection(RedfishVendor.LENOVO)
    with (
        responses.RequestsMock() as http,
        patch.object(lenovo, "wait_for_restart") as lenovo_wait,
    ):
        http.add(
            responses.POST,
            f"{BASE_URL}/Managers/1/Actions/Manager.ResetToDefaults",
            status=500,
        )
        with pytest.raises(requests.exceptions.HTTPError):
            lenovo.factory_reset()
        lenovo_wait.assert_not_called()

    bluefield = _rotating_connection(RedfishVendor.BLUEFIELD)
    with (
        responses.RequestsMock() as http,
        patch.object(bluefield, "wait_for_restart") as bluefield_wait,
    ):
        http.add(
            responses.POST,
            f"{BASE_URL}/Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
            status=500,
        )
        assert bluefield.factory_reset() is None
        bluefield_wait.assert_called_once_with()

    dell = _connection(RedfishVendor.DELL)
    with pytest.raises(
        ApplicationError,
        match="BMC factory reset should not be performed Dell",
    ):
        dell.factory_reset()
