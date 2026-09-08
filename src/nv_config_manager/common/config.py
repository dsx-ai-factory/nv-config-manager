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
from configparser import ConfigParser, SectionProxy
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import certifi
import nats
import nats.js.errors

# =============================================================================
# CLIENT IMPORTS
# =============================================================================
from nv_config_manager.common.client import (
    DEFAULT_NATS_API_PREFIX,
    ConfigStoreClient,
    DHCPClient,
    NatsClient,
    RedisClient,
    RenderClient,
    TemporalClient,
    ZTPClient,
    config_manager_api_prefix,
)

# =============================================================================
# LOGGING (re-exported from nv_config_manager.common.log to avoid circular imports)
# =============================================================================
from nv_config_manager.common.ini import FileFingerprint, file_fingerprint
from nv_config_manager.common.log import (  # noqa: F401, E402
    LogCategory,
    configure_logging,
    get_logger,
)
from nv_config_manager.dcim import DCIMClient, create_dcim_client
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.s3 import S3Client
from nv_config_manager.ztp.storage import ObjectStorageClient

if TYPE_CHECKING:
    import nats.aio.client


# =============================================================================
# ENUMS
# =============================================================================


class ConfigStoreType(Enum):
    """Config store file types."""

    BACKUP = "backup"
    INTENDED = "intended"


# =============================================================================
# CONFIG LOADING
# =============================================================================


@lru_cache(maxsize=1)
def _load_config(
    config_path: str,
    _fingerprint: FileFingerprint | None,
) -> ConfigParser:
    """Parse one version of the unified INI file."""
    config = ConfigParser(interpolation=None, delimiters=("=",))
    config.read(config_path)
    return config


def load_config() -> ConfigParser:
    """Load the unified nv-config-manager.ini configuration.

    All services use the same INI file. The path is determined by:
    1. NV_CONFIG_MANAGER_INI environment variable
    2. Default: /etc/vault/nv-config-manager.ini

    The parsed result is reused while the file is unchanged. Direct writes and
    Kubernetes Secret-volume symlink swaps invalidate the cache automatically.

    Returns:
        Loaded ConfigParser instance for the current file version
    """
    config_path = os.getenv("NV_CONFIG_MANAGER_INI", "/etc/vault/nv-config-manager.ini")
    return _load_config(config_path, file_fingerprint(config_path))


def clear_config_cache() -> None:
    """Clear the parsed INI cache without reading the file again."""
    _load_config.cache_clear()


def reload_config() -> ConfigParser:
    """Force reload the configuration (clears cache)."""
    clear_config_cache()
    return load_config()


# =============================================================================
# CONFIG HELPERS
# =============================================================================


DEFAULT_CONFIG_MANAGER_NATS_STREAM = "nv-config-manager"
DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT = "nv-config-manager.nautobotchange"
DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT = "nv-config-manager.devicechange"
DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT = "nv-config-manager.workflow.result"
DEFAULT_NAUTOBOT_NATS_STREAM = "nautobot"
DEFAULT_NAUTOBOT_NATS_SUBJECT = "nautobot"


def _nats_section(config: ConfigParser | None = None) -> SectionProxy:
    if config is None:
        config = load_config()
    return config["nats"]


def nats_config_manager_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for the config-manager-owned stream.

    Render-change, device-change and archive events are subjects on this one stream,
    so they share its prefix.
    """
    return config_manager_api_prefix(_nats_section(config))


def nats_render_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for render-triggering changes."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "render_change_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "render_change_subject",
        DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
    )
    return stream, subject


def nats_device_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for device-change notifications."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "device_change_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "device_change_subject",
        DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
    )
    return stream, subject


def nats_archive_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for workflow archive events."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "archive_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "archive_subject",
        DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
    )
    return stream, subject


def nats_nautobot_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for Nautobot changelog events."""
    nats_config = _nats_section(config)
    stream = nats_config.get("nautobot_stream", DEFAULT_NAUTOBOT_NATS_STREAM)
    subject = nats_config.get("nautobot_subject", DEFAULT_NAUTOBOT_NATS_SUBJECT)
    return stream, subject


def nats_nautobot_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for Nautobot changelog events."""
    nats_config = _nats_section(config)
    return nats_config.get("nautobot_api_prefix", DEFAULT_NATS_API_PREFIX)


def nats_dcim_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the stream and subject for provider-neutral DCIM change events.

    The legacy Nautobot settings remain the fallback while its publisher is
    upgraded. External providers should configure ``dcim_change_*`` directly.
    """
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "dcim_change_stream",
        nats_config.get("nautobot_stream", DEFAULT_NAUTOBOT_NATS_STREAM),
    )
    subject = nats_config.get(
        "dcim_change_subject",
        nats_config.get("nautobot_subject", DEFAULT_NAUTOBOT_NATS_SUBJECT),
    )
    return stream, subject


def parse_verify_param(
    config_section: SectionProxy,
    key: str = "verify",
    fallback: bool = True,
) -> bool | str:
    """Parse SSL verify parameter from config.

    Handles boolean values ("true", "false", "yes", "no", "1", "0")
    or string paths to CA certificate files.

    Args:
        config_section: Config section to read from
        key: Key name for the verify parameter
        fallback: Default value if key doesn't exist

    Returns:
        Boolean True/False or string path to CA cert file
    """
    try:
        return config_section.getboolean(key, fallback=fallback)
    except ValueError:
        return config_section[key]


def get_mtls_cert_paths(config: ConfigParser | None = None) -> tuple[str, str] | None:
    """Get mTLS certificate paths from config.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Tuple of (cert_path, key_path) or None if not configured
    """
    if config is None:
        config = load_config()

    if not config.has_section("mtls"):
        return None

    mtls = config["mtls"]
    cert_path = mtls.get("tls_client_cert_path")
    key_path = mtls.get("tls_client_key_path")

    if cert_path and key_path:
        return (cert_path, key_path)
    return None


