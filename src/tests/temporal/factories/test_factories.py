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
"""Tests for service-owned INI-to-client-settings adapters."""

from __future__ import annotations

import logging
from configparser import ConfigParser
from unittest.mock import Mock

import pytest

from nv_config_manager.common.config import get_internal_auth_headers
from nv_config_manager.temporal.factories import _config as config_module
from nv_config_manager.temporal.factories import (
    config_store_client_settings,
    device_connection_settings,
    nats_client_settings,
    redfish_client_settings,
    redis_settings,
    render_client_settings,
    ticketing_client_settings,
    ufm_client_settings,
)


@pytest.fixture
def client_config() -> ConfigParser:
    """Return representative settings for every supported client family."""
    config = ConfigParser()
    config.read_dict(
        {
            "device": {
                "username": "device-user",
                "password": "device-fallback",
                "api_user_key_r1": "device-old",
                "api_user_key_r3": "device-new",
                "mock": "true",
            },
            "ufm": {
                "ufm_api_user": "ufm-user",
                "ufm_api_token_r2": "ufm-old",
                "ufm_api_token_r7": "ufm-new",
            },
            "redis": {
                "host": "redis.example",
                "port": "6380",
                "db": "4",
                "lock_db": "9",
                "ssl": "true",
                "password": "redis-secret",
                "socket_timeout": "11",
                "socket_connect_timeout": "12",
            },
            "render": {
                "api_url": "https://render.example",
                "api_service": "http://render.internal:9000",
                "use_internal_endpoint": "false",
            },
            "config_store.client": {
                "api_url": "https://config-store.example",
                "api_service": "http://config-store.internal:8080",
                "ui_url": "https://config-manager.example",
                "use_internal_endpoint": "true",
                "verify": "true",
            },
            "mtls": {
                "tls_client_cert_path": "/certs/client.crt",
                "tls_client_key_path": "/certs/client.key",
            },
            "nats": {
                "server": "nats://nats.example:4222",
                "queue": "workflow",
                "local": "true",
                "auth_method": "JWT",
                "user": "nats-user",
                "password": "nats-secret",
                "creds_path": "/secrets/nats.creds",
                "config_manager_stream": "archive",
                "config_manager_subjects": "one, two , ,three",
                "config_manager_api_prefix": "$JS.CUSTOM.API",
            },
            "jira": {
                "base_url": "https://jira.example",
                "api_token": "jira-secret",
            },
            "redfish": {
                "lenovo_default_user": "lenovo-user",
                "lenovo_default_password": "lenovo-default",
                "lenovo_config_manager_password": "lenovo-managed",
                "bluefield_default_user": "bluefield-user",
                "bluefield_default_password": "bluefield-default",
                "bluefield_config_manager_password": "bluefield-managed",
            },
        }
    )
    return config


def test_device_connection_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert device_connection_settings(client_config) == {
        "username": "device-user",
        "passwords": ["device-new", "device-old"],
        "mock": True,
    }


def test_ufm_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert ufm_client_settings(client_config) == {
        "username": "ufm-user",
        "passwords": ["ufm-new", "ufm-old"],
    }


def test_redis_settings_match_current_constructor_values(client_config: ConfigParser) -> None:
    assert redis_settings(client_config, db_key="lock_db") == {
        "host": "redis.example",
        "port": 6380,
        "db": 9,
        "ssl": True,
        "password": "redis-secret",
        "socket_timeout": 11,
        "socket_connect_timeout": 12,
    }


def test_render_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert render_client_settings(client_config) == {
        "base_url": "https://render.example",
        "client_certificate": ("/certs/client.crt", "/certs/client.key"),
        "headers": None,
    }


def test_config_store_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert config_store_client_settings(client_config, file_type="backup") == {
        "target": "http://config-store.internal:8080",
        "file_type": "backup",
        "ui_url": "https://config-manager.example",
        "verify": False,
        "client_certificate": None,
        "headers": get_internal_auth_headers,
    }


def test_nats_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert nats_client_settings(client_config) == {
        "api_prefix": "$JS.CUSTOM.API",
        "server": "nats://nats.example:4222",
        "queue": "workflow",
        "local": True,
        "auth_method": "JWT",
        "user": "nats-user",
        "password": "nats-secret",
        "creds_path": "/secrets/nats.creds",
        "default_stream_name": "archive",
        "default_stream_subjects": ["one", "two", "three"],
    }


def test_ticketing_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert ticketing_client_settings(client_config, platform="jira") == {
        "base_url": "https://jira.example",
        "api_token": "jira-secret",
    }


def test_redfish_client_settings_match_current_constructor_values(
    client_config: ConfigParser,
) -> None:
    assert redfish_client_settings(client_config, vendor="Lenovo") == {
        "username": "lenovo-user",
        "password": "lenovo-default",
        "config_manager_password": "lenovo-managed",
    }
    assert redfish_client_settings(
        client_config,
        vendor="Nvidia",
        credentials={
            "default_user": "host-user",
            "default_password": "host-default",
            "config_manager_password": "host-managed",
        },
        credential_kind="config_manager",
    ) == {
        "username": "host-user",
        "password": "host-managed",
        "config_manager_password": "host-managed",
    }


def test_internal_render_endpoint_settings(client_config: ConfigParser) -> None:
    client_config.set("render", "use_internal_endpoint", "true")

    assert render_client_settings(client_config) == {
        "base_url": "http://render.internal:9000",
        "client_certificate": None,
        "headers": get_internal_auth_headers,
    }


def test_external_config_store_endpoint_settings(client_config: ConfigParser) -> None:
    client_config.set("config_store.client", "use_internal_endpoint", "false")
    client_config.set("config_store.client", "verify", "/certs/ca.crt")

    assert config_store_client_settings(client_config) == {
        "target": "https://config-store.example",
        "file_type": "intended",
        "ui_url": "https://config-manager.example",
        "verify": "/certs/ca.crt",
        "client_certificate": ("/certs/client.crt", "/certs/client.key"),
        "headers": None,
    }


def test_default_configuration_is_loaded_when_not_injected(
    client_config: ConfigParser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_config = Mock(return_value=client_config)
    monkeypatch.setattr(config_module, "load_config", load_config)

    assert redis_settings()["host"] == "redis.example"
    load_config.assert_called_once_with()


def test_injected_configuration_does_not_load_global_config(
    client_config: ConfigParser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_config = Mock(side_effect=AssertionError("load_config should not be called"))
    monkeypatch.setattr(config_module, "load_config", load_config)

    config_store_client_settings(client_config)
    device_connection_settings(client_config)
    nats_client_settings(client_config)
    redfish_client_settings(client_config, vendor="Lenovo")
    render_client_settings(client_config)
    redis_settings(client_config)
    ticketing_client_settings(client_config, platform="jira")
    ufm_client_settings(client_config)

    load_config.assert_not_called()


def test_factories_do_not_log_credentials(
    client_config: ConfigParser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        device_connection_settings(client_config)
        nats_client_settings(client_config)
        redfish_client_settings(client_config, vendor="Lenovo")
        redis_settings(client_config)
        ticketing_client_settings(client_config, platform="jira")
        ufm_client_settings(client_config)

    for secret in (
        "device-new",
        "ufm-new",
        "redis-secret",
        "nats-secret",
        "jira-secret",
        "lenovo-default",
        "lenovo-managed",
    ):
        assert secret not in caplog.text
