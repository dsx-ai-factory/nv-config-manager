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
"""Tests for service-owned deployment runtime composition."""

from configparser import ConfigParser
from unittest.mock import AsyncMock, Mock, call

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_clients.render import RenderClient
from nv_config_manager_clients.ztp import ZTPClient
from pytest_mock import MockerFixture

from nv_config_manager.common.config.client_settings.ztp import ztp_client_settings
from nv_config_manager.temporal import runtime as service_runtime
from nv_config_manager.ztp.storage import ObjectStorageNotFoundException
from nv_config_manager_workflows.runtime import (
    get_config_store_runtime,
    get_firmware_storage,
    get_render_client,
    get_ztp_client,
)


def _service_config() -> ConfigParser:
    """Build representative Config Store, Render, ZTP, and mTLS settings."""
    config = ConfigParser()
    config.read_dict(
        {
            "config_store.client": {
                "api_service": "http://config-store.internal:8080",
                "use_internal_endpoint": "true",
                "ui_url": "https://config-store.example/",
            },
            "render": {
                "api_service": "http://render.internal:9000",
                "use_internal_endpoint": "true",
            },
            "ztp": {
                "api_service": "http://ztp.internal:9000",
                "use_internal_endpoint": "true",
            },
            "mtls": {
                "tls_client_cert_path": "/test/client.crt",
                "tls_client_key_path": "/test/client.key",
            },
        }
    )
    return config


