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
"""Shared fixtures for the independently runnable workflows package."""

from collections.abc import Generator
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import Mock, patch

import pytest
from aiohttp import ClientResponse
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_clients.render import RenderClient
from nv_config_manager_clients.ztp import ZTPClient
from nv_config_manager_dcim.api import DCIMClient

from nv_config_manager_workflows import runtime as runtime_module
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.ticketing.base import TicketingProvider
from nv_config_manager_workflows.clients.ufm import UFMClient
from nv_config_manager_workflows.runtime import (
    ConfigStoreRuntime,
    NatsRuntime,
    configure_runtime,
)

_CLIENT_RESPONSE_INIT = ClientResponse.__init__


def _client_response_init_with_stream_writer(
    self: ClientResponse, *args: Any, **kwargs: Any
) -> None:
    """Bridge aioresponses to the aiohttp 3.14 ClientResponse signature."""
    kwargs.setdefault("stream_writer", Mock(output_size=0))
    _CLIENT_RESPONSE_INIT(self, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def aiohttp_mock_response_compatibility() -> Generator[None]:
    """Supply the argument omitted by the latest aioresponses release."""
    with patch.object(ClientResponse, "__init__", _client_response_init_with_stream_writer):
        yield


class _TestNatsPublisher:
    """No-I/O NATS publisher used by the package test environment."""

    server = "nats://test.invalid:4222"

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Accept a publish without contacting NATS."""


class _TestLockBackend:
    """No-I/O lock backend used by the package test environment."""

    async def acquire(
        self,
        name: str,
        token: str,
        *,
        timeout: int,
        blocking_timeout: float | None = None,
        blocking: bool = True,
    ) -> bool:
        """Acquire every test lock."""
        return True

    async def renew(self, name: str, token: str, *, timeout: int) -> bool:
        """Renew every test lock."""
        return True

    async def release(self, name: str, token: str) -> bool:
        """Release every test lock."""
        return True


class _TestFirmwareStorage:
    """No-I/O firmware storage used by the package test environment."""

    async def __aenter__(self) -> Self:
        """Enter the test storage lifetime."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the test storage lifetime."""

    async def firmware_exists(self, platform: str, image: str) -> bool:
        """Report every test firmware image as available."""
        return True


@pytest.fixture(autouse=True)
def configured_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install isolated, no-I/O defaults for package and plugin activity tests."""
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ui_base_url_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_lock_backend_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_dcim_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_device_connection_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_redfish_connection_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ufm_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_config_store_runtime_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_render_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ztp_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_firmware_storage_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_redis_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ticketing_provider_factory", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_api_base_url_provider", runtime_module._UNSET)

    nats = NatsRuntime(
        publisher=_TestNatsPublisher(),
        stream="test-workflow-events",
        subject="test.workflow.result",
    )
    lock = _TestLockBackend()
    dcim_client = cast(DCIMClient, object())
    device_connection = cast(NetworkConnection, object())
    redfish_connection = cast(RedfishConnection, object())
    ufm_client = cast(UFMClient, object())
    config_store_client = cast(ConfigStoreClient, object())
    config_store = ConfigStoreRuntime(
        client_factory=lambda _file_type: config_store_client,
        ui_url="https://config-store.test",
        default_user_domain="test.example",
    )
    render_client = cast(RenderClient, object())
    ztp_client = cast(ZTPClient, object())
    firmware_storage = _TestFirmwareStorage()
    redis_client = cast(runtime_module.RedisCache, object())
    ticketing_provider = cast(TicketingProvider, object())
    configure_runtime(
        nats_provider=lambda: nats,
        slack_provider=None,
        ui_base_url_provider=lambda: "https://workflow-ui.test",
        lock_backend_provider=lambda: lock,
        dcim_client_provider=lambda: dcim_client,
        device_connection_provider=lambda _device_data: device_connection,
        redfish_connection_provider=lambda _host, _credential_role: redfish_connection,
        ufm_client_provider=lambda _host, _site: ufm_client,
        config_store_runtime_provider=lambda: config_store,
        render_client_provider=lambda: render_client,
        ztp_client_provider=lambda: ztp_client,
        firmware_storage_provider=lambda: firmware_storage,
        redis_client_provider=lambda: redis_client,
        ticketing_provider_factory=lambda _platform: ticketing_provider,
        api_base_url_provider=lambda: "https://workflow-api.test",
    )


@pytest.fixture
def unconfigured_workflow_runtime(
    configured_workflow_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the explicit startup-not-configured state for failure-path tests."""
    monkeypatch.setattr(runtime_module, "_nats_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_slack_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ui_base_url_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_lock_backend_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_dcim_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_device_connection_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_redfish_connection_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ufm_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_config_store_runtime_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_render_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ztp_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_firmware_storage_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_redis_client_provider", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_ticketing_provider_factory", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_api_base_url_provider", runtime_module._UNSET)
