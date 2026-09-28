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
"""Constructor contracts for service-owned device-client adapters."""

from configparser import ConfigParser
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from nv_config_manager.temporal.client.device import (
    AristaConnection,
    CumulusConnection,
    JuniperConnection,
    MellanoxConnection,
    MockNetworkConnection,
    NetworkConnection,
    NVOSConnection,
)
from nv_config_manager_workflows.clients.device.settings import DeviceConnectionSettings

_SETTINGS: DeviceConnectionSettings = {
    "username": "user",
    "passwords": ["password"],
    "mock": False,
}
_OMITTED = object()

_ADAPTERS = [
    pytest.param(AristaConnection, 443, id="arista"),
    pytest.param(CumulusConnection, 8765, id="cumulus"),
    pytest.param(NVOSConnection, 443, id="nvos"),
    pytest.param(MellanoxConnection, 22, id="mellanox"),
    pytest.param(JuniperConnection, 830, id="juniper"),
    pytest.param(MockNetworkConnection, 443, id="mock"),
]


def _construct(
    connection_class: type[NetworkConnection],
    *,
    port: object = _OMITTED,
) -> NetworkConnection:
    kwargs = {} if port is _OMITTED else {"port": port}
    with (
        patch(
            "nv_config_manager.temporal.client.device.base.device_connection_settings",
            return_value=_SETTINGS,
        ),
        patch.object(AristaConnection, "_connect", return_value=Mock()),
    ):
        return connection_class("192.0.2.10", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(("connection_class", "default_port"), _ADAPTERS)
@pytest.mark.parametrize(
    "port",
    [pytest.param(_OMITTED, id="omitted"), pytest.param(None, id="none")],
)
def test_service_adapters_use_platform_default_port(
    connection_class: type[NetworkConnection],
    default_port: int,
    port: object,
) -> None:
    connection = _construct(connection_class, port=port)

    assert connection._port == default_port
    connection.close()


@pytest.mark.parametrize(("connection_class", "default_port"), _ADAPTERS)
def test_service_adapters_preserve_explicit_numeric_port(
    connection_class: type[NetworkConnection],
    default_port: int,
) -> None:
    connection = _construct(connection_class, port=2022)

    assert connection._port == 2022
    assert connection._port != default_port
    connection.close()


@pytest.mark.parametrize("port", [_OMITTED, None])
def test_service_base_requires_port(port: object) -> None:
    constructor = cast(Any, NetworkConnection)
    kwargs = {} if port is _OMITTED else {"port": port}

    with pytest.raises(TypeError, match="requires a port"):
        constructor("192.0.2.10", **kwargs)


@pytest.mark.parametrize(("connection_class", "default_port"), _ADAPTERS)
def test_service_adapter_site_is_keyword_only(
    connection_class: type[NetworkConnection],
    default_port: int,
) -> None:
    constructor = cast(Any, connection_class)

    with pytest.raises(TypeError):
        constructor("192.0.2.10", default_port, None, None, "site-a")


@pytest.mark.parametrize(("connection_class", "default_port"), _ADAPTERS)
def test_service_adapters_do_not_accept_explicit_settings(
    connection_class: type[NetworkConnection],
    default_port: int,
) -> None:
    constructor = cast(Any, connection_class)

    with pytest.raises(TypeError, match="settings"):
        constructor("192.0.2.10", default_port, settings=_SETTINGS)


def test_service_factory_does_not_accept_explicit_settings() -> None:
    factory = cast(Any, NetworkConnection.from_device_data)

    with pytest.raises(TypeError, match="settings"):
        factory(None, settings=_SETTINGS)


def test_service_adapter_passes_legacy_inputs_to_settings_resolver() -> None:
    config = ConfigParser()
    with (
        patch(
            "nv_config_manager.temporal.client.device.base.device_connection_settings",
            return_value=_SETTINGS,
        ) as resolve_settings,
        patch.object(AristaConnection, "_connect", return_value=Mock()),
    ):
        connection = AristaConnection(
            "192.0.2.10",
            username="explicit-user",
            password="explicit-password",
            site="site-a",
            config=config,
        )

    resolve_settings.assert_called_once_with(
        config,
        site="site-a",
        username="explicit-user",
        password="explicit-password",
        mock=False,
    )
    connection.close()
