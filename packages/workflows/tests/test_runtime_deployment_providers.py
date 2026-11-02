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
"""Tests for process-local deployment workflow runtime dependencies."""

from types import TracebackType
from typing import Self, cast

import pytest
from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_clients.render import RenderClient
from nv_config_manager_clients.ztp import ZTPClient
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter

from nv_config_manager_workflows.runtime import (
    ConfigStoreNotConfiguredError,
    ConfigStoreRuntime,
    FirmwareStorageNotConfiguredError,
    RenderClientNotConfiguredError,
    RuntimeConfigurationError,
    ZTPClientNotConfiguredError,
    configure_config_store,
    configure_firmware_storage,
    configure_render_client,
    configure_runtime,
    configure_ztp_client,
    get_config_store_runtime,
    get_firmware_storage,
    get_render_client,
    get_ztp_client,
)


class StubFirmwareStorage:
    """No-I/O firmware existence capability for provider tests."""

    async def __aenter__(self) -> Self:
        """Enter the stub lifetime."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the stub lifetime."""

    async def firmware_exists(self, platform: str, image: str) -> bool:
        """Report only the representative test image as present."""
        return (platform, image) == ("cumulus-linux", "5.0.0")


def _config_store_runtime(client: ConfigStoreClient) -> ConfigStoreRuntime:
    """Build a complete Config Store runtime around an identity-only client."""
    return ConfigStoreRuntime(
        client_factory=lambda _file_type: client,
        ui_url="https://config-store.example",
        default_user_domain="example.com",
    )


@pytest.mark.parametrize(
    "error_class",
    [
        ConfigStoreNotConfiguredError,
        RenderClientNotConfiguredError,
        ZTPClientNotConfiguredError,
        FirmwareStorageNotConfiguredError,
    ],
)
def test_deployment_configuration_error_name_survives_temporal_serialization(
    error_class: type[RuntimeConfigurationError],
) -> None:
    """Temporal history retains each concrete missing-resource failure type."""
    failure = Failure()

    DefaultFailureConverter().to_failure(
        error_class("missing runtime resource"),
        DefaultPayloadConverter.default,
        failure,
    )

    assert failure.application_failure_info.type == error_class.__name__
    assert failure.application_failure_info.non_retryable is True


def test_unconfigured_deployment_resources_raise_named_errors(
    unconfigured_workflow_runtime: None,
) -> None:
    """Deployment resources fail clearly when worker startup omitted configuration."""
    with pytest.raises(ConfigStoreNotConfiguredError, match="configure_config_store"):
        get_config_store_runtime()
    with pytest.raises(RenderClientNotConfiguredError, match="configure_render_client"):
        get_render_client()
    with pytest.raises(ZTPClientNotConfiguredError, match="configure_ztp_client"):
        get_ztp_client()
    with pytest.raises(FirmwareStorageNotConfiguredError, match="configure_firmware_storage"):
        get_firmware_storage()


def test_explicitly_disabled_deployment_resources_raise_named_errors() -> None:
    """Explicit disablement remains distinct from omitted startup configuration."""
    configure_config_store(None)
    configure_render_client(None)
    configure_ztp_client(None)
    configure_firmware_storage(None)

    with pytest.raises(ConfigStoreNotConfiguredError, match="disabled"):
        get_config_store_runtime()
    with pytest.raises(RenderClientNotConfiguredError, match="disabled"):
        get_render_client()
    with pytest.raises(ZTPClientNotConfiguredError, match="disabled"):
        get_ztp_client()
    with pytest.raises(FirmwareStorageNotConfiguredError, match="disabled"):
        get_firmware_storage()


