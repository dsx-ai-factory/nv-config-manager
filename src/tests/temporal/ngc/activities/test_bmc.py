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
"""Service credential and HTTP integration tests for BMC and Redfish."""

import base64
import copy
from unittest.mock import patch

import pytest
import requests
import responses
from aioresponses import aioresponses
from responses import matchers
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.redfish import (
    RedfishDpu,
    RedfishDpuPort,
    RedfishHost,
    RedfishNic,
    RedfishServer,
    RedfishVendor,
)
from nv_config_manager.temporal.common.mixins.device import (
    HostDeviceData,
    InterfaceData,
)
from nv_config_manager.temporal.ngc.activities.bmc import (
    GetDpuDetailsActivityInput,
    GetDpuDetailsActivityOutput,
    GetServerDetailsActivityInput,
    GetServerDetailsActivityOutput,
    RedfishHostInput,
    RedfishHostOutput,
    UpdateDpuDataActivityInput,
    UpdateDpuDataActivityOutput,
    factory_reset_bmc,
    get_dpu_details,
    get_server_details,
    power_on_host,
    set_redfish_password,
    update_dpu_data,
)
from tests.temporal.ngc.activities.test_bmc_data import (
    BLUEFIELD_CHASSIS,
    BLUEFIELD_FACTORY_RESET_RESPONSE,
    BLUEFIELD_PASSWORD_RESPONSE,
    BLUEFIELD_SYS_INFO,
    BLUEFIELD_SYSTEM_INFO,
    BLUEFIELD_UNAUTHORIZED_RESPONSE,
    DELL_NETWORK_ADAPTER_DETAILS,
    DELL_NETWORK_ADAPTERS,
    DELL_NETWORK_FUNCTION_DETAIL,
    DELL_SYSTEM_INFO,
    DELL_UNAUTHORIZED_RESPONSE,
    LENOVO_NETWORK_ADAPTER_DETAILS,
    LENOVO_NETWORK_ADAPTERS,
    LENOVO_PASSWORD_RESPONSE,
    LENOVO_PORT_DETAILS,
    LENOVO_SYSTEM_INFO,
    LENOVO_UNAUTHORIZED_RESPONSE,
    TEST_DPU_DEVICES,
    TEST_REDFISH_DPUS,
    TEST_SERVERS,
)


def bmc_http_auth(username, password) -> dict[str, str]:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


@responses.activate
def test_set_redfish_password():
    responses.add(
        responses.PATCH,
        "https://127.0.0.1:443/redfish/v1/AccountService/Accounts/1",
        json=LENOVO_PASSWORD_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "PASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="c8:4b:d6:7a:e9:e2",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="c8:4b:d6:7a:e9:e2",
        )
    )

    responses.add(
        responses.PATCH,
        "https://127.0.0.2:443/redfish/v1/AccountService/Accounts/root",
        json=BLUEFIELD_PASSWORD_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER2", "PASSWORD2"))],
    )

    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="c8:4b:d6:7a:e8:f2",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="c8:4b:d6:7a:e8:f2",
        )
    )

    # Dell password should not be changed
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="58-A2-E1-72-DD-C5",
        )
    )
    with pytest.raises(ApplicationError) as error:
        result = set_redfish_password(activity_input)
    assert error.type is ApplicationError
    assert error.value.args[0] == (
        "BMC password should not be changed for Dell: https://127.0.0.3:443/redfish/v1"
    )


@responses.activate
def test_no_mac_fallback_creds():
    responses.add(
        responses.PATCH,
        "https://127.0.0.6:443/redfish/v1/AccountService/Accounts/1",
        json=LENOVO_PASSWORD_RESPONSE,
        match=[
            matchers.header_matcher(bmc_http_auth("LENOVO_DEFAULT_USER", "LENOVO_DEFAULT_PASSWORD"))
        ],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.6",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-28-F2",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.6",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-28-F2",
        )
    )

    responses.add(
        responses.PATCH,
        "https://127.0.0.7:443/redfish/v1/AccountService/Accounts/root",
        json=BLUEFIELD_PASSWORD_RESPONSE,
        match=[
            matchers.header_matcher(
                bmc_http_auth("BLUEFIELD_DEFAULT_USER", "BLUEFIELD_DEFAULT_PASSWORD")
            )
        ],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.7",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="D0-8E-79-F8-12-44",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.7",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="D0-8E-79-F8-12-44",
        )
    )

    # Dell cannot fallback to default auth
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.8",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="D0-1E-79-A8-12-46",
        )
    )
    with pytest.raises(ApplicationError) as error:
        result = set_redfish_password(activity_input)
    assert error.type is ApplicationError
    assert error.value.args[0] == (
        "No password found for host Dell/D0-1E-79-A8-12-46/127.0.0.8:443"
    )


