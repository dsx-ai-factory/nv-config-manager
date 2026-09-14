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

from configparser import ConfigParser
from unittest.mock import patch

import pytest
from nv_config_manager_dcim.workflow_models import Platform

from nv_config_manager.temporal.client.device import (
    CumulusConnection,
    JuniperConnection,
    MockNetworkConnection,
    NetworkConnection,
)
from nv_config_manager.temporal.client.device.factory import from_device_data
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager_workflows.clients.device.juniper import (
    JuniperConnection as WorkflowJuniperConnection,
)

_CUMULUS_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-switch",
    role="tor-switch",
    platform=Platform.CUMULUS_LINUX,
    site="SITEA",
    device_type="sn5600",
    primary_ip4="192.0.2.100",
    primary_ip6=None,
)

_JUNIPER_DEVICE = NetworkDeviceData(
    id="a1b2c3d4-1111-2222-3333-444455556666",
    name="test-router",
    role="backbone-router",
    platform=Platform.JUNIPER_JUNOS,
    site="SITEA",
    device_type="ptx10002-36qdd",
    primary_ip4="192.0.2.10",
    primary_ip6=None,
)

_UFM_DEVICE = NetworkDeviceData(
    id="b2c3d4e5-1111-2222-3333-444455556666",
    name="test-ufm",
    role="ufm",
    platform=Platform.UFM,
    site="SITEA",
    device_type="ufm",
    primary_ip4="192.0.2.50",
    primary_ip6=None,
)


def _mock_config(*, mock: bool | None = False) -> ConfigParser:
    config = ConfigParser()
    config.add_section("device")
    config.set("device", "username", "admin")
    if mock is not None:
        config.set("device", "mock", "true" if mock else "false")
    return config


@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_mock_when_config_mock_true(mock_base_load):
    """Config with [device] mock = true → from_device_data() returns MockNetworkConnection."""
    config = _mock_config(mock=True)
    mock_base_load.return_value = config
    conn = from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, MockNetworkConnection)


@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_cumulus_when_mock_false(mock_base_load):
    """Config with [device] mock = false + cumulus-linux platform → returns CumulusConnection."""
    config = _mock_config(mock=False)
    mock_base_load.return_value = config
    conn = from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, CumulusConnection)


@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_juniper_when_mock_false(mock_base_load):
    """Config with mock = false + juniper-junos platform → JuniperConnection on the NETCONF port."""
    config = _mock_config(mock=False)
    mock_base_load.return_value = config
    conn = from_device_data(_JUNIPER_DEVICE, config=config)
    assert isinstance(conn, JuniperConnection)
    assert conn._port == 830


@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_selects_platform_when_mock_option_missing(mock_base_load):
    """A [device] section without mock continues with normal platform selection."""
    config = _mock_config(mock=None)
    mock_base_load.return_value = config
    conn = from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, CumulusConnection)


@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_rejects_ufm_platform(mock_base_load):
    """UFM is inventoried as a platform but is not a NetworkConnection."""
    config = _mock_config(mock=False)
    mock_base_load.return_value = config
    with pytest.raises(NotImplementedError, match="use UFMClient"):
        from_device_data(_UFM_DEVICE, config=config)


@pytest.mark.parametrize(
    ("platform", "constructor_name"),
    [
        (Platform.ARISTA_EOS, "AristaConnection"),
        (Platform.CUMULUS_LINUX, "CumulusConnection"),
        (Platform.NV_OS, "NVOSConnection"),
        (Platform.MLNX_OS, "MellanoxConnection"),
        (Platform.JUNIPER_JUNOS, "JuniperConnection"),
    ],
)
def test_factory_selects_vendor_adapter(platform, constructor_name):
    device = _CUMULUS_DEVICE.model_copy(update={"platform": platform})
    config = _mock_config()
    with patch(
        f"nv_config_manager.temporal.client.device.factory.{constructor_name}"
    ) as constructor:
        assert from_device_data(device, config=config) is constructor.return_value
    constructor.assert_called_once_with(device.host, site=device.site, config=config)


def test_legacy_wrapper_delegates_to_factory():
    with patch("nv_config_manager.temporal.client.device.factory.from_device_data") as factory:
        assert NetworkConnection.from_device_data(_CUMULUS_DEVICE) is factory.return_value
    factory.assert_called_once_with(_CUMULUS_DEVICE, config=None)


def test_factory_uses_shared_selectors_result():
    """The shared selector owns platform policy; the service only adapts its result."""
    config = _mock_config()
    with (
        patch(
            "nv_config_manager.temporal.client.device.factory.connection_class_for_platform",
            return_value=WorkflowJuniperConnection,
        ) as select,
        patch("nv_config_manager.temporal.client.device.factory.JuniperConnection") as constructor,
    ):
        assert from_device_data(_CUMULUS_DEVICE, config=config) is constructor.return_value
    select.assert_called_once_with(Platform.CUMULUS_LINUX, mock=False)
    constructor.assert_called_once_with(
        _CUMULUS_DEVICE.host, site=_CUMULUS_DEVICE.site, config=config
    )


def test_factory_loads_config_when_not_injected():
    config = _mock_config(mock=True)
    config.set("device", "password", "configured-password")
    with (
        patch(
            "nv_config_manager.temporal.client.device.factory.load_config",
            return_value=config,
        ) as load,
        patch(
            "nv_config_manager.temporal.client.device.base.load_config",
            side_effect=AssertionError("Configuration must only be loaded once"),
        ),
    ):
        connection = from_device_data(_CUMULUS_DEVICE)
    load.assert_called_once_with()
    assert isinstance(connection, MockNetworkConnection)
    assert connection._username == "admin"
    assert connection._passwords_to_try == ["configured-password"]
    connection.close()


@pytest.mark.parametrize("mock", [False, True])
def test_factory_uses_injected_credentials_without_loading_global_config(mock):
    config = _mock_config(mock=mock)
    config.set("device", "username", "injected-user")
    config.set("device", "password", "injected-password")
    with (
        patch(
            "nv_config_manager.temporal.client.device.factory.load_config",
            side_effect=AssertionError("Configuration was injected"),
        ),
        patch(
            "nv_config_manager.temporal.client.device.base.load_config",
            side_effect=AssertionError("Configuration was injected"),
        ),
    ):
        connection = from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(connection, MockNetworkConnection if mock else CumulusConnection)
    assert connection._username == "injected-user"
    assert connection._passwords_to_try == ["injected-password"]
    connection.close()
