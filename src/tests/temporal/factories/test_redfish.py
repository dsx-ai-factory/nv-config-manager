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
"""Tests for the service-owned Redfish credential adapter."""

from __future__ import annotations

import json
from configparser import ConfigParser
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.factories.redfish import (
    get_bmc_creds,
    get_config_manager_connection,
    get_default_connection,
    redfish_client_settings,
)
from nv_config_manager_workflows.clients.redfish import (
    Bluefield3RedfishConnection,
    DellRedfishConnection,
    LenovoRedfishConnection,
    RedfishConnection,
    RedfishHost,
    RedfishVendor,
)


@pytest.fixture
def redfish_config() -> ConfigParser:
    """Return representative fallback Redfish credentials."""
    config = ConfigParser()
    config.read_dict(
        {
            "redfish": {
                "lenovo_default_user": "lenovo-user",
                "lenovo_default_password": "lenovo-default",
                "lenovo_config_manager_password": "lenovo-managed",
                "bluefield_default_user": "bluefield-user",
                "bluefield_default_password": "bluefield-default",
                "bluefield_config_manager_password": "bluefield-managed",
            }
        }
    )
    return config


def test_get_bmc_creds_reads_the_service_owned_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "bmc-creds.json"
    credentials = {
        "AA-BB-CC-DD-EE-FF": {
            "default_user": "host-user",
            "default_password": "host-default",
            "config_manager_password": "host-managed",
        }
    }
    credentials_path.write_text(json.dumps(credentials), encoding="utf-8")
    monkeypatch.setenv("BMC_CREDS_PATH", str(credentials_path))

    assert get_bmc_creds() == credentials


def test_get_bmc_creds_preserves_missing_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "missing.json"
    monkeypatch.setenv("BMC_CREDS_PATH", str(credentials_path))

    with pytest.raises(FileNotFoundError):
        get_bmc_creds()


@pytest.mark.parametrize(
    ("vendor", "connection_type"),
    [
        (RedfishVendor.LENOVO, LenovoRedfishConnection),
        (RedfishVendor.BLUEFIELD, Bluefield3RedfishConnection),
        (RedfishVendor.DELL, DellRedfishConnection),
    ],
)
def test_connections_use_host_specific_credentials(
    vendor: RedfishVendor,
    connection_type: type[RedfishConnection],
) -> None:
    host = RedfishHost(
        address="192.0.2.10",
        vendor=vendor,
        mac="aa:bb:cc:dd:ee:ff",
    )
    assert host.mac is not None
    credentials = {
        host.mac: {
            "default_user": "host-user",
            "default_password": "host-default",
            "config_manager_password": "host-managed",
        }
    }

    default_connection = get_default_connection(host, bmc_credentials=credentials)
    managed_connection = get_config_manager_connection(host, bmc_credentials=credentials)

    assert isinstance(default_connection, connection_type)
    assert default_connection.username == "host-user"
    assert default_connection.password == "host-default"
    assert default_connection.config_manager_password == "host-managed"
    assert isinstance(managed_connection, connection_type)
    assert managed_connection.username == "host-user"
    expected_managed_password = "host-default" if vendor == RedfishVendor.DELL else "host-managed"
    assert managed_connection.password == expected_managed_password
    assert managed_connection.config_manager_password == "host-managed"


@pytest.mark.parametrize(
    ("vendor", "connection_type", "username", "default_password", "managed_password"),
    [
        (
            RedfishVendor.LENOVO,
            LenovoRedfishConnection,
            "lenovo-user",
            "lenovo-default",
            "lenovo-managed",
        ),
        (
            RedfishVendor.BLUEFIELD,
            Bluefield3RedfishConnection,
            "bluefield-user",
            "bluefield-default",
            "bluefield-managed",
        ),
    ],
)
def test_connections_fall_back_to_injected_ini_settings(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
    connection_type: type[RedfishConnection],
    username: str,
    default_password: str,
    managed_password: str,
) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=vendor)

    default_connection = get_default_connection(
        host,
        redfish_config,
        bmc_credentials={},
    )
    managed_connection = get_config_manager_connection(
        host,
        redfish_config,
        bmc_credentials={},
    )

    assert isinstance(default_connection, connection_type)
    assert default_connection.username == username
    assert default_connection.password == default_password
    assert default_connection.config_manager_password == managed_password
    assert isinstance(managed_connection, connection_type)
    assert managed_connection.username == username
    assert managed_connection.password == managed_password
    assert managed_connection.config_manager_password == managed_password


def test_dell_without_host_credentials_preserves_application_error() -> None:
    host = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.DELL)

    with pytest.raises(ApplicationError, match="No password found for host"):
        get_default_connection(host, bmc_credentials={})


def test_dell_managed_settings_use_the_default_password() -> None:
    settings = redfish_client_settings(
        vendor=RedfishVendor.DELL,
        credentials={
            "default_user": "host-user",
            "default_password": "host-default",
            "config_manager_password": "host-managed",
        },
        credential_kind="config_manager",
    )

    assert settings == {
        "username": "host-user",
        "password": "host-default",
        "config_manager_password": "host-managed",
    }