@responses.activate
def test_set_redfish_password_already_set():
    responses.add(
        responses.PATCH,
        "https://127.0.0.1:443/redfish/v1/AccountService/Accounts/1",
        status=401,
        json=LENOVO_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "PASSWORD1"))],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Managers/1",
        match=[matchers.header_matcher(bmc_http_auth("USER1", "CONFIGMANAGERPASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(host=None)

    responses.add(
        responses.PATCH,
        "https://127.0.0.2:443/redfish/v1/AccountService/Accounts/root",
        status=401,
        json=BLUEFIELD_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER2", "PASSWORD2"))],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Managers/Bluefield_BMC",
        match=[matchers.header_matcher(bmc_http_auth("USER2", "CONFIGMANAGERPASSWORD2"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    result = set_redfish_password(activity_input)
    assert result == RedfishHostOutput(host=None)


@responses.activate
def test_set_redfish_password_unknown():
    responses.add(
        responses.PATCH,
        "https://127.0.0.1:443/redfish/v1/AccountService/Accounts/1",
        status=401,
        json=LENOVO_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "PASSWORD1"))],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Managers/1",
        status=401,
        json=LENOVO_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "CONFIGMANAGERPASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    with pytest.raises(ApplicationError) as error:
        set_redfish_password(activity_input)
    assert error.type is ApplicationError
    assert (
        error.value.args[0]
        == "Host Lenovo/C8-4B-D6-7A-E9-E2/127.0.0.1:443 has a non-default root password, "
        "please factory reset it and try again"
    )

    responses.add(
        responses.PATCH,
        "https://127.0.0.2:443/redfish/v1/AccountService/Accounts/root",
        status=401,
        json=BLUEFIELD_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER2", "PASSWORD2"))],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Managers/Bluefield_BMC",
        status=401,
        json=BLUEFIELD_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER2", "CONFIGMANAGERPASSWORD2"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    with pytest.raises(ApplicationError) as error:
        set_redfish_password(activity_input)
    assert error.type is ApplicationError
    assert (
        error.value.args[0]
        == "Host Nvidia/C8-4B-D6-7A-E8-F2/127.0.0.2:443 has a non-default root password, "
        "please factory reset it and try again"
    )


@responses.activate
def test_password_incorrect():
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/1",
        status=401,
        json=DELL_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "CONFIGMANAGERPASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    with pytest.raises(requests.exceptions.HTTPError) as error:
        power_on_host(activity_input)
    assert error.type is requests.exceptions.HTTPError
    assert error.value.args[0] == (
        "401 Client Error: Unauthorized for url: https://127.0.0.1:443/redfish/v1/Systems/1"
    )

    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield",
        status=401,
        json=DELL_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "CONFIGMANAGERPASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    with pytest.raises(requests.exceptions.HTTPError) as error:
        power_on_host(activity_input)
    assert error.type is requests.exceptions.HTTPError
    assert error.value.args[0] == (
        "401 Client Error: Unauthorized for url: https://127.0.0.2:443/redfish/v1/Systems/Bluefield"
    )

    responses.add(
        responses.GET,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1",
        status=401,
        json=DELL_UNAUTHORIZED_RESPONSE,
        match=[matchers.header_matcher(bmc_http_auth("USER1", "PASSWORD1"))],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    with pytest.raises(requests.exceptions.HTTPError) as error:
        power_on_host(activity_input)
    assert error.type is requests.exceptions.HTTPError
    assert error.value.args[0] == (
        "401 Client Error: Unauthorized for url: "
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1"
    )


@responses.activate
def test_power_on_host():
    def change_status_on_sleep(path, state):
        def _change_status_on_sleep(_):
            responses.replace(
                responses.GET,
                path,
                json=state,
            )

        return _change_status_on_sleep

    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/1",
        json={"PowerState": "Off"},
    )
    responses.add(
        responses.POST,
        "https://127.0.0.1:443/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )

    with patch(
        "time.sleep",
        change_status_on_sleep(
            "https://127.0.0.1:443/redfish/v1/Systems/1",
            {"PowerState": "On"},
        ),
    ):
        result = power_on_host(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )

    responses.add(
        responses.POST,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield",
        json={"PowerState": "Off"},
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    with patch(
        "time.sleep",
        change_status_on_sleep(
            "https://127.0.0.2:443/redfish/v1/Systems/Bluefield",
            {"PowerState": "On"},
        ),
    ):
        result = power_on_host(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )

    responses.add(
        responses.POST,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    responses.add(
        responses.GET,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1",
        json={"PowerState": "Off"},
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    with patch(
        "time.sleep",
        change_status_on_sleep(
            "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1",
            {"PowerState": "On"},
        ),
    ):
        result = power_on_host(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )


@responses.activate
@patch("time.sleep", return_value=None)
def test_power_on_host_already_on(mock_time):
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/1",
        json={"PowerState": "On"},
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    result = power_on_host(activity_input)
    assert result == RedfishHostOutput(host=None)

    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield",
        json={"PowerState": "On"},
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    result = power_on_host(activity_input)
    assert result == RedfishHostOutput(host=None)

    responses.add(
        responses.GET,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1",
        json={"PowerState": "On"},
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    result = power_on_host(activity_input)
    assert result == RedfishHostOutput(host=None)


@responses.activate
@patch("time.sleep", return_value=None)
def test_power_on_host_time_out(mock_time):
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/1",
        json={"PowerState": "Off"},
    )
    responses.add(
        responses.POST,
        "https://127.0.0.1:443/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    with pytest.raises(ApplicationError) as error:
        power_on_host(activity_input)

    assert error.type is ApplicationError
    assert (
        error.value.args[0]
        == "Timed out waiting for host https://127.0.0.1:443/redfish/v1 to power on"
    )

    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield",
        json={"PowerState": "Off"},
    )
    responses.add(
        responses.POST,
        "https://127.0.0.2:443/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    with pytest.raises(ApplicationError) as error:
        power_on_host(activity_input)

    assert error.type is ApplicationError
    assert (
        error.value.args[0]
        == "Timed out waiting for host https://127.0.0.2:443/redfish/v1 to power on"
    )

    responses.add(
        responses.GET,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1",
        json={"PowerState": "Off"},
    )
    responses.add(
        responses.POST,
        "https://127.0.0.3:443/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        match=[matchers.json_params_matcher({"ResetType": "On"})],
    )
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    with pytest.raises(ApplicationError) as error:
        power_on_host(activity_input)

    assert error.type is ApplicationError
    assert (
        error.value.args[0]
        == "Timed out waiting for host https://127.0.0.3:443/redfish/v1 to power on"
    )


@patch("time.sleep", return_value=None)
@responses.activate
def test_factory_reset_host(mock_time):
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/",
    )
    responses.add(
        responses.POST,
        "https://127.0.0.1:443/redfish/v1/Managers/1/Actions/Manager.ResetToDefaults",
        match=[matchers.json_params_matcher({"ResetType": "ResetAll"})],
    )

    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )
    result = factory_reset_bmc(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E2",
        )
    )

    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/",
    )
    responses.add(
        responses.POST,
        "https://127.0.0.2:443/redfish/v1/Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
        match=[matchers.json_params_matcher({"ResetToDefaultsType": "ResetAll"})],
        json=BLUEFIELD_FACTORY_RESET_RESPONSE,
    )

    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )
    result = factory_reset_bmc(activity_input)
    assert result == RedfishHostOutput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E8-F2",
        )
    )

    # Dell should not be factory reset
    activity_input = RedfishHostInput(
        host=RedfishHost(
            address="127.0.0.3",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="58-A2-E1-72-DD-C5",
        )
    )
    with pytest.raises(ApplicationError) as error:
        result = factory_reset_bmc(activity_input)
    assert error.type is ApplicationError
    assert error.value.args[0] == (
        "BMC factory reset should not be performed Dell: https://127.0.0.3:443/redfish/v1"
    )


@responses.activate
def test_get_server_details():
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters",
        json=DELL_NETWORK_ADAPTERS,
    )
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/System.Embedded.1",
        json=DELL_SYSTEM_INFO,
    )
    for adapter, adapter_data in DELL_NETWORK_ADAPTER_DETAILS.items():
        responses.add(
            responses.GET,
            f"https://127.0.0.1:443/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/{adapter}",
            json=adapter_data,
        )
        for network_function in DELL_NETWORK_ADAPTER_DETAILS[adapter]["Controllers"][0]["Links"][
            "NetworkDeviceFunctions"
        ]:
            network_function_name = network_function["@odata.id"].rsplit("/", maxsplit=1)[-1]
            responses.add(
                responses.GET,
                "https://127.0.0.1:443/redfish/v1/Chassis/System.Embedded.1/"
                f"NetworkAdapters/{adapter}/NetworkDeviceFunctions/{network_function_name}",
                json=DELL_NETWORK_FUNCTION_DETAIL[network_function_name],
            )

    activity_input = GetServerDetailsActivityInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E9-E2",
        ),
        nic_manufacturers=["Mellanox Technologies", "MLNX"],
    )
    result = get_server_details(activity_input)
    assert result == GetServerDetailsActivityOutput(
        server=RedfishServer(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.DELL,
            mac="C8-4B-D6-7A-E9-E2",
            serial="MXFC40025U00BL",
            nics=[
                RedfishNic(
                    slot="NIC.Slot.4",
                    serial=None,
                    name="NIC.Slot.4-2",
                    mac="58-A2-E1-72-DD-A1",
                ),
                RedfishNic(
                    slot="NIC.Slot.4",
                    serial=None,
                    name="NIC.Slot.4-1",
                    mac="58-A2-E1-72-DD-A0",
                ),
                RedfishNic(
                    slot="NIC.Slot.5",
                    serial=None,
                    name="NIC.Slot.5-2",
                    mac="58-A2-E1-72-B8-F7",
                ),
                RedfishNic(
                    slot="NIC.Slot.5",
                    serial=None,
                    name="NIC.Slot.5-1",
                    mac="58-A2-E1-72-B8-F6",
                ),
            ],
        )
    )

    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Systems/1",
        json=LENOVO_SYSTEM_INFO,
    )
    responses.add(
        responses.GET,
        "https://127.0.0.2:443/redfish/v1/Chassis/1/NetworkAdapters",
        json=LENOVO_NETWORK_ADAPTERS,
    )
    for adapter, adapter_data in LENOVO_NETWORK_ADAPTER_DETAILS.items():
        responses.add(
            responses.GET,
            f"https://127.0.0.2:443/redfish/v1/Chassis/1/NetworkAdapters/{adapter}",
            json=adapter_data,
        )
        for port in LENOVO_NETWORK_ADAPTER_DETAILS[adapter]["Controllers"][0]["Links"]["Ports"]:
            port_name = port["@odata.id"].rsplit("/", maxsplit=1)[-1]
            responses.add(
                responses.GET,
                "https://127.0.0.2:443/redfish/v1/Chassis/1/"
                f"NetworkAdapters/{adapter}/Ports/{port_name}",
                json=LENOVO_PORT_DETAILS[port_name],
            )

    activity_input = GetServerDetailsActivityInput(
        host=RedfishHost(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E3",
        ),
        nic_manufacturers=["Mellanox Technologies", "MLNX"],
    )
    result = get_server_details(activity_input)
    assert result == GetServerDetailsActivityOutput(
        server=RedfishServer(
            address="127.0.0.2",
            port=443,
            vendor=RedfishVendor.LENOVO,
            mac="C8-4B-D6-7A-E9-E3",
            serial="JZ00268C",
            nics=[
                RedfishNic(
                    slot="slot-2",
                    name="2",
                    mac="58-A2-E1-84-74-D7",
                )
            ],
        )
    )