def use_internal_endpoint(section: str, config: ConfigParser | None = None) -> bool:
    """Check if a service should use internal endpoints.

    Args:
        section: Config section name (e.g., "render", "temporal")
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        True if use_internal_endpoint is set, False otherwise
    """
    if config is None:
        config = load_config()
    return config[section].getboolean("use_internal_endpoint", fallback=False)


def _read_spiffe_jwt() -> str | None:
    """Read the current JWT-SVID from the file written by spiffe-helper.

    The path is read from ``[auth.spiffe] jwt_svid_path`` in the INI.
    Returns the raw JWT string, or None if SPIFFE is not configured or
    the file is unavailable.
    """
    config = load_config()
    jwt_path = config.get("auth.spiffe", "jwt_svid_path", fallback="")
    if not jwt_path:
        return None
    try:
        with open(jwt_path) as f:
            token = f.read().strip()
        return token or None
    except OSError:
        return None


def get_internal_auth_headers(
    service_name: str | None = None,
    group: str = "nv-config-manager",
) -> dict[str, str]:
    """Get auth headers for internal service-to-service calls.

    When SPIFFE is configured (``[auth.spiffe] jwt_svid_path`` is set),
    reads the JWT-SVID from disk and returns an ``Authorization: Bearer``
    header.  The receiving service validates the JWT against the Workload
    API trust bundle.

    When SPIFFE is not configured, falls back to ``X-Auth-Request-*``
    headers for environments that trust the caller's identity headers
    directly (the receiving service must have
    ``[auth] accept_request_headers = true``).

    Callers should invoke this function per-request (not cache the result)
    because JWT-SVIDs have short TTLs and are refreshed on disk by
    spiffe-helper.

    Args:
        service_name: Name of the calling service. If not provided, derives
            from HOSTNAME (e.g., "nv-config-manager-ztp-6c98b9b6cb-xyz" -> "nv-config-manager-ztp")
        group: RBAC group for authorization (default: "nv-config-manager")

    Returns:
        Dict of auth headers to include in HTTP requests
    """
    jwt = _read_spiffe_jwt()
    if jwt:
        return {"Authorization": f"Bearer {jwt}"}

    if service_name:
        caller = service_name
    else:
        caller = os.environ.get("HOSTNAME", "internal-service")
    return {
        "X-Auth-Request-Email": caller,
        "X-Auth-Request-User": caller,
        "X-Auth-Request-Groups": group,
    }


def get_service_url(
    section: str,
    internal_key: str = "api_service",
    external_key: str = "api_url",
    config: ConfigParser | None = None,
) -> str:
    """Get the appropriate service URL based on internal/external config.

    Args:
        section: Config section name
        internal_key: Key for internal URL
        external_key: Key for external URL
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The appropriate URL for the current environment
    """
    if config is None:
        config = load_config()

    if use_internal_endpoint(section, config):
        return config[section][internal_key]
    return config[section][external_key]


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
    if config is None:
        config = load_config()
    return ConfigStoreClient.from_config(config, file_type=file_type)


def config_store_ui_url(config: ConfigParser | None = None) -> str:
    """Get the Config Store UI URL.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The Config Store UI URL
    """
    if config is None:
        config = load_config()
    return config["config_store.client"]["ui_url"]


def dhcp_client(config: ConfigParser | None = None) -> DHCPClient:
    """Create a DHCPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured DHCPClient instance
    """
    if config is None:
        config = load_config()
    return DHCPClient.from_config(config)


def dcim_client(config: ConfigParser | None = None) -> DCIMClient:
    """Create a client for the configured DCIM provider.

    Existing deployments without a ``[dcim]`` section select the built-in
    Nautobot provider. New services should use this factory instead of
    constructing a backend-specific client.
    """
    if config is None:
        config = load_config()
    return create_dcim_client(config)


def dcim_cache_ttl(config: ConfigParser | None = None, default: int = 86400) -> int:
    """Return the provider-neutral device metadata cache TTL.

    ``[nautobot] cache_ttl`` remains the legacy fallback until the generic
    configuration rendering in the next implementation checkpoint is in place.
    """
    if config is None:
        config = load_config()
    if config.has_option("dcim", "cache_ttl"):
        return config.getint("dcim", "cache_ttl")
    if config.has_option("nautobot", "cache_ttl"):
        return config.getint("nautobot", "cache_ttl")
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
    if config is None:
        config = load_config()
    return RedisClient.from_config(config, db_key=db_key)


def nats_client(config: ConfigParser | None = None) -> NatsClient:
    """Create a NatsClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured NatsClient instance
    """
    if config is None:
        config = load_config()
    return NatsClient.from_config(config)


def render_client(config: ConfigParser | None = None) -> RenderClient:
    """Create a RenderClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured RenderClient instance
    """
    if config is None:
        config = load_config()
    return RenderClient.from_config(config)


def temporal_client(config: ConfigParser | None = None) -> TemporalClient:
    """Create a TemporalClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured TemporalClient instance
    """
    if config is None:
        config = load_config()
    return TemporalClient.from_config(config)


def ztp_client(config: ConfigParser | None = None) -> ZTPClient:
    """Create a ZTPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured ZTPClient instance
    """
    if config is None:
        config = load_config()
    return ZTPClient.from_config(config)


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
    config = load_config()
    ssl_context = ssl.create_default_context()
    ssl_context.load_verify_locations(certifi.where())

    nats_config = config["nats"]
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
    if config is None:
        config = load_config()
    if not config.has_section("aggregate"):
        return False
    return config["aggregate"].getboolean("is_aggregate_environment", False)
