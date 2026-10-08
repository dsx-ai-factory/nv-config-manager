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
"""Translate service configuration into workflow activity dependencies."""

from __future__ import annotations

import asyncio
import os
from types import TracebackType
from typing import cast

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_clients.render import RenderClient
from nv_config_manager_clients.ztp import ZTPClient
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from nv_config_manager_infrastructure.redis import RedisClient
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

from nv_config_manager.common.client import NatsProducer
from nv_config_manager.common.config import (
    is_aggregate_environment,
    load_config,
    nats_archive_config,
)
from nv_config_manager.common.config.client_settings.config_store import (
    config_store_client_settings,
)
from nv_config_manager.common.config.client_settings.redis import redis_settings
from nv_config_manager.common.config.client_settings.render import render_client_settings
from nv_config_manager.common.config.client_settings.ticketing import (
    ticketing_client_settings,
)
from nv_config_manager.common.config.client_settings.ztp import ztp_client_settings
from nv_config_manager.common.config.storage import get_storage_client
from nv_config_manager.common.lock import token_lock_backend
from nv_config_manager.dcim.registry import create_dcim_client
from nv_config_manager.temporal.client.connection import (
    client_connect_options,
    temporal_address,
)
from nv_config_manager.temporal.client.device.base import NetworkConnection
from nv_config_manager.temporal.client.redfish import (
    get_config_manager_connection,
    get_default_connection,
)
from nv_config_manager.temporal.client.ufm import UFMClient
from nv_config_manager.temporal.common.rbac_config import RBACConfig
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.telemetry import get_runtime
from nv_config_manager.ztp.storage import (
    ObjectStorageClient,
    ObjectStorageNotFoundException,
)
from nv_config_manager_workflows.clients.device.base import (
    NetworkConnection as WorkflowNetworkConnection,
)
from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost
from nv_config_manager_workflows.clients.ticketing.base import TicketingProvider
from nv_config_manager_workflows.clients.ticketing.registry import (
    get_ticketing_provider as build_ticketing_provider,
)
from nv_config_manager_workflows.clients.ufm import UFMClient as WorkflowUFMClient
from nv_config_manager_workflows.runtime import (
    ConfigStoreRuntime,
    FirmwareStorage,
    NatsPublisher,
    NatsRuntime,
    RedfishCredentialRole,
    SlackRuntime,
    configure_dcim_client,
    configure_runtime,
    configure_ui_base_url,
)
from nv_config_manager_workflows.schedulers.runtime import (
    BuiltinSchedulerRuntime,
    SchedulerRuntime,
    SchedulerWorkflowRoles,
)


def _nats_runtime() -> NatsRuntime | None:
    """Build current NATS publishing dependencies from service configuration."""
    config = load_config()
    if not config.has_section("nats"):
        return None

    server = config.get("nats", "server", fallback="").strip()
    if not server:
        return None

    stream, subject = nats_archive_config(config)
    return NatsRuntime(
        publisher=cast(NatsPublisher, NatsProducer.from_config(config)),
        stream=stream,
        subject=subject,
    )


def _slack_runtime() -> SlackRuntime | None:
    """Return current Slack settings, or None when notifications are disabled."""
    config = load_config()
    token = config.get("slack", "bot_token", fallback="").strip()
    channel = config.get("slack", "channel_name", fallback="").strip()
    if not token or not channel:
        return None
    return SlackRuntime(token=token, channel=channel)


def _ui_base_url() -> str | None:
    """Return the current NVCM workflow UI base URL."""
    url = load_config().get("temporal", "ui_url", fallback="").strip()
    return url or None


def _api_base_url() -> str:
    """Return the current external workflow API URL, including blank disablement."""
    return load_config().get("temporal", "api_url", fallback="").strip().rstrip("/")


def _redis_client() -> RedisClient:
    """Create an infrastructure Redis client from current service configuration."""
    return RedisClient(**redis_settings(load_config()))


def _ticketing_provider(platform: str) -> TicketingProvider:
    """Create a ticketing provider from current credentials on every call."""
    config = load_config()
    settings = ticketing_client_settings(config, platform=platform)
    return build_ticketing_provider(platform, settings)


def _dcim_client() -> DCIMClient:
    """Create a DCIM client from the service's current provider configuration."""
    return create_dcim_client()


def _device_connection(device_data: NetworkDeviceData) -> WorkflowNetworkConnection:
    """Create a device connection through the service configuration adapter."""
    return NetworkConnection.from_device_data(device_data)


def _redfish_connection(
    host: RedfishHost,
    credential_role: RedfishCredentialRole,
) -> RedfishConnection:
    """Create a Redfish connection through the service credential adapter."""
    if credential_role == "default":
        return get_default_connection(host)
    elif credential_role == "config_manager":
        return get_config_manager_connection(host)
    else:
        raise ValueError(f"Unsupported Redfish credential role: {credential_role!r}")


