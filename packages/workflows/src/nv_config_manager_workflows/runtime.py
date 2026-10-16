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
"""Process-local dependencies used by reusable workflow activities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from types import TracebackType
from typing import Any, Final, Literal, Protocol, Self

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient
from nv_config_manager_clients.render import RenderClient
from nv_config_manager_clients.ztp import ZTPClient
from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from nv_config_manager_infrastructure.lock import TokenLockBackend
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.clients.redfish.base import RedfishConnection
from nv_config_manager_workflows.clients.redfish.models import RedfishHost
from nv_config_manager_workflows.clients.ticketing.base import TicketingProvider
from nv_config_manager_workflows.clients.ufm import UFMClient


class RuntimeConfigurationError(ApplicationError):
    """Non-retryable failure caused by missing process runtime configuration."""

    def __init__(self, message: str) -> None:
        """Initialize a permanent activity configuration failure."""
        super().__init__(message, type=self.__class__.__name__, non_retryable=True)


class NatsNotConfiguredError(RuntimeConfigurationError):
    """Raised when NATS publishing is used without an available provider."""


class SlackNotConfiguredError(RuntimeConfigurationError):
    """Raised when Slack configuration is read before startup configured it."""


class UIBaseURLNotConfiguredError(RuntimeConfigurationError):
    """Raised when the NVCM UI base URL is unavailable to an activity."""


class LockNotConfiguredError(RuntimeConfigurationError):
    """Raised when workflow locking is used without an available backend."""


class DCIMNotConfiguredError(RuntimeConfigurationError):
    """Raised when a DCIM client is used without an available provider."""


class DeviceConnectionNotConfiguredError(RuntimeConfigurationError):
    """Raised when a device connection is used without an available provider."""


class RedfishConnectionNotConfiguredError(RuntimeConfigurationError):
    """Raised when a Redfish connection is used without an available provider."""


class UFMClientNotConfiguredError(RuntimeConfigurationError):
    """Raised when a UFM client is used without an available provider."""


class ConfigStoreNotConfiguredError(RuntimeConfigurationError):
    """Raised when Config Store dependencies are used without a provider."""


class RenderClientNotConfiguredError(RuntimeConfigurationError):
    """Raised when a Render client is used without an available provider."""


class ZTPClientNotConfiguredError(RuntimeConfigurationError):
    """Raised when a ZTP client is used without an available provider."""


class FirmwareStorageNotConfiguredError(RuntimeConfigurationError):
    """Raised when firmware storage is used without an available provider."""


class RedisNotConfiguredError(RuntimeConfigurationError):
    """Raised when Redis-backed activity storage has no available provider."""


class TicketingNotConfiguredError(RuntimeConfigurationError):
    """Raised when ticketing is used without an available provider factory."""


class APIBaseURLNotConfiguredError(RuntimeConfigurationError):
    """Raised when the external workflow API URL was not configured at startup."""


class NatsPublisher(Protocol):
    """Narrow publishing capability required by the NATS archive activity."""

    server: str

    async def publish(self, subject: str, message: str, stream: str | None = None) -> None:
        """Publish ``message`` to a NATS JetStream subject."""


class LockBackend(Protocol):
    """Distributed lock operations required by workflow lock activities."""

    async def acquire(
        self,
        name: str,
        token: str,
        *,
        timeout: int,
        blocking_timeout: float | None = None,
        blocking: bool = True,
    ) -> bool:
        """Acquire or refresh the lock identified by ``name``."""

    async def renew(self, name: str, token: str, *, timeout: int) -> bool:
        """Renew a lock held by ``token``."""

    async def release(self, name: str, token: str) -> bool:
        """Release a lock held by ``token``."""


class FirmwareStorage(Protocol):
    """Narrow object-storage capability required by render validation."""

    async def __aenter__(self) -> Self:
        """Enter the service-owned storage client lifetime."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the service-owned storage client lifetime."""

    async def firmware_exists(self, platform: str, image: str) -> bool:
        """Return whether the requested firmware image exists."""


class RedisCache(Protocol):
    """Narrow Redis capability required by diagnostics and ticketing activities."""

    async def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None,
        serialize: bool = True,
    ) -> None:
        """Store a value with the requested serialization and expiration."""

    async def get(self, key: str, deserialize: bool = True) -> Any | None:
        """Read a value with the requested deserialization behavior."""


