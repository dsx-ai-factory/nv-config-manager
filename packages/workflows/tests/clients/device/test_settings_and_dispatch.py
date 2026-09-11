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
"""Explicit construction and credential-safe authentication across platforms."""

import logging
from unittest.mock import Mock, patch

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager_workflows.clients.device import (
    AristaConnection,
    CumulusConnection,
    JuniperConnection,
    MellanoxConnection,
    MockNetworkConnection,
    NetworkConnection,
    NetworkDeviceException,
    NVOSConnection,
    from_device_data,
)


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
def test_factory_constructs_platform_from_explicit_settings(platform, connection_cls, port):
    device = NetworkDeviceData.model_validate(
        {
            "id": "a1b2c3d4-1111-2222-3333-444455556666",
            "name": "switch",
            "role": "tor-switch",
            "platform": platform,
            "site": "Site A",
            "device_type": "switch",
            "primary_ip4": "192.0.2.10",
            "primary_ip6": None,
        }
    )
    settings = {"username": "user-sentinel", "passwords": ["new", "old"], "mock": False}
    with patch.object(AristaConnection, "_connect", return_value=Mock()):
        connection = from_device_data(device, settings)
    assert type(connection) is connection_cls
    assert connection._host == device.host
    assert connection._port == port
    assert connection._username == settings["username"]
    assert connection._passwords_to_try == settings["passwords"]
    assert connection._passwords_to_try is not settings["passwords"]
    connection.close()


@pytest.mark.parametrize("platform", list(Platform))
def test_mock_selection_precedes_platform_dispatch(platform):
    device = NetworkDeviceData.model_construct(platform=platform, primary_ip4="192.0.2.10")
    connection = NetworkConnection.from_device_data(
        device, {"username": "user", "passwords": [], "mock": True}
    )
    assert type(connection) is MockNetworkConnection


def test_ufm_preserves_unsupported_error():
    device = NetworkDeviceData.model_construct(platform=Platform.UFM)
    with pytest.raises(NotImplementedError, match="use UFMClient"):
        from_device_data(device, {"username": "user", "passwords": [], "mock": False})


def test_rotation_caches_success_without_logging_credentials(caplog):
    username, newest, older = "username-sentinel", "new-password-sentinel", "old-password-sentinel"
    connection = NetworkConnection(
        "host", 22, settings={"username": username, "passwords": [newest, older], "mock": False}
    )
    callback = Mock(side_effect=[ValueError(f"{username}: {newest}"), "connected"])
    with caplog.at_level(logging.DEBUG):
        assert connection._try_passwords_with_callback(callback, (ValueError,)) == "connected"
    assert [call.args[0] for call in callback.call_args_list] == [newest, older]
    assert connection._get_passwords_to_try() == [older, newest]
    for credential in (username, newest, older):
        assert credential not in caplog.text


def test_all_passwords_fail_preserves_exception_cause(caplog):
    connection = NetworkConnection(
        "host",
        22,
        settings={"username": "username-sentinel", "passwords": ["secret-sentinel"], "mock": False},
    )
    failure = ValueError("secret-sentinel")
    with caplog.at_level(logging.DEBUG), pytest.raises(NetworkDeviceException) as caught:
        connection._try_passwords_with_callback(Mock(side_effect=failure), (ValueError,))
    assert caught.value.__cause__ is failure
    assert "secret-sentinel" not in caplog.text
    assert "username-sentinel" not in caplog.text


def test_http_failure_does_not_log_response_secrets(caplog):
    connection = CumulusConnection(
        "host", settings={"username": "user", "passwords": [], "mock": False}
    )
    response = Mock(status_code=500, text="response-secret-sentinel")
    with patch.object(connection, "_make_request", return_value=response):
        with pytest.raises(NetworkDeviceException, match="response-secret-sentinel"):
            connection.get("https://host/path")
    assert "response-secret-sentinel" not in caplog.text
    connection.close()