def test_incomplete_deployment_providers_raise_named_errors() -> None:
    """Reload-aware providers may report resources as unavailable or incomplete."""
    configure_config_store(lambda: None)
    configure_render_client(lambda: None)
    configure_ztp_client(lambda: None)
    configure_firmware_storage(lambda: None)

    with pytest.raises(ConfigStoreNotConfiguredError, match="disabled or incomplete"):
        get_config_store_runtime()
    with pytest.raises(RenderClientNotConfiguredError, match="disabled or incomplete"):
        get_render_client()
    with pytest.raises(ZTPClientNotConfiguredError, match="disabled or incomplete"):
        get_ztp_client()
    with pytest.raises(FirmwareStorageNotConfiguredError, match="disabled or incomplete"):
        get_firmware_storage()

    incomplete_config_store = ConfigStoreRuntime(
        client_factory=lambda _file_type: None,
        ui_url="https://config-store.example",
        default_user_domain="example.com",
    )
    configure_config_store(lambda: incomplete_config_store)
    with pytest.raises(ConfigStoreNotConfiguredError, match="disabled or incomplete"):
        get_config_store_runtime().client(ConfigStoreType.BACKUP)

    configure_config_store(
        lambda: ConfigStoreRuntime(
            client_factory=lambda _file_type: cast(ConfigStoreClient, object()),
            ui_url="",
            default_user_domain="example.com",
        )
    )
    with pytest.raises(ConfigStoreNotConfiguredError, match="disabled or incomplete"):
        get_config_store_runtime()


async def test_deployment_providers_supply_current_values_and_lookup_arguments() -> None:
    """Every getter resolves current provider state and preserves lookup arguments."""
    config_store_clients = [
        cast(ConfigStoreClient, object()),
        cast(ConfigStoreClient, object()),
    ]
    config_store_calls: list[ConfigStoreType] = []
    render_clients = [cast(RenderClient, object()), cast(RenderClient, object())]
    ztp_clients = [cast(ZTPClient, object()), cast(ZTPClient, object())]
    firmware_storages = [StubFirmwareStorage(), StubFirmwareStorage()]
    index = [0]

    def config_store_provider() -> ConfigStoreRuntime:
        current = index[0]

        def client_factory(file_type: ConfigStoreType) -> ConfigStoreClient:
            config_store_calls.append(file_type)
            return config_store_clients[current]

        return ConfigStoreRuntime(
            client_factory=client_factory,
            ui_url=f"https://config-store-v{current + 1}.example",
            default_user_domain=f"example-v{current + 1}.com",
        )

    configure_config_store(config_store_provider)
    configure_render_client(lambda: render_clients[index[0]])
    configure_ztp_client(lambda: ztp_clients[index[0]])
    configure_firmware_storage(lambda: firmware_storages[index[0]])

    first_runtime = get_config_store_runtime()
    assert first_runtime.client(ConfigStoreType.BACKUP) is config_store_clients[0]
    assert first_runtime.ui_url == "https://config-store-v1.example"
    assert first_runtime.default_user_domain == "example-v1.com"
    assert get_render_client() is render_clients[0]
    assert get_ztp_client() is ztp_clients[0]
    assert get_firmware_storage() is firmware_storages[0]

    index[0] = 1

    second_runtime = get_config_store_runtime()
    assert second_runtime.client(ConfigStoreType.INTENDED) is config_store_clients[1]
    assert second_runtime.ui_url == "https://config-store-v2.example"
    assert second_runtime.default_user_domain == "example-v2.com"
    assert get_render_client() is render_clients[1]
    assert get_ztp_client() is ztp_clients[1]
    assert get_firmware_storage() is firmware_storages[1]
    assert config_store_calls == [ConfigStoreType.BACKUP, ConfigStoreType.INTENDED]
    async with get_firmware_storage() as storage:
        assert await storage.firmware_exists("cumulus-linux", "5.0.0") is True


def test_configure_runtime_applies_all_deployment_providers() -> None:
    """The aggregate runtime entry point installs every deployment dependency."""
    config_store_client = cast(ConfigStoreClient, object())
    config_store = _config_store_runtime(config_store_client)
    render_client = cast(RenderClient, object())
    ztp_client = cast(ZTPClient, object())
    firmware_storage = StubFirmwareStorage()

    configure_runtime(
        nats_provider=None,
        slack_provider=None,
        ui_base_url_provider=None,
        lock_backend_provider=None,
        config_store_runtime_provider=lambda: config_store,
        render_client_provider=lambda: render_client,
        ztp_client_provider=lambda: ztp_client,
        firmware_storage_provider=lambda: firmware_storage,
    )

    assert get_config_store_runtime() is config_store
    assert get_config_store_runtime().client(ConfigStoreType.INTENDED) is config_store_client
    assert get_render_client() is render_client
    assert get_ztp_client() is ztp_client
    assert get_firmware_storage() is firmware_storage