@dataclass(frozen=True, slots=True)
class NatsRuntime:
    """NATS publisher and routing values used by the archive activity."""

    publisher: NatsPublisher
    stream: str
    subject: str


@dataclass(frozen=True, slots=True)
class SlackRuntime:
    """Slack credentials and default destination used by notification activities."""

    token: str
    channel: str


type ConfigStoreClientFactory = Callable[[ConfigStoreType], ConfigStoreClient | None]


@dataclass(frozen=True, slots=True)
class ConfigStoreRuntime:
    """Config Store client factory and scalar settings from one config snapshot."""

    client_factory: ConfigStoreClientFactory
    ui_url: str
    default_user_domain: str

    def client(self, file_type: ConfigStoreType) -> ConfigStoreClient:
        """Create a client for ``file_type`` or raise a named configuration error."""
        client = self.client_factory(file_type)
        if client is None:
            raise ConfigStoreNotConfiguredError("Config Store client is disabled or incomplete")
        return client


type NatsRuntimeProvider = Callable[[], NatsRuntime | None]
type SlackRuntimeProvider = Callable[[], SlackRuntime | None]
type UIBaseURLProvider = Callable[[], str | None]
type LockBackendProvider = Callable[[], LockBackend]
type DCIMClientProvider = Callable[[], DCIMClient]
type DeviceConnectionProvider = Callable[[NetworkDeviceData], NetworkConnection | None]
type RedfishCredentialRole = Literal["default", "config_manager"]
type RedfishConnectionProvider = Callable[
    [RedfishHost, RedfishCredentialRole], RedfishConnection | None
]
type UFMClientProvider = Callable[[str, str | None], UFMClient | None]
type ConfigStoreRuntimeProvider = Callable[[], ConfigStoreRuntime | None]
type RenderClientProvider = Callable[[], RenderClient | None]
type ZTPClientProvider = Callable[[], ZTPClient | None]
type FirmwareStorageProvider = Callable[[], FirmwareStorage | None]
type RedisClientProvider = Callable[[], RedisCache | None]
type TicketingProviderFactory = Callable[[str], TicketingProvider | None]
type APIBaseURLProvider = Callable[[], str | None]


class _Unset:
    """Distinguish omitted startup configuration from an intentionally disabled resource."""


_UNSET: Final = _Unset()
_NOOP_LOCK_BACKEND: Final[LockBackend] = TokenLockBackend(None)

_nats_provider: NatsRuntimeProvider | None | _Unset = _UNSET
_slack_provider: SlackRuntimeProvider | None | _Unset = _UNSET
_ui_base_url_provider: UIBaseURLProvider | None | _Unset = _UNSET
_lock_backend_provider: LockBackendProvider | _Unset = _UNSET
_dcim_client_provider: DCIMClientProvider | None | _Unset = _UNSET
_device_connection_provider: DeviceConnectionProvider | None | _Unset = _UNSET
_redfish_connection_provider: RedfishConnectionProvider | None | _Unset = _UNSET
_ufm_client_provider: UFMClientProvider | None | _Unset = _UNSET
_config_store_runtime_provider: ConfigStoreRuntimeProvider | None | _Unset = _UNSET
_render_client_provider: RenderClientProvider | None | _Unset = _UNSET
_ztp_client_provider: ZTPClientProvider | None | _Unset = _UNSET
_firmware_storage_provider: FirmwareStorageProvider | None | _Unset = _UNSET
_redis_client_provider: RedisClientProvider | None | _Unset = _UNSET
_ticketing_provider_factory: TicketingProviderFactory | None | _Unset = _UNSET
_api_base_url_provider: APIBaseURLProvider | None | _Unset = _UNSET


def configure_nats(provider: NatsRuntimeProvider | None) -> None:
    """Configure NATS publishing, or explicitly disable it with ``None``."""
    global _nats_provider  # noqa: PLW0603
    _nats_provider = provider


def configure_slack(provider: SlackRuntimeProvider | None) -> None:
    """Configure Slack notifications, or explicitly disable them with ``None``."""
    global _slack_provider  # noqa: PLW0603
    _slack_provider = provider


