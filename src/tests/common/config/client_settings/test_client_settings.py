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
"""Tests for service-owned client settings adapters."""

from collections.abc import Callable, Iterator
from configparser import ConfigParser
from unittest.mock import patch

import pytest
from nv_config_manager_clients import ConfigStoreClient as PackageConfigStoreClient
from nv_config_manager_clients import ConfigStoreType
from nv_config_manager_clients import RenderClient as PackageRenderClient
from nv_config_manager_infrastructure.nats import DEFAULT_NATS_API_PREFIX
from nv_config_manager_infrastructure.nats import NatsClient as PackageNatsClient
from nv_config_manager_infrastructure.redis import RedisClient as PackageRedisClient

from nv_config_manager.common.config import (
    config_store_client,
    nats_client,
    redis_client,
    render_client,
)
from nv_config_manager.common.config.client_settings import (
    config_store_client_settings,
    device_connection_settings,
    nats_client_settings,
    nats_consumer_settings,
    redfish_client_settings,
    redis_settings,
    render_client_settings,
    ticketing_client_settings,
    ufm_client_settings,
)
from nv_config_manager.common.config.http import get_internal_auth_headers
from nv_config_manager.temporal.common.secrets import clear_secrets_cache


def _config(*, internal: bool = False) -> ConfigParser:
    config = ConfigParser(interpolation=None)
    config.read_dict(
        {
            "config_store.client": {
                "api_service": "http://config-store.internal:9000",
                "api_url": "https://config-store.example.com",
                "ui_url": "https://config.example.com",
                "use_internal_endpoint": str(internal),
                "verify": "/etc/ssl/config-store-ca.pem",
            },
            "render": {
                "api_service": "http://render.internal:9000",
                "api_url": "https://render.example.com",
                "use_internal_endpoint": str(internal),
                # Render currently ignores this value and retains verify=True.
                "verify": "false",
            },
            "mtls": {
                "tls_client_cert_path": "/etc/mtls/client.crt",
                "tls_client_key_path": "/etc/mtls/client.key",
            },
            "redis": {
                "host": "redis.internal",
                "port": "6380",
                "db": "2",
                "lock_db": "3",
                "ssl": "true",
                "password": "redis-secret",
                "socket_timeout": "7",
                "socket_connect_timeout": "8",
            },
            "nats": {
                "server": "tls://nats.example.com:4222",
                "queue": "test-queue",
                "local": "false",
                "auth_method": "JWT",
                "user": "nats-user",
                "password": "nats-secret",
                "creds_path": "/etc/nats/test.creds",
                "config_manager_stream": "test-stream",
                "config_manager_subjects": "one, two, ,three",
                "config_manager_api_prefix": "$JS.TEST.API",
                "archive_consumer_name": "archive-durable",
                "archive_deliver_subject": "archive.delivery",
            },
            "device": {
                "username": "device-user",
                "password": "device-fallback",
                "api_user_key_r1": "device-old",
                "api_user_key_r3": "device-new",
                "mock": "true",
            },
            "ufm": {
                "ufm_api_user": "ufm-user",
                "ufm_api_token_r1": "ufm-old",
                "ufm_api_token_r2": "ufm-new",
                "ufm_api_token_invalid": "ignored",
            },
            "jira": {
                "base_url": "https://jira.example.com",
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


@pytest.fixture(autouse=True)
def _no_site_secrets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", raising=False)
    clear_secrets_cache()
    yield
    clear_secrets_cache()


def test_http_client_settings_preserve_internal_endpoint_behavior() -> None:
    config = _config(internal=True)

    config_store = config_store_client_settings(config, file_type=ConfigStoreType.BACKUP)
    render = render_client_settings(config)

    assert config_store == {
        "target": "http://config-store.internal:9000",
        "file_type": "backup",
        "ui_url": "https://config.example.com",
        "verify": False,
        "client_certificate": None,
        "headers": get_internal_auth_headers,
    }
    assert render == {
        "base_url": "http://render.internal:9000",
        "client_certificate": None,
        "headers": get_internal_auth_headers,
        "verify": True,
    }


def test_http_client_settings_preserve_external_endpoint_behavior() -> None:
    config = _config()

    assert config_store_client_settings(config) == {
        "target": "https://config-store.example.com",
        "file_type": "intended",
        "ui_url": "https://config.example.com",
        "verify": "/etc/ssl/config-store-ca.pem",
        "client_certificate": ("/etc/mtls/client.crt", "/etc/mtls/client.key"),
        "headers": None,
    }
    assert render_client_settings(config) == {
        "base_url": "https://render.example.com",
        "client_certificate": ("/etc/mtls/client.crt", "/etc/mtls/client.key"),
        "headers": None,
        "verify": True,
    }


def test_redis_settings_match_infrastructure_constructor_values() -> None:
    config = _config()

    assert redis_settings(config, db_key="lock_db") == {
        "host": "redis.internal",
        "port": 6380,
        "db": 3,
        "ssl": True,
        "password": "redis-secret",
        "socket_timeout": 7,
        "socket_connect_timeout": 8,
    }


def test_nats_settings_match_infrastructure_constructor_values() -> None:
    config = _config()
    common = {
        "server": "tls://nats.example.com:4222",
        "queue": "test-queue",
        "local": False,
        "auth_method": "JWT",
        "user": "nats-user",
        "password": "nats-secret",
        "creds_path": "/etc/nats/test.creds",
        "default_stream_name": "test-stream",
        "default_stream_subjects": ["one", "two", "three"],
        "api_prefix": "$JS.TEST.API",
    }

    assert nats_client_settings(config) == common
    assert nats_consumer_settings(config, queue_suffix="archive") == {
        **common,
        "durable_name": "archive-durable",
        "deliver_subject": "archive.delivery",
    }


def test_nats_settings_preserve_defaults() -> None:
    config = ConfigParser()
    config.read_dict({"nats": {"server": "nats://localhost:4222"}})

    settings = nats_consumer_settings(config, queue_suffix="events")

    assert settings["queue"] == "nv-config-manager"
    assert settings["default_stream_subjects"] == ["nv-config-manager.>"]
    assert settings["api_prefix"] == DEFAULT_NATS_API_PREFIX
    assert settings["durable_name"] == "nv-config-manager-events"
    assert settings["deliver_subject"] == "nv-config-manager.archive.delivery"


def test_workflow_client_settings_preserve_global_values_and_rotation_order() -> None:
    config = _config()

    assert device_connection_settings(config) == {
        "username": "device-user",
        "passwords": ["device-new", "device-old"],
        "mock": True,
    }
    assert ufm_client_settings(config) == {
        "username": "ufm-user",
        "passwords": ["ufm-new", "ufm-old"],
    }
    assert ticketing_client_settings(config, platform="jira") == {
        "base_url": "https://jira.example.com",
        "api_token": "jira-secret",
    }


def test_ticketing_settings_reject_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown ticketing platform"):
        ticketing_client_settings(_config(), platform="unknown")


def test_redfish_settings_preserve_host_precedence_and_ini_managed_password() -> None:
    config = _config()
    credentials = {
        "AA-BB": {
            "default_user": "host-user",
            "default_password": "host-default",
            "config_manager_password": "host-managed",
        }
    }

    assert redfish_client_settings(
        config,
        vendor="Lenovo",
        mac="AA-BB",
        credential_kind="default",
        bmc_credentials=credentials,
    ) == {
        "username": "host-user",
        "password": "host-default",
        "config_manager_password": "lenovo-managed",
    }
    assert redfish_client_settings(
        config,
        vendor="Nvidia",
        mac="AA-BB",
        credential_kind="config_manager",
        bmc_credentials=credentials,
    ) == {
        "username": "host-user",
        "password": "host-managed",
        "config_manager_password": "bluefield-managed",
    }


def test_redfish_settings_preserve_fallback_and_dell_behavior() -> None:
    config = _config()

    assert redfish_client_settings(
        config,
        vendor="Lenovo",
        mac=None,
        bmc_credentials={},
    ) == {
        "username": "lenovo-user",
        "password": "lenovo-default",
        "config_manager_password": "lenovo-managed",
    }

    with pytest.raises(ValueError, match="host-specific mapping"):
        redfish_client_settings(
            config,
            vendor="Dell",
            mac=None,
            bmc_credentials={},
        )


def test_redfish_rejects_unknown_vendor_before_configuration_or_file_access() -> None:
    with (
        patch(
            "nv_config_manager.common.config.client_settings.redfish.get_bmc_credentials"
        ) as load_bmc,
        patch("nv_config_manager.common.config.loader.load_config") as load_config,
        pytest.raises(NotImplementedError, match="Unsupported"),
    ):
        redfish_client_settings(vendor="Unsupported", mac=None)

    load_bmc.assert_not_called()
    load_config.assert_not_called()


@pytest.mark.parametrize(
    "get_settings",
    [
        lambda config: config_store_client_settings(config),
        lambda config: render_client_settings(config),
        lambda config: redis_settings(config),
        lambda config: nats_client_settings(config),
        lambda config: nats_consumer_settings(config, queue_suffix="archive"),
        lambda config: device_connection_settings(config),
        lambda config: ufm_client_settings(config),
        lambda config: ticketing_client_settings(config, platform="jira"),
        lambda config: redfish_client_settings(
            config,
            vendor="Lenovo",
            mac=None,
            bmc_credentials={},
        ),
    ],
)
def test_injected_configuration_never_calls_load_config(
    get_settings: Callable[[ConfigParser], object],
) -> None:
    with patch(
        "nv_config_manager.common.config.loader.load_config",
        side_effect=AssertionError("load_config must not be called"),
    ):
        get_settings(_config())


def test_missing_configuration_uses_load_config() -> None:
    config = _config()
    with patch(
        "nv_config_manager.common.config.loader.load_config", return_value=config
    ) as load_config:
        assert redis_settings()["host"] == "redis.internal"

    load_config.assert_called_once_with()


@pytest.mark.asyncio
async def test_service_factories_construct_reusable_package_clients() -> None:
    config = _config(internal=True)

    config_store = config_store_client(ConfigStoreType.BACKUP, config)
    render = render_client(config)
    redis = redis_client("lock_db", config)
    nats = nats_client(config)

    try:
        assert isinstance(config_store, PackageConfigStoreClient)
        assert isinstance(render, PackageRenderClient)
        assert isinstance(redis, PackageRedisClient)
        assert isinstance(nats, PackageNatsClient)
        assert config_store.file_type == "backup"
        assert render.base_url == "http://render.internal:9000"
        assert redis.redis.connection_pool.connection_kwargs["db"] == 3
        assert nats.default_stream_subjects == ["one", "two", "three"]
    finally:
        await config_store.close()
        await render.close()
        await redis.close()
