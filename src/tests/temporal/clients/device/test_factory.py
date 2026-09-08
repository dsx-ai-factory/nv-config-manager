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

from nv_config_manager.temporal.client.device import (
    CumulusConnection,
    JuniperConnection,
    MockNetworkConnection,
    NetworkConnection,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData

_CUMULUS_DEVICE = NetworkDeviceData(
    id="c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f",
    name="test-switch",
    role="tor-switch",
    platform="cumulus-linux",
    site="SITEA",
    device_type="sn5600",
    primary_ip4="192.0.2.100",
    primary_ip6=None,
)

_JUNIPER_DEVICE = NetworkDeviceData(
    id="a1b2c3d4-1111-2222-3333-444455556666",
    name="test-router",
    role="backbone-router",
    platform="juniper-junos",
    site="SITEA",
    device_type="ptx10002-36qdd",
    primary_ip4="192.0.2.10",
    primary_ip6=None,
)

_UFM_DEVICE = NetworkDeviceData(
    id="b2c3d4e5-1111-2222-3333-444455556666",
    name="test-ufm",
    role="ufm",
    platform="ufm",
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


@patch("nv_config_manager.temporal.client.device.factory.load_config")
@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_mock_when_config_mock_true(mock_base_load, mock_factory_load):
    """Config with [device] mock = true → from_device_data() returns MockNetworkConnection."""
    config = _mock_config(mock=True)
    mock_factory_load.return_value = config
    mock_base_load.return_value = config
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE)
    assert isinstance(conn, MockNetworkConnection)


@patch("nv_config_manager.temporal.client.device.factory.load_config")
@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_cumulus_when_mock_false(mock_base_load, mock_factory_load):
    """Config with [device] mock = false + cumulus-linux platform → returns CumulusConnection."""
    config = _mock_config(mock=False)
    mock_factory_load.return_value = config
    mock_base_load.return_value = config
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE)
    assert isinstance(conn, CumulusConnection)


@patch("nv_config_manager.temporal.client.device.factory.load_config")
@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_returns_juniper_when_mock_false(mock_base_load, mock_factory_load):
    """Config with mock = false + juniper-junos platform → JuniperConnection on the NETCONF port."""
    config = _mock_config(mock=False)
    mock_factory_load.return_value = config
    mock_base_load.return_value = config
    conn = NetworkConnection.from_device_data(_JUNIPER_DEVICE)
    assert isinstance(conn, JuniperConnection)
    assert conn._port == 830


@patch("nv_config_manager.temporal.client.device.factory.load_config")
@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_selects_platform_when_mock_option_missing(
    mock_base_load, mock_factory_load
):
    """A [device] section without mock continues with normal platform selection."""
    config = _mock_config(mock=None)
    mock_factory_load.return_value = config
    mock_base_load.return_value = config
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE)
    assert isinstance(conn, CumulusConnection)


@patch("nv_config_manager.temporal.client.device.factory.load_config")
@patch("nv_config_manager.temporal.client.device.base.load_config")
def test_from_device_data_rejects_ufm_platform(mock_base_load, mock_factory_load):
    """UFM is inventoried as a platform but is not a NetworkConnection."""
    config = _mock_config(mock=False)
    mock_factory_load.return_value = config
    mock_base_load.return_value = config
    with pytest.raises(NotImplementedError, match="use UFMClient"):
        NetworkConnection.from_device_data(_UFM_DEVICE)
