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
"""Tests for shared Redfish HTTP behavior."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from nv_config_manager_workflows.clients.redfish import (
    RedfishConnection,
    RedfishHost,
    RedfishVendor,
)


@pytest.fixture
def connection() -> RedfishConnection:
    return RedfishConnection(
        host=RedfishHost(address="192.0.2.10", port=8443, vendor=RedfishVendor.LENOVO),
        username="explicit-user",
        password="explicit-password",
        config_manager_password="explicit-managed-password",
    )


def test_session_uses_explicit_credentials(connection: RedfishConnection) -> None:
    session = connection.get_session()

    assert session.verify is False
    assert session.auth == ("explicit-user", "explicit-password")


def test_patch_preserves_url_payload_and_timeout(connection: RedfishConnection) -> None:
    session = MagicMock(spec=requests.Session)
    response = MagicMock(spec=requests.Response)
    session.patch.return_value = response

    with patch.object(connection, "get_session", return_value=session):
        result = connection.patch("AccountService/Accounts/1", {"Password": "new"}, timeout=7)

    assert result is response
    session.patch.assert_called_once_with(
        "https://192.0.2.10:8443/redfish/v1/AccountService/Accounts/1",
        json={"Password": "new"},
        timeout=7,
    )
    response.raise_for_status.assert_called_once_with()


def test_post_preserves_url_payload_and_timeout(connection: RedfishConnection) -> None:
    session = MagicMock(spec=requests.Session)
    response = MagicMock(spec=requests.Response)
    session.post.return_value = response

    with patch.object(connection, "get_session", return_value=session):
        result = connection.post("Systems/1/Actions/ComputerSystem.Reset", {"ResetType": "On"})

    assert result is response
    session.post.assert_called_once_with(
        "https://192.0.2.10:8443/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": "On"},
        timeout=10,
    )
    response.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize(
    ("path", "expected_url"),
    [
        (None, "https://192.0.2.10:8443/redfish/v1/"),
        ("Managers/1", "https://192.0.2.10:8443/redfish/v1/Managers/1"),
    ],
)
def test_get_preserves_root_and_resource_urls(
    connection: RedfishConnection,
    path: str | None,
    expected_url: str,
) -> None:
    session = MagicMock(spec=requests.Session)
    response = MagicMock(spec=requests.Response)
    session.get.return_value = response

    with patch.object(connection, "get_session", return_value=session):
        result = connection.get(path, timeout=12)

    assert result is response
    session.get.assert_called_once_with(expected_url, timeout=12)
    response.raise_for_status.assert_called_once_with()


def test_http_error_is_propagated(connection: RedfishConnection) -> None:
    session = MagicMock(spec=requests.Session)
    response = MagicMock(spec=requests.Response)
    error = requests.HTTPError("unauthorized")
    response.raise_for_status.side_effect = error
    session.get.return_value = response

    with (
        patch.object(connection, "get_session", return_value=session),
        pytest.raises(requests.HTTPError) as raised,
    ):
        connection.get("Systems/1")

    assert raised.value is error
