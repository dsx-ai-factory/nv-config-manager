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
"""Compatibility contracts at the service/workflow device boundary."""

from configparser import ConfigParser
from unittest.mock import Mock, patch

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager.temporal.client import device as legacy
from nv_config_manager.temporal.client.device import base as legacy_base
from nv_config_manager.temporal.client.device import exceptions as legacy_exceptions
from nv_config_manager.temporal.client.device import factory as legacy_factory
from nv_config_manager.temporal.client.device import models as legacy_models
from nv_config_manager_workflows.clients import device as extracted
from nv_config_manager_workflows.clients.device import models as extracted_models


@pytest.mark.parametrize(
    "name",
    [
        "NetworkDeviceException",
        "DiffChangedException",
        "InvalidConfigException",
        "ConfigApplyFailureException",
        "ConfigSyntaxException",
        "DiffValidationError",
    ],
)
def test_exception_identity_is_shared(name):
    assert getattr(legacy, name) is getattr(extracted, name)
    assert getattr(legacy_exceptions, name) is getattr(extracted, name)


@pytest.mark.parametrize(
    "name",
    [
        "DeviceArpTable",
        "DeviceMacEntry",
        "DeviceMacTable",
        "DeviceNeighborData",
        "InterfaceNeighborData",
        "format_mac",
        "is_mac_address",
        "_append_unique",
    ],
)
def test_model_and_helper_identity_is_shared(name):
    assert getattr(legacy_models, name) is getattr(extracted_models, name)


@pytest.mark.parametrize(
    ("name", "port"),
    [
        ("AristaConnection", 443),
        ("CumulusConnection", 8765),
        ("NVOSConnection", 443),
        ("JuniperConnection", 830),
        ("MellanoxConnection", 22),
        ("MockNetworkConnection", 443),
    ],
)
def test_legacy_positional_constructor_and_inheritance(name, port):
    config = ConfigParser()
    # Direct constructors historically ignore factory-only mock configuration.
    config.read_dict({"device": {"mock": "invalid-boolean"}})
    with (
        patch("nv_config_manager.temporal.client.device.base.load_config", return_value=config),
        patch.object(extracted.AristaConnection, "_connect", return_value=Mock()),
    ):
        connection = getattr(legacy, name)("host", port, "user", "password", "Site A")
    assert isinstance(connection, legacy.NetworkConnection)
    assert isinstance(connection, getattr(extracted, name))
    assert connection._username == "user"
    assert connection._passwords_to_try == ["password"]
    if name == "NVOSConnection":
        assert isinstance(connection, legacy.CumulusConnection)
    connection.close()


@pytest.mark.parametrize(
    ("name", "default_port"),
    [
        ("AristaConnection", 443),
        ("CumulusConnection", 8765),
        ("NVOSConnection", 443),
        ("JuniperConnection", 830),
        ("MellanoxConnection", 22),
        ("MockNetworkConnection", 443),
    ],
)
def test_shared_constructor_ports_and_explicit_settings(name, default_port):
    settings = {"username": "user", "passwords": ["password"], "mock": False}
    with (
        patch.object(legacy_base, "load_config", side_effect=AssertionError("No INI lookup")),
        patch.object(extracted.AristaConnection, "_connect", return_value=Mock()),
    ):
        for kwargs, expected_port in [
            ({}, default_port),
            ({"port": None}, default_port),
            ({"port": 2222}, 2222),
            ({"port": 0}, 0),
        ]:
            connection = getattr(legacy, name)("host", settings=settings, **kwargs)
            assert connection._port == expected_port
            assert connection._username == "user"
            assert connection._passwords_to_try == ["password"]
            assert connection._passwords_to_try is not settings["passwords"]
            connection.close()


@pytest.mark.parametrize(
    "name",
    [
        "AristaConnection",
        "CumulusConnection",
        "NVOSConnection",
        "JuniperConnection",
        "MellanoxConnection",
        "MockNetworkConnection",
    ],
)
def test_shared_constructor_resolves_credentials_and_initializes_vendor_once(name):
    settings = {"username": "resolved-user", "passwords": ["resolved-password"], "mock": False}
    vendor = getattr(extracted, name)
    with (
        patch.object(legacy_base, "legacy_settings", return_value=settings) as resolve,
        patch.object(extracted.AristaConnection, "_connect", return_value=Mock()),
        patch.object(vendor, "__init__", autospec=True, side_effect=vendor.__init__) as vendor_init,
        patch.object(
            extracted.NetworkConnection,
            "__init__",
            autospec=True,
            side_effect=extracted.NetworkConnection.__init__,
        ) as base_init,
    ):
        connection = getattr(legacy, name)("host", 1234, "user", "password", "Site A")
        resolve.assert_called_once_with("user", "password", "Site A")
        assert vendor_init.call_count == base_init.call_count == 1
        assert connection._username == "resolved-user"
        assert connection._passwords_to_try == ["resolved-password"]
        if name == "NVOSConnection":
            assert isinstance(connection, legacy.CumulusConnection)
            assert connection._session is not None
            assert connection._base_url.startswith("https://host:1234")
        connection.close()


def test_service_base_still_requires_a_port():
    settings = {"username": "user", "passwords": [], "mock": False}
    with pytest.raises(TypeError, match="NetworkConnection requires a port"):
        legacy.NetworkConnection("host", settings=settings)
    assert legacy.NetworkConnection("host", 22, settings=settings)._port == 22


def test_factory_shim_preserves_its_config_loader_and_public_constructor_patch():
    config = ConfigParser()
    config.read_dict({"device": {"mock": "true"}})
    device = NetworkDeviceData.model_construct(
        platform=Platform.UFM, primary_ip4="192.0.2.10", site="Site A"
    )
    with (
        patch.object(legacy_factory, "load_config", return_value=config) as load,
        patch.object(legacy_base, "load_config", side_effect=AssertionError("Config was injected")),
        patch.object(legacy, "MockNetworkConnection") as constructor,
    ):
        assert legacy_factory.from_device_data(device) is constructor.return_value
        load.assert_called_once_with()
        constructor.assert_called_once_with(device.host, site="Site A")
