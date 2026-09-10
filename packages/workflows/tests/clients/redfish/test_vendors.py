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
"""Tests for vendor-specific Redfish behavior."""

from unittest.mock import MagicMock, call, patch

import pytest
import requests
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection,
    DellRedfishConnection,
    LenovoRedfishConnection,
    RedfishConnection,
    RedfishHost,
    RedfishNic,
    RedfishVendor,
)


def _connection(
    connection_type: type[RedfishConnection],
    vendor: RedfishVendor,
) -> RedfishConnection:
    return connection_type(
        host=RedfishHost(address="192.0.2.10", vendor=vendor),
        username="explicit-user",
        password="explicit-password",
        config_manager_password="explicit-managed-password",
    )


def _response(payload: dict[str, object] | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.ok = True
    response.json.return_value = payload or {}
    return response


@pytest.mark.parametrize(
    ("connection_type", "vendor", "account_path"),
    [
        (LenovoRedfishConnection, RedfishVendor.LENOVO, "AccountService/Accounts/1"),
        (
            Bluefield3RedfishConnection,
            RedfishVendor.BLUEFIELD,
            "AccountService/Accounts/root",
        ),
    ],
)
def test_password_rotation_uses_explicit_password_and_vendor_path(
    connection_type: type[RedfishConnection],
    vendor: RedfishVendor,
    account_path: str,
) -> None:
    connection = _connection(connection_type, vendor)
    response = _response()

    with patch.object(connection, "patch", return_value=response) as request:
        result = connection.set_config_manager_password()

    assert result is response
    request.assert_called_once_with(
        path=account_path,
        payload={"Password": "explicit-managed-password"},
    )
    response.raise_for_status.assert_called_once_with()
    assert connection.password == "explicit-managed-password"


def test_dell_rejects_password_rotation_and_factory_reset() -> None:
    connection = _connection(DellRedfishConnection, RedfishVendor.DELL)

    with pytest.raises(
        ApplicationError,
        match=("BMC password should not be changed for Dell: https://192.0.2.10:443/redfish/v1"),
    ):
        connection.set_config_manager_password()

    with pytest.raises(
        ApplicationError,
        match=("BMC factory reset should not be performed Dell: https://192.0.2.10:443/redfish/v1"),
    ):
        connection.factory_reset()


@pytest.mark.parametrize(
    ("connection_type", "vendor", "system_path"),
    [
        (LenovoRedfishConnection, RedfishVendor.LENOVO, "Systems/1"),
        (Bluefield3RedfishConnection, RedfishVendor.BLUEFIELD, "Systems/Bluefield"),
        (DellRedfishConnection, RedfishVendor.DELL, "Systems/System.Embedded.1"),
    ],
)
@pytest.mark.parametrize(
    ("method_name", "reset_type", "waits_for_power"),
    [
        ("power_on_chassis", "On", True),
        ("power_off_chassis", "GracefulShutdown", False),
    ],
)
def test_power_actions_preserve_vendor_paths_and_payloads(
    connection_type: type[RedfishConnection],
    vendor: RedfishVendor,
    system_path: str,
    method_name: str,
    reset_type: str,
    waits_for_power: bool,
) -> None:
    connection = _connection(connection_type, vendor)
    response = _response()

    with (
        patch.object(connection, "post", return_value=response) as request,
        patch.object(connection, "wait_for_power_on") as wait_for_power_on,
    ):
        result = getattr(connection, method_name)()

    assert result is response
    request.assert_called_once_with(
        path=f"{system_path}/Actions/ComputerSystem.Reset",
        payload={"ResetType": reset_type},
    )
    response.raise_for_status.assert_called_once_with()
    assert wait_for_power_on.call_count == int(waits_for_power)


@pytest.mark.parametrize(
    (
        "connection_type",
        "vendor",
        "manager_path",
        "manager_timeout",
        "system_path",
        "chassis_path",
        "expected_serial",
    ),
    [
        (
            LenovoRedfishConnection,
            RedfishVendor.LENOVO,
            "Managers/1",
            None,
            "Systems/1",
            "Chassis/1",
            " SERIAL ",
        ),
        (
            Bluefield3RedfishConnection,
            RedfishVendor.BLUEFIELD,
            "Managers/Bluefield_BMC",
            30,
            "Systems/Bluefield",
            "Chassis/Card1",
            "SERIAL",
        ),
        (
            DellRedfishConnection,
            RedfishVendor.DELL,
            "Managers/iDRAC.Embedded.1",
            None,
            "Systems/System.Embedded.1",
            "Chassis/System.Embedded.1",
            " SERIAL ",
        ),
    ],
)
def test_vendor_read_paths_are_unchanged(
    connection_type: type[RedfishConnection],
    vendor: RedfishVendor,
    manager_path: str,
    manager_timeout: int | None,
    system_path: str,
    chassis_path: str,
    expected_serial: str,
) -> None:
    connection = _connection(connection_type, vendor)
    response = _response({"PowerState": "On", "SerialNumber": " SERIAL "})

    with patch.object(connection, "get", return_value=response) as request:
        assert connection.get_redfish_data() is response
        expected_manager_call = (
            call(path=manager_path, timeout=manager_timeout)
            if manager_timeout is not None
            else call(path=manager_path)
        )
        assert request.call_args == expected_manager_call

        request.reset_mock()
        assert connection.is_host_powered_on() is True
        request.assert_called_once_with(path=system_path)

        request.reset_mock()
        assert connection.get_chassis() is response
        request.assert_called_once_with(path=chassis_path)

        request.reset_mock()
        assert connection.get_serial() == expected_serial
        request.assert_called_once_with(path=system_path)


@pytest.mark.parametrize(
    ("connection_type", "vendor"),
    [
        (LenovoRedfishConnection, RedfishVendor.LENOVO),
        (Bluefield3RedfishConnection, RedfishVendor.BLUEFIELD),
        (DellRedfishConnection, RedfishVendor.DELL),
    ],
)
def test_vendor_http_errors_are_propagated(
    connection_type: type[RedfishConnection],
    vendor: RedfishVendor,
) -> None:
    connection = _connection(connection_type, vendor)
    response = _response()
    error = requests.HTTPError("unauthorized")
    response.raise_for_status.side_effect = error

    with (
        patch.object(connection, "get", return_value=response),
        pytest.raises(requests.HTTPError) as raised,
    ):
        connection.get_redfish_data()

    assert raised.value is error


def test_lenovo_factory_reset_preserves_path_payload_and_password_state() -> None:
    connection = _connection(LenovoRedfishConnection, RedfishVendor.LENOVO)
    connection.password = "changed-password"
    response = _response()

    with (
        patch.object(connection, "post", return_value=response) as request,
        patch.object(connection, "wait_for_restart") as wait_for_restart,
    ):
        result = connection.factory_reset()

    assert result is response
    request.assert_called_once_with(
        path="Managers/1/Actions/Manager.ResetToDefaults",
        payload={"ResetType": "ResetAll"},
    )
    wait_for_restart.assert_called_once_with()
    assert connection.password == "explicit-password"


@pytest.mark.parametrize("post_fails", [False, True])
def test_bluefield_factory_reset_preserves_path_payload_and_error_handling(
    post_fails: bool,
) -> None:
    connection = _connection(Bluefield3RedfishConnection, RedfishVendor.BLUEFIELD)
    connection.password = "changed-password"
    response = _response()
    side_effect = requests.RequestException("connection closed") if post_fails else None

    with (
        patch.object(
            connection,
            "post",
            return_value=response,
            side_effect=side_effect,
        ) as request,
        patch.object(connection, "wait_for_restart") as wait_for_restart,
    ):
        result = connection.factory_reset()

    assert result is (None if post_fails else response)
    request.assert_called_once_with(
        path="Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults",
        payload={"ResetToDefaultsType": "ResetAll"},
    )
    wait_for_restart.assert_called_once_with()
    assert connection.password == "explicit-password"


def test_lenovo_nic_discovery_preserves_paths_and_deduplicates_ports() -> None:
    connection = _connection(LenovoRedfishConnection, RedfishVendor.LENOVO)
    responses = [
        _response({"Members": [{"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/slot-1"}]}),
        _response(
            {
                "Manufacturer": "NVIDIA",
                "Controllers": [
                    {
                        "Links": {
                            "Ports": [
                                {"@odata.id": "/redfish/v1/Ports/1"},
                                {"@odata.id": "/redfish/v1/Ports/1"},
                            ]
                        }
                    }
                ],
            }
        ),
        _response({"Oem": {"Lenovo": {"PhysicalPortMacAddress": "58:a2:e1:72:dd:b1"}}}),
    ]

    with patch.object(connection, "get", side_effect=responses) as request:
        result = connection.get_nic_info(["NVIDIA"])

    assert result == [RedfishNic(name="1", slot="slot-1", mac="58-A2-E1-72-DD-B1")]
    assert request.call_args_list == [
        call(path="Chassis/1/NetworkAdapters"),
        call(path="Chassis/1/NetworkAdapters/slot-1"),
        call(path="Chassis/1/NetworkAdapters/slot-1/Ports/1"),
    ]


def test_dell_nic_discovery_preserves_paths_and_deduplicates_functions() -> None:
    connection = _connection(DellRedfishConnection, RedfishVendor.DELL)
    responses = [
        _response(
            {
                "Members": [
                    {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.1"}
                ]
            }
        ),
        _response(
            {
                "Manufacturer": "NVIDIA",
                "Controllers": [
                    {
                        "Links": {
                            "NetworkDeviceFunctions": [
                                {"@odata.id": "/redfish/v1/NetworkDeviceFunctions/1"},
                                {"@odata.id": "/redfish/v1/NetworkDeviceFunctions/1"},
                            ]
                        }
                    }
                ],
            }
        ),
        _response({"Ethernet": {"MACAddress": "58:a2:e1:72:dd:b1"}}),
    ]

    with patch.object(connection, "get", side_effect=responses) as request:
        result = connection.get_nic_info(["NVIDIA"])

    assert result == [RedfishNic(name="1", slot="NIC.1", mac="58-A2-E1-72-DD-B1")]
    assert request.call_args_list == [
        call(path="Chassis/System.Embedded.1/NetworkAdapters"),
        call(path="Chassis/System.Embedded.1/NetworkAdapters/NIC.1"),
        call(path=("Chassis/System.Embedded.1/NetworkAdapters/NIC.1/NetworkDeviceFunctions/1")),
    ]


def test_bluefield_specific_resource_paths_and_base_mac() -> None:
    connection = _connection(Bluefield3RedfishConnection, RedfishVendor.BLUEFIELD)
    response = _response({"Version": "58a2:e103:0072:dda0"})

    with patch.object(connection, "get", return_value=response) as request:
        assert connection.get_network_device_functions() is response
        request.assert_called_once_with(
            path="Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions"
        )

        request.reset_mock()
        assert connection.get_network_device_function_details("eth0") is response
        request.assert_called_once_with(
            path=("Chassis/Card1/NetworkAdapters/NvidiaNetworkAdapter/NetworkDeviceFunctions/eth0")
        )

        request.reset_mock()
        assert connection.get_base_mac() == "58-A2-E1-72-DD-A0"
        request.assert_called_once_with(path="UpdateService/FirmwareInventory/DPU_SYS_IMAGE")

    with pytest.raises(NotImplementedError):
        connection.get_nic_info()