def configure_ui_base_url(provider: UIBaseURLProvider | None) -> None:
    """Configure the NVCM UI URL, or explicitly disable it with ``None``."""
    global _ui_base_url_provider  # noqa: PLW0603
    _ui_base_url_provider = provider


def _noop_lock_backend() -> LockBackend:
    """Return the stable infrastructure-owned no-op lock backend."""
    return _NOOP_LOCK_BACKEND


def configure_lock_backend(provider: LockBackendProvider | None) -> None:
    """Configure workflow locking, or explicitly use no-op locking with ``None``."""
    global _lock_backend_provider  # noqa: PLW0603
    _lock_backend_provider = _noop_lock_backend if provider is None else provider


def configure_dcim_client(provider: DCIMClientProvider | None) -> None:
    """Configure DCIM client creation, or explicitly disable it with ``None``."""
    global _dcim_client_provider  # noqa: PLW0603
    _dcim_client_provider = provider


def configure_device_connection(provider: DeviceConnectionProvider | None) -> None:
    """Configure device connection creation, or explicitly disable it with ``None``."""
    global _device_connection_provider  # noqa: PLW0603
    _device_connection_provider = provider


def configure_redfish_connection(provider: RedfishConnectionProvider | None) -> None:
    """Configure Redfish connection creation, or explicitly disable it with ``None``."""
    global _redfish_connection_provider  # noqa: PLW0603
    _redfish_connection_provider = provider


def configure_ufm_client(provider: UFMClientProvider | None) -> None:
    """Configure UFM client creation, or explicitly disable it with ``None``."""
    global _ufm_client_provider  # noqa: PLW0603
    _ufm_client_provider = provider


def configure_config_store(provider: ConfigStoreRuntimeProvider | None) -> None:
    """Configure Config Store dependencies, or explicitly disable them with ``None``."""
    global _config_store_runtime_provider  # noqa: PLW0603
    _config_store_runtime_provider = provider


def configure_render_client(provider: RenderClientProvider | None) -> None:
    """Configure Render client creation, or explicitly disable it with ``None``."""
    global _render_client_provider  # noqa: PLW0603
    _render_client_provider = provider


def configure_ztp_client(provider: ZTPClientProvider | None) -> None:
    """Configure ZTP client creation, or explicitly disable it with ``None``."""
    global _ztp_client_provider  # noqa: PLW0603
    _ztp_client_provider = provider


def configure_firmware_storage(provider: FirmwareStorageProvider | None) -> None:
    """Configure firmware storage, or explicitly disable it with ``None``."""
    global _firmware_storage_provider  # noqa: PLW0603
    _firmware_storage_provider = provider


def configure_redis_client(provider: RedisClientProvider | None) -> None:
    """Configure Redis client creation, or explicitly disable it with ``None``."""
    global _redis_client_provider  # noqa: PLW0603
    _redis_client_provider = provider


def configure_ticketing(provider: TicketingProviderFactory | None) -> None:
    """Configure ticketing provider creation, or explicitly disable it with ``None``."""
    global _ticketing_provider_factory  # noqa: PLW0603
    _ticketing_provider_factory = provider


def configure_api_base_url(provider: APIBaseURLProvider | None) -> None:
    """Configure the external API URL, including an intentionally blank URL."""
    global _api_base_url_provider  # noqa: PLW0603
    _api_base_url_provider = provider


