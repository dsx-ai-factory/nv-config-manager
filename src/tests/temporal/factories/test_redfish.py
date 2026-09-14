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
from typing import cast
from unittest.mock import patch

import pytest
import requests
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.factories.redfish import (
    RedfishCredentialKind,
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


@pytest.fixture(autouse=True)
def redfish_config(monkeypatch: pytest.MonkeyPatch) -> ConfigParser:
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
    monkeypatch.setattr("nv_config_manager.common.config_loader.load_config", lambda: config)
    monkeypatch.setattr("nv_config_manager.temporal.factories.redfish.load_config", lambda: config)
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
    redfish_config: ConfigParser,
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
    rotation_password = {
        RedfishVendor.LENOVO: "lenovo-managed",
        RedfishVendor.BLUEFIELD: "bluefield-managed",
        RedfishVendor.DELL: "host-default",
    }[vendor]

    assert isinstance(default_connection, connection_type)
    assert default_connection.username == "host-user"
    assert default_connection.password == "host-default"
    assert default_connection.config_manager_password == rotation_password
    assert isinstance(managed_connection, connection_type)
    assert managed_connection.username == "host-user"
    expected_managed_password = "host-default" if vendor == RedfishVendor.DELL else "host-managed"
    assert managed_connection.password == expected_managed_password
    assert managed_connection.config_manager_password == rotation_password


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
        bmc_credentials={},
    )
    managed_connection = get_config_manager_connection(
        host,
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


@pytest.mark.parametrize(
    ("vendor", "account_path", "managed_password"),
    [
        (RedfishVendor.LENOVO, "AccountService/Accounts/1", "lenovo-managed"),
        (RedfishVendor.BLUEFIELD, "AccountService/Accounts/root", "bluefield-managed"),
    ],
)
@pytest.mark.parametrize("host_managed_password", [None, "host-managed"])
def test_password_rotation_uses_ini_independently_of_host_managed_password(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
    account_path: str,
    managed_password: str,
    host_managed_password: str | None,
) -> None:
    """A host login password must not replace the INI password used for rotation."""
    host = RedfishHost(address="192.0.2.10", vendor=vendor, mac="aa:bb:cc:dd:ee:ff")
    assert host.mac is not None
    credentials = {"default_user": "host-user", "default_password": "factory-password"}
    if host_managed_password is not None:
        credentials["config_manager_password"] = host_managed_password
    connection = get_default_connection(
        host,
        bmc_credentials={host.mac: credentials},
    )
    assert connection.username == "host-user"
    assert connection.password == "factory-password"
    response = requests.Response()
    response.status_code = 200

    with patch.object(connection, "patch", return_value=response) as request:
        result = connection.set_config_manager_password()

    assert result is response
    request.assert_called_once_with(
        path=account_path,
        payload={"Password": managed_password},
    )
    assert connection.password == managed_password


@pytest.mark.parametrize("vendor", [RedfishVendor.LENOVO, RedfishVendor.BLUEFIELD])
def test_managed_login_requires_managed_password_in_existing_host_credentials(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=vendor, mac="aa:bb:cc:dd:ee:ff")
    assert host.mac is not None
    with pytest.raises(KeyError, match="config_manager_password"):
        get_config_manager_connection(
            host,
            bmc_credentials={
                host.mac: {"default_user": "host-user", "default_password": "factory-password"}
            },
        )


def test_dell_without_host_credentials_preserves_application_error() -> None:
    host = RedfishHost(address="192.0.2.10", vendor=RedfishVendor.DELL)

    with pytest.raises(ApplicationError, match="No password found for host"):
        get_default_connection(host, bmc_credentials={})


@pytest.mark.parametrize("credential_kind", ["default", "config_manager"])
def test_unsupported_vendor_without_credentials_raises_not_implemented(
    redfish_config: ConfigParser,
    credential_kind: RedfishCredentialKind,
) -> None:
    with pytest.raises(
        NotImplementedError,
        match="No Redfish connection implemented for vendor Unsupported",
    ):
        redfish_client_settings(
            vendor=cast(RedfishVendor, "Unsupported"),
            config=redfish_config,
            credentials=None,
            credential_kind=credential_kind,
        )


@pytest.mark.parametrize("credential_kind", ["default", "config_manager"])
def test_dell_settings_without_credentials_raise_application_error(
    redfish_config: ConfigParser,
    credential_kind: RedfishCredentialKind,
) -> None:
    with pytest.raises(
        ApplicationError, match="Redfish credentials require a host-specific mapping"
    ):
        redfish_client_settings(
            vendor=RedfishVendor.DELL,
            config=redfish_config,
            credentials=None,
            credential_kind=credential_kind,
        )


def test_dell_managed_settings_use_the_default_password(redfish_config: ConfigParser) -> None:
    settings = redfish_client_settings(
        vendor=RedfishVendor.DELL,
        config=redfish_config,
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
        "config_manager_password": "host-default",
    }


@pytest.mark.parametrize("vendor", [RedfishVendor.LENOVO, RedfishVendor.BLUEFIELD])
@pytest.mark.parametrize("credential_kind", ["default", "config_manager"])
def test_host_login_does_not_require_ini_redfish_section(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
    credential_kind: RedfishCredentialKind,
) -> None:
    redfish_config.remove_section("redfish")
    host = RedfishHost(address="192.0.2.10", vendor=vendor, mac="aa:bb:cc:dd:ee:ff")
    assert host.mac is not None
    password_key = "default_password" if credential_kind == "default" else "config_manager_password"
    factory = (
        get_default_connection if credential_kind == "default" else get_config_manager_connection
    )
    connection = factory(
        host,
        bmc_credentials={host.mac: {"default_user": "host-user", password_key: "host-password"}},
    )
    assert connection.username == "host-user"
    assert connection.password == "host-password"
    with patch.object(connection, "patch") as request:
        with pytest.raises(KeyError, match="redfish"):
            connection.set_config_manager_password()
    request.assert_not_called()


@pytest.mark.parametrize(
    ("vendor", "prefix"),
    [(RedfishVendor.LENOVO, "lenovo"), (RedfishVendor.BLUEFIELD, "bluefield")],
)
@pytest.mark.parametrize("credential_kind", ["default", "config_manager"])
@pytest.mark.parametrize("empty_entry", [False, True])
def test_ini_login_requires_only_the_selected_password(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
    prefix: str,
    credential_kind: RedfishCredentialKind,
    empty_entry: bool,
) -> None:
    unused_key = "config_manager_password" if credential_kind == "default" else "default_password"
    redfish_config.remove_option("redfish", f"{prefix}_{unused_key}")
    host = RedfishHost(address="192.0.2.10", vendor=vendor, mac="aa:bb:cc:dd:ee:ff")
    assert host.mac is not None
    factory = (
        get_default_connection if credential_kind == "default" else get_config_manager_connection
    )
    connection = factory(host, bmc_credentials={host.mac: {}} if empty_entry else {})
    expected = "default" if credential_kind == "default" else "managed"
    assert connection.username == f"{prefix}-user"
    assert connection.password == f"{prefix}-{expected}"


@pytest.mark.parametrize(
    ("vendor", "prefix", "account_path"),
    [
        (RedfishVendor.LENOVO, "lenovo", "AccountService/Accounts/1"),
        (RedfishVendor.BLUEFIELD, "bluefield", "AccountService/Accounts/root"),
    ],
)
def test_rotation_reads_latest_ini_password_once(
    redfish_config: ConfigParser,
    monkeypatch: pytest.MonkeyPatch,
    vendor: RedfishVendor,
    prefix: str,
    account_path: str,
) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=vendor)
    connection = get_default_connection(host, bmc_credentials={})
    updated_config = ConfigParser()
    updated_config.read_dict({"redfish": {f"{prefix}_config_manager_password": "updated-password"}})
    monkeypatch.setattr(
        "nv_config_manager.common.config_loader.load_config", lambda: updated_config
    )
    response = requests.Response()
    response.status_code = 200

    def patch_password(**kwargs: object) -> requests.Response:
        updated_config["redfish"][f"{prefix}_config_manager_password"] = "changed-during-request"
        return response

    with patch.object(connection, "patch", side_effect=patch_password) as request:
        assert connection.set_config_manager_password() is response
    request.assert_called_once_with(path=account_path, payload={"Password": "updated-password"})
    assert connection.password == "updated-password"


