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

from nv_config_manager.temporal.client import device as service_device

# isort: off
from nv_config_manager.temporal.client.device import (
    AristaConnection,
    CumulusConnection,
    JuniperConnection,
    MellanoxConnection,
    MockNetworkConnection,
    NetworkConnection,
    NVOSConnection,
    arista as service_arista,
    base as service_base,
    cumulus as service_cumulus,
    juniper as service_juniper,
    mellanox as service_mellanox,
    mock as service_mock,
)
# isort: on

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager_workflows.clients.device.arista import (
    AristaConnection as WorkflowAristaConnection,
)
from nv_config_manager_workflows.clients.device.base import COMMIT_CONFIRM_ROLLBACK_SECONDS

# isort: off
from nv_config_manager_workflows.clients.device.cumulus import (
    CumulusConnection as WorkflowCumulusConnection,
    NVOSConnection as WorkflowNVOSConnection,
)
# isort: on

from nv_config_manager_workflows.clients.device.juniper import (
    JuniperConnection as WorkflowJuniperConnection,
)
from nv_config_manager_workflows.clients.device.mellanox import (
    MellanoxConnection as WorkflowMellanoxConnection,
)
from nv_config_manager_workflows.clients.device.mock import (
    MockNetworkConnection as WorkflowMockNetworkConnection,
)

_EXPECTED_PUBLIC_EXPORTS = [
    "COMMIT_CONFIRM_ROLLBACK_SECONDS",
    "AristaConnection",
    "ConfigApplyFailureException",
    "ConfigSyntaxException",
    "CumulusConnection",
    "DeviceArpTable",
    "DeviceMacEntry",
    "DeviceMacTable",
    "DeviceNeighborData",
    "DiffChangedException",
    "DiffValidationError",
    "InterfaceNeighborData",
    "InvalidConfigException",
    "JuniperConnection",
    "MellanoxConnection",
    "MockNetworkConnection",
    "NVOSConnection",
    "NetworkConnection",
    "NetworkDeviceData",
    "NetworkDeviceException",
    "format_mac",
    "is_mac_address",
]

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


def test_service_package_public_exports_match_main() -> None:
    assert service_device.__all__ == _EXPECTED_PUBLIC_EXPORTS


@pytest.mark.parametrize(
    ("module", "name", "public_class", "workflow_class"),
    [
        (service_arista, "AristaConnection", AristaConnection, WorkflowAristaConnection),
        (
            service_cumulus,
            "CumulusConnection",
            CumulusConnection,
            WorkflowCumulusConnection,
        ),
        (service_cumulus, "NVOSConnection", NVOSConnection, WorkflowNVOSConnection),
        (
            service_juniper,
            "JuniperConnection",
            JuniperConnection,
            WorkflowJuniperConnection,
        ),
        (
            service_mellanox,
            "MellanoxConnection",
            MellanoxConnection,
            WorkflowMellanoxConnection,
        ),
        (
            service_mock,
            "MockNetworkConnection",
            MockNetworkConnection,
            WorkflowMockNetworkConnection,
        ),
    ],
)
def test_connection_adapters_are_exported_through_remaining_submodules(
    module: object,
    name: str,
    public_class: type[NetworkConnection],
    workflow_class: type[object],
) -> None:
    assert getattr(module, name) is public_class
    assert issubclass(public_class, service_base.NetworkConnection)
    assert issubclass(public_class, workflow_class)


def test_shared_compatibility_exports_are_canonical() -> None:
    assert service_device.COMMIT_CONFIRM_ROLLBACK_SECONDS == COMMIT_CONFIRM_ROLLBACK_SECONDS
    assert service_device.NetworkDeviceData is NetworkDeviceData


def test_base_factory_entry_point_delegates_to_service_factory() -> None:
    config = _mock_config(mock=False)
    expected = object()
    with patch(
        "nv_config_manager.temporal.client.device.factory.from_device_data",
        return_value=expected,
    ) as service_factory:
        result = NetworkConnection.from_device_data(_CUMULUS_DEVICE, config=config)

    assert result is expected
    service_factory.assert_called_once_with(_CUMULUS_DEVICE, config=config)


def test_from_device_data_returns_mock_when_config_mock_true():
    """Config with [device] mock = true → from_device_data() returns MockNetworkConnection."""
    config = _mock_config(mock=True)
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, MockNetworkConnection)


def test_from_device_data_returns_cumulus_when_mock_false():
    """Config with [device] mock = false + cumulus-linux platform → returns CumulusConnection."""
    config = _mock_config(mock=False)
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, CumulusConnection)


def test_from_device_data_returns_juniper_when_mock_false():
    """Config with mock = false + juniper-junos platform → JuniperConnection on the NETCONF port."""
    config = _mock_config(mock=False)
    conn = NetworkConnection.from_device_data(_JUNIPER_DEVICE, config=config)
    assert isinstance(conn, JuniperConnection)
    assert conn._port == 830


def test_from_device_data_selects_platform_when_mock_option_missing():
    """A [device] section without mock continues with normal platform selection."""
    config = _mock_config(mock=None)
    conn = NetworkConnection.from_device_data(_CUMULUS_DEVICE, config=config)
    assert isinstance(conn, CumulusConnection)


@pytest.mark.parametrize(
    ("platform", "expected_class"),
    [
        (Platform.ARISTA_EOS, AristaConnection),
        (Platform.CUMULUS_LINUX, CumulusConnection),
        (Platform.NV_OS, NVOSConnection),
        (Platform.MLNX_OS, MellanoxConnection),
        (Platform.JUNIPER_JUNOS, JuniperConnection),
    ],
)
def test_from_device_data_maps_every_workflow_selection_to_service_adapter(
    platform: Platform,
    expected_class: type[NetworkConnection],
) -> None:
    device = _CUMULUS_DEVICE.model_copy(update={"platform": platform})

    with patch.object(AristaConnection, "_connect"):
        connection = NetworkConnection.from_device_data(
            device,
            config=_mock_config(mock=False),
        )

    assert isinstance(connection, expected_class)


def test_from_device_data_mock_mode_precedes_ufm_rejection() -> None:
    connection = NetworkConnection.from_device_data(
        _UFM_DEVICE,
        config=_mock_config(mock=True),
    )

    assert isinstance(connection, MockNetworkConnection)


def test_from_device_data_loads_service_config_when_not_injected() -> None:
    config = _mock_config(mock=False)
    with patch(
        "nv_config_manager.common.config.loader.load_config",
        return_value=config,
    ) as load_config:
        connection = NetworkConnection.from_device_data(_CUMULUS_DEVICE)

    assert isinstance(connection, CumulusConnection)
    load_config.assert_called_once_with()


def test_from_device_data_rejects_ufm_platform():
    """UFM is inventoried as a platform but is not a NetworkConnection."""
    config = _mock_config(mock=False)
    with pytest.raises(NotImplementedError, match="use UFMClient"):
        NetworkConnection.from_device_data(_UFM_DEVICE, config=config)
