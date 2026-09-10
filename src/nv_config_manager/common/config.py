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
"""NVIDIA Config Manager Common Configuration.

Centralized configuration loading and client factories for all NVIDIA Config Manager services.
All services use the same INI format - see nv-config-manager-chart/sample-nv-config-manager.ini.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Awaitable, Callable
from configparser import ConfigParser
from typing import TYPE_CHECKING, Any

import certifi
import nats
import nats.js.errors

# =============================================================================
# CLIENT IMPORTS
# =============================================================================
from nv_config_manager.common.client import (
    DHCPClient,
    NatsClient,
    TemporalClient,
    ZTPClient,
)

# =============================================================================
# LOGGING (re-exported from nv_config_manager.common.log to avoid circular imports)
# =============================================================================
from nv_config_manager.common.config_loader import (  # noqa: F401
    _load_config,
    clear_config_cache,
    load_config,
    reload_config,
    resolve_config,
    resolve_section,
)
from nv_config_manager.common.http_config import (  # noqa: F401
    DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
    DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
    DEFAULT_CONFIG_MANAGER_NATS_STREAM,
    DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
    DEFAULT_NATS_API_PREFIX,
    DEFAULT_NAUTOBOT_NATS_STREAM,
    DEFAULT_NAUTOBOT_NATS_SUBJECT,
    _nats_section,
    _read_spiffe_jwt,
    config_manager_api_prefix,
    get_internal_auth_headers,
    get_mtls_cert_paths,
    get_service_url,
    nats_archive_config,
    nats_config_manager_api_prefix,
    nats_dcim_change_config,
    nats_device_change_config,
    nats_nautobot_api_prefix,
    nats_nautobot_change_config,
    nats_render_change_config,
    parse_verify_param,
    use_internal_endpoint,
)
from nv_config_manager.common.log import (  # noqa: F401, E402
    LogCategory,
    configure_logging,
    get_logger,
)
from nv_config_manager.dcim import DCIMClient, create_dcim_client
from nv_config_manager.temporal.factories import redis_settings
from nv_config_manager.temporal.factories.config_store import config_store_client_settings
from nv_config_manager.temporal.factories.render import render_client_settings
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.s3 import S3Client
from nv_config_manager.ztp.storage import ObjectStorageClient
from nv_config_manager_workflows.clients import (
    ConfigStoreClient,
    ConfigStoreType,
    RedisClient,
    RenderClient,
)

if TYPE_CHECKING:
    import nats.aio.client
# =============================================================================
# CLIENT FACTORIES
# =============================================================================


def config_store_client(
    file_type: ConfigStoreType | str = ConfigStoreType.INTENDED,
    config: ConfigParser | None = None,
) -> ConfigStoreClient:
    """Create a ConfigStoreClient for this environment.

    Args:
        file_type: ConfigStoreType enum or "intended"/"backup" string
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured ConfigStoreClient instance
    """
    settings = config_store_client_settings(config, file_type=file_type)
    return ConfigStoreClient(**settings)


def config_store_ui_url(config: ConfigParser | None = None) -> str:
    """Get the Config Store UI URL.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The Config Store UI URL
    """
    return resolve_section("config_store.client", config)["ui_url"]


def dhcp_client(config: ConfigParser | None = None) -> DHCPClient:
    """Create a DHCPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured DHCPClient instance
    """
    return DHCPClient.from_config(resolve_config(config))


def dcim_client(config: ConfigParser | None = None) -> DCIMClient:
    """Create a client for the configured DCIM provider.

    Existing deployments without a ``[dcim]`` section select the built-in
    Nautobot provider. New services should use this factory instead of
    constructing a backend-specific client.
    """
    return create_dcim_client(resolve_config(config))


def dcim_cache_ttl(config: ConfigParser | None = None, default: int = 86400) -> int:
    """Return the provider-neutral device metadata cache TTL.

    ``[nautobot] cache_ttl`` remains the legacy fallback until the generic
    configuration rendering in the next implementation checkpoint is in place.
    """
    resolved = resolve_config(config)
    if resolved.has_option("dcim", "cache_ttl"):
        return resolved.getint("dcim", "cache_ttl")
    if resolved.has_option("nautobot", "cache_ttl"):
        return resolved.getint("nautobot", "cache_ttl")
    return default


def redis_client(
    db_key: str = "db",
    config: ConfigParser | None = None,
) -> RedisClient:
    """Create a RedisClient for this environment.

    Args:
        db_key: Key name for database number (default "db", some use "lock_db")
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured RedisClient instance
    """
    settings = redis_settings(config, db_key=db_key)
    return RedisClient(**settings)


def nats_client(config: ConfigParser | None = None) -> NatsClient:
    """Create a NatsClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured NatsClient instance
    """
    return NatsClient.from_config(resolve_config(config))


def render_client(config: ConfigParser | None = None) -> RenderClient:
    """Create a RenderClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured RenderClient instance
    """
    settings = render_client_settings(config)
    return RenderClient(**settings)


def temporal_client(config: ConfigParser | None = None) -> TemporalClient:
    """Create a TemporalClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured TemporalClient instance
    """
    return TemporalClient.from_config(resolve_config(config))


def ztp_client(config: ConfigParser | None = None) -> ZTPClient:
    """Create a ZTPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured ZTPClient instance
    """
    return ZTPClient.from_config(resolve_config(config))


# =============================================================================
# CONNECTION MANAGERS (Singletons)
# =============================================================================


class NATSConnectionManager:
    """Singleton to manage shared NATS connection."""

    _instance: NATSConnectionManager | None = None
    _connection: nats.aio.client.Client | None = None

    def __new__(cls) -> NATSConnectionManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def set_connection(self, connection: nats.aio.client.Client) -> None:
        """Set the shared NATS connection."""
        self._connection = connection

    def get_connection(self) -> nats.aio.client.Client | None:
        """Get the shared NATS connection."""
        return self._connection

    def clear_connection(self) -> None:
        """Clear the shared connection."""
        self._connection = None


# =============================================================================
# NATS CONNECTION
# =============================================================================


async def nats_connection(
    closed_cb: Callable[[], Awaitable[None]] | None = None,
    error_cb: Callable[[Exception], Awaitable[None]] | None = None,
    disconnected_cb: Callable[[], Awaitable[None]] | None = None,
    reconnected_cb: Callable[[], Awaitable[None]] | None = None,
) -> nats.aio.client.Client:
    """Return a connected NATS client for this environment.

    Args:
        closed_cb: Callback when connection is closed
        error_cb: Callback on error
        disconnected_cb: Callback on disconnect
        reconnected_cb: Callback on reconnect

    Returns:
        Connected NATS client
    """
    ssl_context = ssl.create_default_context()
    ssl_context.load_verify_locations(certifi.where())

    nats_config = resolve_section("nats")
    servers = nats_config["server"]
    auth_method = nats_config.get("auth_method", "password")

    options: dict[str, Any] = {
        "tls": ssl_context,
        "connect_timeout": 30,
        "closed_cb": closed_cb,
        "error_cb": error_cb,
        "disconnected_cb": disconnected_cb,
        "reconnected_cb": reconnected_cb,
        "allow_reconnect": True,
        "ping_interval": 50,
    }

    if auth_method == "JWT":
        options["user_credentials"] = nats_config["credentials"]
    elif auth_method == "password":
        if "user" in nats_config:
            options["user"] = nats_config["user"]
        if "password" in nats_config:
            options["password"] = nats_config["password"]

    conn = await nats.connect(servers, **options)

    # Create streams locally if needed
    if nats_config.getboolean("local", fallback=False):
        jetstream = conn.jetstream()
        for stream in ["nv-config-manager", "nautobot"]:
            try:
                await jetstream.stream_info(stream)
            except nats.js.errors.NotFoundError:
                # nv-config-manager uses hierarchical subjects (nv_config_manager.render.events, etc.)
                # nautobot uses exact subject (nautobot_broker_nats publishes to "nautobot")
                subjects = [f"{stream}.>"] if stream == "nv-config-manager" else [stream]
                await jetstream.add_stream(name=stream, subjects=subjects)

    return conn


# STORAGE CLIENT (ZTP)
# =============================================================================


def _nonblank_config_value(value: str | None) -> str | None:
    return value if value and value.strip() else None


def get_storage_client() -> ObjectStorageClient:
    """Return the appropriate storage client based on ZTP configuration.

    Uses [ztp] config values with environment variable fallback:
    - "file": Returns FileStoreClient
    - "s3" or unset: Returns S3Client (default)

    Returns:
        ObjectStorageClient implementation
    """
    config = load_config()
    ztp_config = config["ztp"] if config.has_section("ztp") else {}
    storage_type = ztp_config.get("storage_type") or os.environ.get("STORAGE_TYPE", "s3")
    storage_type = storage_type.lower()

    if storage_type == "file":
        file_store_path = ztp_config.get("file_store_path") or os.environ.get("FILE_STORE_PATH")
        if not file_store_path:
            raise ValueError("storage_type is 'file' but file_store_path is not set.")
        return FileStoreClient(base_path=file_store_path)
    return S3Client(
        bucket=_nonblank_config_value(ztp_config.get("s3_bucket")),
        custom_endpoint=_nonblank_config_value(ztp_config.get("s3_endpoint")),
        region=_nonblank_config_value(ztp_config.get("s3_region")),
        custom_access_key=_nonblank_config_value(ztp_config.get("s3_access_key")),
        custom_secret_key=_nonblank_config_value(ztp_config.get("s3_secret_key")),
    )


# =============================================================================
# ENVIRONMENT HELPERS
# =============================================================================


def is_local_environment() -> bool:
    """Check if running in local development environment."""
    return bool(os.getenv("LOCAL_VENV"))


def is_aggregate_environment(config: ConfigParser | None = None) -> bool:
    """Check if this is an aggregate environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        True if aggregate environment, False otherwise
    """
    config = resolve_config(config)
    if not config.has_section("aggregate"):
        return False
    return config["aggregate"].getboolean("is_aggregate_environment", False)