@pytest.mark.parametrize("vendor", [RedfishVendor.LENOVO, RedfishVendor.BLUEFIELD])
@pytest.mark.parametrize("credential_kind", ["default", "config_manager"])
def test_ini_login_uses_config_loaded_before_host_credentials(
    redfish_config: ConfigParser,
    vendor: RedfishVendor,
    credential_kind: RedfishCredentialKind,
) -> None:
    host = RedfishHost(address="192.0.2.10", vendor=vendor, mac="aa:bb:cc:dd:ee:ff")
    factory = (
        get_default_connection if credential_kind == "default" else get_config_manager_connection
    )
    with (
        patch(
            "nv_config_manager.temporal.factories.redfish.load_config",
            return_value=redfish_config,
        ) as initial_load,
        patch(
            "nv_config_manager.common.config_loader.load_config",
            side_effect=AssertionError("Login must reuse the initial configuration"),
        ),
        patch("nv_config_manager.temporal.factories.redfish.get_bmc_creds") as host_credentials,
    ):

        def read_host_credentials() -> dict[str, dict[str, str]]:
            initial_load.assert_called_once_with()
            return {}

        host_credentials.side_effect = read_host_credentials
        connection = factory(host)
        initial_load.assert_called_once_with()
        host_credentials.assert_called_once_with()

    prefix = "lenovo" if vendor == RedfishVendor.LENOVO else "bluefield"
    expected = "default" if credential_kind == "default" else "managed"
    assert connection.username == f"{prefix}-user"
    assert connection.password == f"{prefix}-{expected}"