def get_nats_runtime() -> NatsRuntime:
    """Return current NATS publishing dependencies or raise a named error."""
    provider = _nats_provider
    if isinstance(provider, _Unset):
        raise NatsNotConfiguredError(
            "NATS runtime is not configured. Call configure_nats(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        raise NatsNotConfiguredError("NATS runtime is disabled")

    runtime = provider()
    if runtime is None or not runtime.stream:
        raise NatsNotConfiguredError("NATS runtime is disabled or incomplete")
    return runtime


def get_slack_runtime() -> SlackRuntime | None:
    """Return current Slack settings, including an intentional disabled state."""
    provider = _slack_provider
    if isinstance(provider, _Unset):
        raise SlackNotConfiguredError(
            "Slack runtime is not configured. Call configure_slack(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        return None
    return provider()


def get_ui_base_url() -> str:
    """Return the current NVCM UI base URL or raise a named error."""
    provider = _ui_base_url_provider
    if isinstance(provider, _Unset):
        raise UIBaseURLNotConfiguredError(
            "UI base URL is not configured. Call configure_ui_base_url(provider) or "
            "configure_runtime() at worker startup."
        )
    if provider is None:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")

    url = provider()
    if not url:
        raise UIBaseURLNotConfiguredError("UI base URL is disabled")
    return url


def get_lock_backend() -> LockBackend:
    """Return the current workflow lock backend or raise a named error."""
    provider = _lock_backend_provider
    if isinstance(provider, _Unset):
        raise LockNotConfiguredError(
            "Workflow lock backend is not configured. Call configure_lock_backend(provider) or "
            "configure_runtime() at worker startup."
        )
    return provider()


def get_dcim_client() -> DCIMClient:
    """Create a current DCIM client or raise a named configuration error."""
    provider = _dcim_client_provider
    if isinstance(provider, _Unset):
        raise DCIMNotConfiguredError(
            "DCIM client is not configured. Call configure_dcim_client(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise DCIMNotConfiguredError("DCIM client is disabled")

    client = provider()
    if client is None:
        raise DCIMNotConfiguredError("DCIM client is disabled or incomplete")
    return client


def get_device_connection(device_data: NetworkDeviceData) -> NetworkConnection:
    """Create a current device connection or raise a named configuration error."""
    provider = _device_connection_provider
    if isinstance(provider, _Unset):
        raise DeviceConnectionNotConfiguredError(
            "Device connection is not configured. Call "
            "configure_device_connection(provider) or configure_runtime() at process startup."
        )
    if provider is None:
        raise DeviceConnectionNotConfiguredError("Device connection is disabled")

    connection = provider(device_data)
    if connection is None:
        raise DeviceConnectionNotConfiguredError("Device connection is disabled or incomplete")
    return connection


def get_redfish_connection(
    host: RedfishHost,
    credential_role: RedfishCredentialRole,
) -> RedfishConnection:
    """Create a current Redfish connection or raise a named configuration error."""
    if credential_role not in ("default", "config_manager"):
        raise ValueError(f"Unsupported Redfish credential role: {credential_role!r}")

    provider = _redfish_connection_provider
    if isinstance(provider, _Unset):
        raise RedfishConnectionNotConfiguredError(
            "Redfish connection is not configured. Call "
            "configure_redfish_connection(provider) or configure_runtime() at process startup."
        )
    if provider is None:
        raise RedfishConnectionNotConfiguredError("Redfish connection is disabled")

    connection = provider(host, credential_role)
    if connection is None:
        raise RedfishConnectionNotConfiguredError("Redfish connection is disabled or incomplete")
    return connection


def get_ufm_client(host: str, site: str | None = None) -> UFMClient:
    """Create a current UFM client or raise a named configuration error."""
    provider = _ufm_client_provider
    if isinstance(provider, _Unset):
        raise UFMClientNotConfiguredError(
            "UFM client is not configured. Call configure_ufm_client(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise UFMClientNotConfiguredError("UFM client is disabled")

    client = provider(host, site)
    if client is None:
        raise UFMClientNotConfiguredError("UFM client is disabled or incomplete")
    return client


def get_config_store_runtime() -> ConfigStoreRuntime:
    """Return current Config Store dependencies or raise a named configuration error."""
    provider = _config_store_runtime_provider
    if isinstance(provider, _Unset):
        raise ConfigStoreNotConfiguredError(
            "Config Store runtime is not configured. Call configure_config_store(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise ConfigStoreNotConfiguredError("Config Store runtime is disabled")

    runtime = provider()
    if runtime is None or not runtime.ui_url:
        raise ConfigStoreNotConfiguredError("Config Store runtime is disabled or incomplete")
    return runtime


def get_render_client() -> RenderClient:
    """Create a current Render client or raise a named configuration error."""
    provider = _render_client_provider
    if isinstance(provider, _Unset):
        raise RenderClientNotConfiguredError(
            "Render client is not configured. Call configure_render_client(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise RenderClientNotConfiguredError("Render client is disabled")

    client = provider()
    if client is None:
        raise RenderClientNotConfiguredError("Render client is disabled or incomplete")
    return client


def get_ztp_client() -> ZTPClient:
    """Create a current ZTP client or raise a named configuration error."""
    provider = _ztp_client_provider
    if isinstance(provider, _Unset):
        raise ZTPClientNotConfiguredError(
            "ZTP client is not configured. Call configure_ztp_client(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise ZTPClientNotConfiguredError("ZTP client is disabled")

    client = provider()
    if client is None:
        raise ZTPClientNotConfiguredError("ZTP client is disabled or incomplete")
    return client


def get_firmware_storage() -> FirmwareStorage:
    """Create current firmware storage or raise a named configuration error."""
    provider = _firmware_storage_provider
    if isinstance(provider, _Unset):
        raise FirmwareStorageNotConfiguredError(
            "Firmware storage is not configured. Call configure_firmware_storage(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise FirmwareStorageNotConfiguredError("Firmware storage is disabled")

    storage = provider()
    if storage is None:
        raise FirmwareStorageNotConfiguredError("Firmware storage is disabled or incomplete")
    return storage


def get_redis_client() -> RedisCache:
    """Create a current Redis client or raise a named configuration error."""
    provider = _redis_client_provider
    if isinstance(provider, _Unset):
        raise RedisNotConfiguredError(
            "Redis client is not configured. Call configure_redis_client(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise RedisNotConfiguredError("Redis client is disabled")

    client = provider()
    if client is None:
        raise RedisNotConfiguredError("Redis client is disabled or incomplete")
    return client


def get_ticketing_provider(platform: str) -> TicketingProvider:
    """Create the current provider for ``platform`` or raise a named error."""
    factory = _ticketing_provider_factory
    if isinstance(factory, _Unset):
        raise TicketingNotConfiguredError(
            "Ticketing is not configured. Call configure_ticketing(provider) or "
            "configure_runtime() at process startup."
        )
    if factory is None:
        raise TicketingNotConfiguredError("Ticketing is disabled")

    provider = factory(platform)
    if provider is None:
        raise TicketingNotConfiguredError("Ticketing is disabled or incomplete")
    return provider


def get_api_base_url() -> str:
    """Return the external API URL, preserving an intentional blank value."""
    provider = _api_base_url_provider
    if isinstance(provider, _Unset):
        raise APIBaseURLNotConfiguredError(
            "API base URL is not configured. Call configure_api_base_url(provider) or "
            "configure_runtime() at process startup."
        )
    if provider is None:
        raise APIBaseURLNotConfiguredError("API base URL is disabled")

    url = provider()
    if url is None:
        raise APIBaseURLNotConfiguredError("API base URL is disabled or incomplete")
    return url


def configure_runtime(
    *,
    nats_provider: NatsRuntimeProvider | None,
    slack_provider: SlackRuntimeProvider | None,
    ui_base_url_provider: UIBaseURLProvider | None,
    lock_backend_provider: LockBackendProvider | None,
    dcim_client_provider: DCIMClientProvider | None = None,
    device_connection_provider: DeviceConnectionProvider | None = None,
    redfish_connection_provider: RedfishConnectionProvider | None = None,
    ufm_client_provider: UFMClientProvider | None = None,
    config_store_runtime_provider: ConfigStoreRuntimeProvider | None = None,
    render_client_provider: RenderClientProvider | None = None,
    ztp_client_provider: ZTPClientProvider | None = None,
    firmware_storage_provider: FirmwareStorageProvider | None = None,
    redis_client_provider: RedisClientProvider | None = None,
    ticketing_provider_factory: TicketingProviderFactory | None = None,
    api_base_url_provider: APIBaseURLProvider | None = None,
) -> None:
    """Apply every currently supported workflow activity dependency."""
    configure_nats(nats_provider)
    configure_slack(slack_provider)
    configure_ui_base_url(ui_base_url_provider)
    configure_lock_backend(lock_backend_provider)
    configure_dcim_client(dcim_client_provider)
    configure_device_connection(device_connection_provider)
    configure_redfish_connection(redfish_connection_provider)
    configure_ufm_client(ufm_client_provider)
    configure_config_store(config_store_runtime_provider)
    configure_render_client(render_client_provider)
    configure_ztp_client(ztp_client_provider)
    configure_firmware_storage(firmware_storage_provider)
    configure_redis_client(redis_client_provider)
    configure_ticketing(ticketing_provider_factory)
    configure_api_base_url(api_base_url_provider)