def _ufm_client(host: str, site: str | None = None) -> WorkflowUFMClient:
    """Create a UFM client through the service credential adapter."""
    return UFMClient(host=host, site=site)


def _config_store_runtime() -> ConfigStoreRuntime:
    """Build Config Store dependencies from one current configuration snapshot."""
    config = load_config()
    ui_url = config["config_store.client"]["ui_url"]
    default_user_domain = os.getenv(
        "NV_CONFIG_MANAGER_DOMAIN",
        "local.config-manager.example.com",
    )

    def client_factory(file_type: ConfigStoreType) -> ConfigStoreClient:
        return ConfigStoreClient(**config_store_client_settings(config, file_type=file_type))

    return ConfigStoreRuntime(
        client_factory=client_factory,
        ui_url=ui_url,
        default_user_domain=default_user_domain,
    )


def _render_client() -> RenderClient:
    """Create a public Render client from current service configuration."""
    return RenderClient(**render_client_settings(load_config()))


def _ztp_client() -> ZTPClient:
    """Create a public ZTP client from current service configuration."""
    return ZTPClient(**ztp_client_settings(load_config()))


class _FirmwareStorageAdapter:
    """Expose only firmware existence from the service object-storage client."""

    def __init__(self, client: ObjectStorageClient) -> None:
        self._client = client
        self._active_client = client

    async def __aenter__(self) -> _FirmwareStorageAdapter:
        """Enter the underlying service storage lifetime."""
        self._active_client = await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the underlying service storage lifetime."""
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def firmware_exists(self, platform: str, image: str) -> bool:
        """Translate storage not-found into the package's boolean capability."""
        try:
            await self._active_client.get_firmware_checksum(platform, image)
        except ObjectStorageNotFoundException:
            return False
        return True


def _firmware_storage() -> FirmwareStorage:
    """Create a narrow firmware-storage adapter from current service configuration."""
    return _FirmwareStorageAdapter(get_storage_client())


async def _scheduler_temporal_client() -> Client:
    """Connect a new scheduler Temporal client from current configuration on every call."""
    return await Client.connect(
        temporal_address(),
        **client_connect_options(),
        data_converter=get_data_converter(),
        interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
        runtime=get_runtime(),
    )


async def _desired_backup_devices() -> set[str]:
    """Read the provider-neutral desired backup set from current configuration."""
    config = load_config()
    # Validate the aggregate flag before creating the DCIM client, as main did.
    is_aggregate_env = is_aggregate_environment(config)
    client = create_dcim_client(config)
    async with client:
        return await client.get_backup_enabled_device_ids(is_aggregate_env)


def _scheduler_workflow_roles(workflow_class_name: str) -> SchedulerWorkflowRoles | None:
    """Translate service RBAC configuration into the scheduler package contract."""
    roles = RBACConfig().get_workflow_roles(workflow_class_name)
    if not roles:
        return None
    return SchedulerWorkflowRoles(
        read_roles=frozenset(roles["read_roles"]),
        execute_roles=frozenset(roles["execute_roles"]),
    )


def build_scheduler_runtime() -> SchedulerRuntime:
    """Build inert shared scheduler adapters without opening service connections."""
    return SchedulerRuntime(
        temporal_client=_scheduler_temporal_client,
        workflow_roles=_scheduler_workflow_roles,
        sleep=asyncio.sleep,
    )


def build_builtin_scheduler_runtime() -> BuiltinSchedulerRuntime:
    """Build inert built-in scheduler adapters without opening service connections."""
    return BuiltinSchedulerRuntime(desired_backup_devices=_desired_backup_devices)


def configure_workflow_runtime() -> None:
    """Install reload-aware workflow activity providers for this service."""
    configure_runtime(
        nats_provider=_nats_runtime,
        slack_provider=_slack_runtime,
        ui_base_url_provider=_ui_base_url,
        lock_backend_provider=token_lock_backend,
        dcim_client_provider=_dcim_client,
        device_connection_provider=_device_connection,
        redfish_connection_provider=_redfish_connection,
        ufm_client_provider=_ufm_client,
        config_store_runtime_provider=_config_store_runtime,
        render_client_provider=_render_client,
        ztp_client_provider=_ztp_client,
        firmware_storage_provider=_firmware_storage,
        redis_client_provider=_redis_client,
        ticketing_provider_factory=_ticketing_provider,
        api_base_url_provider=_api_base_url,
    )


def configure_workflow_ui_runtime() -> None:
    """Install reload-aware dependencies used by workflow API processes."""
    configure_ui_base_url(_ui_base_url)
    configure_dcim_client(_dcim_client)