def test_config_store_adapter_uses_one_snapshot_and_current_domain(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    """A runtime value binds client settings and scalar values to one config read."""
    config = _service_config()
    client = Mock(spec=ConfigStoreClient)
    settings = {
        "target": "http://config-store.internal:8080",
        "file_type": "backup",
        "ui_url": "https://config-store.example/",
        "verify": False,
        "client_certificate": None,
        "headers": None,
    }
    load_config = mocker.patch.object(service_runtime, "load_config", return_value=config)
    config_store_client_settings = mocker.patch.object(
        service_runtime,
        "config_store_client_settings",
        return_value=settings,
    )
    constructor = mocker.patch.object(
        service_runtime,
        "ConfigStoreClient",
        return_value=client,
    )
    monkeypatch.setenv("NV_CONFIG_MANAGER_DOMAIN", "current.example")

    runtime = service_runtime._config_store_runtime()

    assert runtime.ui_url == "https://config-store.example/"
    assert runtime.default_user_domain == "current.example"
    assert runtime.client(ConfigStoreType.BACKUP) is client
    load_config.assert_called_once_with()
    config_store_client_settings.assert_called_once_with(
        config,
        file_type=ConfigStoreType.BACKUP,
    )
    constructor.assert_called_once_with(**settings)


def test_config_store_adapter_preserves_default_user_domain(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    """An absent domain environment variable keeps the established fallback."""
    monkeypatch.delenv("NV_CONFIG_MANAGER_DOMAIN", raising=False)
    mocker.patch.object(service_runtime, "load_config", return_value=_service_config())

    runtime = service_runtime._config_store_runtime()

    assert runtime.default_user_domain == "local.config-manager.example.com"


def test_render_and_ztp_adapters_use_public_clients_and_current_settings(
    mocker: MockerFixture,
) -> None:
    """Service INI translators construct only the reusable public HTTP clients."""
    config = _service_config()
    render = Mock(spec=RenderClient)
    ztp = Mock(spec=ZTPClient)
    render_settings = {
        "base_url": "http://render.internal:9000",
        "client_certificate": None,
        "headers": None,
        "verify": True,
    }
    ztp_settings = {
        "base_url": "http://ztp.internal:9000",
        "client_certificate": None,
        "headers": None,
        "verify": True,
    }
    load_config = mocker.patch.object(service_runtime, "load_config", return_value=config)
    render_client_settings = mocker.patch.object(
        service_runtime,
        "render_client_settings",
        return_value=render_settings,
    )
    ztp_client_settings_mock = mocker.patch.object(
        service_runtime,
        "ztp_client_settings",
        return_value=ztp_settings,
    )
    render_constructor = mocker.patch.object(
        service_runtime,
        "RenderClient",
        return_value=render,
    )
    ztp_constructor = mocker.patch.object(service_runtime, "ZTPClient", return_value=ztp)

    assert service_runtime._render_client() is render
    assert service_runtime._ztp_client() is ztp
    assert load_config.call_count == 2
    render_client_settings.assert_called_once_with(config)
    ztp_client_settings_mock.assert_called_once_with(config)
    render_constructor.assert_called_once_with(**render_settings)
    ztp_constructor.assert_called_once_with(**ztp_settings)


def test_ztp_settings_translation_preserves_internal_and_external_policy(
    mocker: MockerFixture,
) -> None:
    """The extracted settings helper matches the legacy ZTP INI factory."""
    internal = _service_config()
    internal_headers = mocker.patch(
        "nv_config_manager.common.config.client_settings.http_service.get_internal_auth_headers"
    )

    assert ztp_client_settings(internal) == {
        "base_url": "http://ztp.internal:9000",
        "client_certificate": None,
        "headers": internal_headers,
        "verify": True,
    }

    external = _service_config()
    external["ztp"]["use_internal_endpoint"] = "false"
    external["ztp"]["api_url"] = "https://ztp.example"
    get_mtls_cert_paths = mocker.patch(
        "nv_config_manager.common.config.client_settings.http_service.get_mtls_cert_paths",
        return_value=("/test/client.crt", "/test/client.key"),
    )

    assert ztp_client_settings(external) == {
        "base_url": "https://ztp.example",
        "client_certificate": ("/test/client.crt", "/test/client.key"),
        "headers": None,
        "verify": True,
    }
    get_mtls_cert_paths.assert_called_once_with(external)


async def test_firmware_storage_adapter_translates_only_not_found(
    mocker: MockerFixture,
) -> None:
    """The narrow adapter maps missing firmware to false and propagates other failures."""
    storage_client = Mock()
    storage_client.__aenter__ = AsyncMock(return_value=storage_client)
    storage_client.__aexit__ = AsyncMock(return_value=None)
    storage_client.get_firmware_checksum = AsyncMock(
        side_effect=[
            "checksum",
            ObjectStorageNotFoundException("missing"),
            RuntimeError("storage unavailable"),
        ]
    )
    get_storage_client = mocker.patch.object(
        service_runtime,
        "get_storage_client",
        return_value=storage_client,
    )

    adapter = service_runtime._firmware_storage()

    get_storage_client.assert_called_once_with()
    async with adapter as storage:
        assert await storage.firmware_exists("juniper-junos", "24.4R2") is True
        assert await storage.firmware_exists("juniper-junos", "24.4R3") is False
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await storage.firmware_exists("juniper-junos", "24.4R4")

    storage_client.__aenter__.assert_awaited_once_with()
    storage_client.__aexit__.assert_awaited_once_with(None, None, None)
    assert storage_client.get_firmware_checksum.await_args_list == [
        call("juniper-junos", "24.4R2"),
        call("juniper-junos", "24.4R3"),
        call("juniper-junos", "24.4R4"),
    ]


def test_deployment_service_providers_are_lazy_and_resolve_each_access(
    mocker: MockerFixture,
) -> None:
    """Worker setup performs no client selection and getters use current providers."""
    config_stores = [Mock(), Mock()]
    render_clients = [Mock(spec=RenderClient), Mock(spec=RenderClient)]
    ztp_clients = [Mock(spec=ZTPClient), Mock(spec=ZTPClient)]
    firmware_storages = [Mock(), Mock()]
    config_store_provider = mocker.patch.object(
        service_runtime,
        "_config_store_runtime",
        side_effect=config_stores,
    )
    render_provider = mocker.patch.object(
        service_runtime,
        "_render_client",
        side_effect=render_clients,
    )
    ztp_provider = mocker.patch.object(
        service_runtime,
        "_ztp_client",
        side_effect=ztp_clients,
    )
    storage_provider = mocker.patch.object(
        service_runtime,
        "_firmware_storage",
        side_effect=firmware_storages,
    )

    service_runtime.configure_workflow_runtime()

    config_store_provider.assert_not_called()
    render_provider.assert_not_called()
    ztp_provider.assert_not_called()
    storage_provider.assert_not_called()
    assert get_config_store_runtime() is config_stores[0]
    assert get_config_store_runtime() is config_stores[1]
    assert get_render_client() is render_clients[0]
    assert get_render_client() is render_clients[1]
    assert get_ztp_client() is ztp_clients[0]
    assert get_ztp_client() is ztp_clients[1]
    assert get_firmware_storage() is firmware_storages[0]
    assert get_firmware_storage() is firmware_storages[1]