@responses.activate
def test_get_dpu_details():
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Systems/Bluefield",
        json=BLUEFIELD_SYSTEM_INFO,
    )
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/UpdateService/FirmwareInventory/DPU_SYS_IMAGE",
        json=BLUEFIELD_SYS_INFO,
    )
    responses.add(
        responses.GET,
        "https://127.0.0.1:443/redfish/v1/Chassis/Card1",
        json=BLUEFIELD_CHASSIS,
    )
    activity_input = GetDpuDetailsActivityInput(
        host=RedfishHost(
            address="127.0.0.1",
            port=443,
            vendor=RedfishVendor.BLUEFIELD,
            mac="C8-4B-D6-7A-E9-E2",
        ),
    )
    result = get_dpu_details(activity_input)
    assert result == GetDpuDetailsActivityOutput(
        dpu=RedfishDpu(
            address="127.0.0.1",
            port=443,
            vendor="Nvidia",
            mac="C8-4B-D6-7A-E9-E2",
            ports=[
                RedfishDpuPort(name="eth0", mac="58-A2-E1-72-DD-B1"),
                RedfishDpuPort(name="eth1", mac="58-A2-E1-72-DD-B2"),
            ],
            base_mac="58-A2-E1-72-DD-A0",
            serial="MT2402XZ0EC9",
        )
    )


