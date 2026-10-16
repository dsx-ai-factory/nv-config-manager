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
"""Explicit settings and platform dispatch for extracted device clients."""

import logging
from unittest.mock import Mock, patch

import pytest
import requests
from nv_config_manager_dcim.workflow_models import Platform

import nv_config_manager_workflows.clients.device as device_clients
import nv_config_manager_workflows.clients.device.factory as device_factory
from nv_config_manager_workflows.clients.device.arista import AristaConnection
from nv_config_manager_workflows.clients.device.cumulus import CumulusConnection, NVOSConnection
from nv_config_manager_workflows.clients.device.exceptions import NetworkDeviceException
from nv_config_manager_workflows.clients.device.factory import (
    DeviceConnectionClass,
    connection_class_for_platform,
)
from nv_config_manager_workflows.clients.device.juniper import JuniperConnection
from nv_config_manager_workflows.clients.device.mellanox import MellanoxConnection
from nv_config_manager_workflows.clients.device.mock import MockNetworkConnection
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings


@pytest.mark.parametrize(
    ("platform", "connection_cls", "port"),
    [
        (Platform.ARISTA_EOS, AristaConnection, 443),
        (Platform.CUMULUS_LINUX, CumulusConnection, 8765),
        (Platform.NV_OS, NVOSConnection, 443),
        (Platform.MLNX_OS, MellanoxConnection, 22),
        (Platform.JUNIPER_JUNOS, JuniperConnection, 830),
    ],
)
def test_platform_constructs_from_explicit_settings(
    platform: Platform,
    connection_cls: DeviceConnectionClass,
    port: int,
) -> None:
    settings: DeviceConnectionSettings = {
        "username": "user",
        "passwords": ["new", "old"],
        "mock": False,
    }
    selected_class = connection_class_for_platform(platform, mock=False)

    assert selected_class is connection_cls
    with patch.object(AristaConnection, "_connect", return_value=Mock()):
        connection = selected_class("192.0.2.10", settings=settings)

    assert type(connection) is connection_cls
    assert connection._host == "192.0.2.10"
    assert connection._port == port
    assert connection._username == settings["username"]
    assert connection._passwords_to_try == settings["passwords"]
    assert connection._passwords_to_try is not settings["passwords"]
    connection.close()


@pytest.mark.parametrize("platform", list(Platform))
def test_mock_selection_precedes_platform_dispatch(platform: Platform) -> None:
    assert connection_class_for_platform(platform, mock=True) is MockNetworkConnection


def test_ufm_remains_unsupported() -> None:
    with pytest.raises(NotImplementedError, match="use UFMClient"):
        connection_class_for_platform(Platform.UFM, mock=False)


def test_package_initializer_does_not_reexport_implementations() -> None:
    assert not hasattr(device_clients, "connection_class_for_platform")
    assert not hasattr(device_clients, "AristaConnection")
    assert not hasattr(device_clients, "from_device_data")
    assert not hasattr(device_factory, "from_device_data")


def test_http_failure_does_not_log_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response_secret = "response-secret-sentinel"
    connection = CumulusConnection(
        "host",
        settings={"username": "user", "passwords": [], "mock": False},
    )
    response = Mock(status_code=500, text=response_secret)

    with (
        patch.object(connection, "_make_request", return_value=response),
        caplog.at_level(logging.DEBUG),
        pytest.raises(NetworkDeviceException, match=response_secret),
    ):
        connection.get("https://host/path")

    assert response_secret not in caplog.text
    assert "Device HTTP GET failed for host with status 500" in caplog.text
    connection.close()


def test_http_timeout_does_not_log_parameters_or_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parameter_secret = "parameter-secret-sentinel"
    exception_secret = "exception-secret-sentinel"
    connection = CumulusConnection(
        "host",
        settings={"username": "user", "passwords": [], "mock": False},
    )

    with (
        patch.object(
            connection,
            "_make_request",
            side_effect=requests.exceptions.Timeout(exception_secret),
        ),
        caplog.at_level(logging.DEBUG),
        pytest.raises(NetworkDeviceException, match=parameter_secret),
    ):
        connection.get("https://host/path", params={"value": parameter_secret})

    assert parameter_secret not in caplog.text
    assert exception_secret not in caplog.text
    assert "Device HTTP GET timed out for host" in caplog.text
    connection.close()
