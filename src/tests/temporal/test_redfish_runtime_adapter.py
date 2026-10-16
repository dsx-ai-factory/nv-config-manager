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
"""Tests for the service-owned Redfish runtime composition."""

from typing import cast
from unittest.mock import Mock, call

import pytest
from pytest_mock import MockerFixture

from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost, RedfishVendor
from nv_config_manager_workflows.runtime import RedfishCredentialRole, get_redfish_connection


def _host() -> RedfishHost:
    """Return representative package-owned Redfish host data."""
    return RedfishHost(
        address="192.0.2.20",
        port=8443,
        vendor=RedfishVendor.LENOVO,
        mac="aa:bb:cc:dd:ee:ff",
    )


def test_redfish_adapter_delegates_both_credential_roles_without_http(
    mocker: MockerFixture,
) -> None:
    """Connection resolution selects credentials but performs no network request."""
    host = _host()
    default_connection = Mock(spec=RedfishConnection)
    managed_connection = Mock(spec=RedfishConnection)
    default_factory = mocker.patch.object(
        service_runtime,
        "get_default_connection",
        return_value=default_connection,
    )
    managed_factory = mocker.patch.object(
        service_runtime,
        "get_config_manager_connection",
        return_value=managed_connection,
    )
    http_request = mocker.patch("requests.sessions.Session.request")

    assert service_runtime._redfish_connection(host, "default") is default_connection
    assert service_runtime._redfish_connection(host, "config_manager") is managed_connection

    default_factory.assert_called_once_with(host)
    managed_factory.assert_called_once_with(host)
    http_request.assert_not_called()


def test_redfish_adapter_resolves_service_credentials_without_http(
    mocker: MockerFixture,
) -> None:
    """The real factories resolve current service settings without opening a session."""
    host = _host()
    mocker.patch(
        "nv_config_manager.temporal.client.redfish.get_bmc_creds",
        return_value={},
    )
    http_request = mocker.patch("requests.sessions.Session.request")

    default_connection = service_runtime._redfish_connection(host, "default")
    managed_connection = service_runtime._redfish_connection(host, "config_manager")

    assert isinstance(default_connection, RedfishConnection)
    assert isinstance(managed_connection, RedfishConnection)
    assert default_connection.host is host
    assert managed_connection.host is host
    assert default_connection.username == "LENOVO_DEFAULT_USER"
    assert default_connection.password == "LENOVO_DEFAULT_PASSWORD"
    assert managed_connection.username == "LENOVO_DEFAULT_USER"
    assert managed_connection.password == "LENOVO_CONFIG_MANAGER_PASSWORD"
    http_request.assert_not_called()


def test_redfish_adapter_rejects_unsupported_role_without_selecting_credentials(
    mocker: MockerFixture,
) -> None:
    """The service adapter defensively rejects invalid roles before factory lookup."""
    default_factory = mocker.patch.object(service_runtime, "get_default_connection")
    managed_factory = mocker.patch.object(service_runtime, "get_config_manager_connection")

    with pytest.raises(
        ValueError,
        match="^Unsupported Redfish credential role: 'unsupported'$",
    ):
        service_runtime._redfish_connection(
            _host(),
            cast(RedfishCredentialRole, "unsupported"),
        )

    default_factory.assert_not_called()
    managed_factory.assert_not_called()


def test_redfish_service_provider_is_lazy_and_resolves_every_access(
    mocker: MockerFixture,
) -> None:
    """Worker setup does no credential work and each getter sees replacement values."""
    host = _host()
    connections = [Mock(spec=RedfishConnection), Mock(spec=RedfishConnection)]
    default_factory = mocker.patch.object(
        service_runtime,
        "get_default_connection",
        side_effect=connections,
    )
    managed_factory = mocker.patch.object(service_runtime, "get_config_manager_connection")
    http_request = mocker.patch("requests.sessions.Session.request")

    service_runtime.configure_workflow_runtime()

    default_factory.assert_not_called()
    managed_factory.assert_not_called()
    http_request.assert_not_called()
    assert get_redfish_connection(host, "default") is connections[0]
    assert get_redfish_connection(host, "default") is connections[1]
    assert default_factory.call_args_list == [call(host), call(host)]
    managed_factory.assert_not_called()
    http_request.assert_not_called()