@pytest.mark.asyncio
async def test_update_dpu_data():
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"devices": [TEST_SERVERS[0]]}},
        )
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"device": TEST_DPU_DEVICES[0]}},
        )
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"device": TEST_DPU_DEVICES[1]}},
        )
        for device, dpu in zip(TEST_DPU_DEVICES, TEST_REDFISH_DPUS):
            response = copy.deepcopy(device)
            response["serial"] = dpu.serial
            m.patch(
                f"https://nautobot.example.com/api/dcim/devices/{device['id']}/",
                payload=response,
            )
            for device_interface, dpu_interface in zip(device["interfaces"][1:], dpu.ports):
                response = copy.deepcopy(device_interface)
                response["mac_address"] = dpu_interface.mac
                m.patch(
                    f"https://nautobot.example.com/api/dcim/interfaces/{device_interface['id']}/",
                    payload=response,
                )
        activity_input = UpdateDpuDataActivityInput(
            server=RedfishServer(
                address="10.180.166.60",
                port=443,
                vendor=RedfishVendor.DELL,
                mac="C8-4B-D6-7A-E9-E2",
                serial="MXFC40025U00BL",
                nics=[
                    RedfishNic(
                        slot="NIC.Slot.4",
                        serial=None,
                        name="NIC.Slot.4-1",
                        mac="58-A2-E1-72-DD-A0",
                        dpu=TEST_REDFISH_DPUS[0],
                    ),
                    RedfishNic(
                        slot="NIC.Slot.5",
                        serial=None,
                        name="NIC.Slot.5-1",
                        mac="58-A2-E1-72-DD-F0",
                        dpu=TEST_REDFISH_DPUS[1],
                    ),
                ],
            )
        )
        result = await update_dpu_data(activity_input=activity_input)
    assert result == UpdateDpuDataActivityOutput(
        device_data=[
            HostDeviceData(
                id="3046d89c-5758-404a-879d-004fbdb96dd9",
                name="rno1-m04-c10-server1-dpu1.lab1",
                role="gpu",
                site="RNO1-NVIDIA Config Manager-LAB",
                device_type="bluefield-3140",
                serial="MT2402XZ0EC9",
                device_bays=[],
                interfaces=[
                    InterfaceData(
                        name="DPU Port 1",
                        id="d29c23c5-ee99-4b1b-a3b7-242482817213",
                        host="rno1-m04-c10-server1-dpu1.lab1",
                        mac_address="58-A2-E1-72-DD-B1",
                        vrf_id=None,
                    ),
                    InterfaceData(
                        name="DPU Port 2",
                        id="336a0f83-d05e-46d3-92af-7b806733153f",
                        host="rno1-m04-c10-server1-dpu1.lab1",
                        mac_address="58-A2-E1-72-DD-B2",
                        vrf_id=None,
                    ),
                ],
            ),
            HostDeviceData(
                id="fff10e3c-05c8-4cb7-b4f4-636fa9060fd8",
                name="rno1-m04-c10-server1-dpu2.lab1",
                role="gpu",
                site="RNO1-NVIDIA Config Manager-LAB",
                device_type="bluefield-3140",
                serial="MT2402XZ0EC8",
                device_bays=[],
                interfaces=[
                    InterfaceData(
                        name="DPU Port 1",
                        id="ee1e0539-cec0-473e-b490-a792055a219d",
                        host="rno1-m04-c10-server1-dpu2.lab1",
                        mac_address="58-A2-E1-72-DE-01",
                        vrf_id=None,
                    ),
                    InterfaceData(
                        name="DPU Port 2",
                        id="88136029-2d9d-49a8-b820-3d6b884d544e",
                        host="rno1-m04-c10-server1-dpu2.lab1",
                        mac_address="58-A2-E1-72-DE-02",
                        vrf_id=None,
                    ),
                ],
            ),
        ]
    )
