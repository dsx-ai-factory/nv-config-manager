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

from configparser import ConfigParser

from nv_config_manager_clients import ConfigStoreType

# =============================================================================
# CLIENT IMPORTS
# =============================================================================
from nv_config_manager.common.client import (  # noqa: F401
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
from nv_config_manager.common.config.client_settings.config_store import (
    config_store_client_settings,
)
from nv_config_manager.common.config.client_settings.nats import nats_client_settings
from nv_config_manager.common.config.client_settings.redis import redis_settings
from nv_config_manager.common.config.client_settings.render import (
    render_client_settings,
)
from nv_config_manager.common.config.environment import (  # noqa: F401
    is_aggregate_environment,
    is_local_environment,
)
from nv_config_manager.common.config.http import (  # noqa: F401
    _read_spiffe_jwt,
    get_internal_auth_headers,
    get_mtls_cert_paths,
    get_service_url,
    parse_verify_param,
    use_internal_endpoint,
)
from nv_config_manager.common.config.loader import (  # noqa: F401
    clear_config_cache,
    load_config,
    reload_config,
    resolve_config,
)
from nv_config_manager.common.config.nats import (  # noqa: F401
    NATSConnectionManager,
    nats_archive_config,
    nats_config_manager_api_prefix,
    nats_connection,
    nats_dcim_change_config,
    nats_device_change_config,
    nats_nautobot_api_prefix,
    nats_nautobot_change_config,
    nats_render_change_config,
)
from nv_config_manager.common.config.storage import (  # noqa: F401
    get_storage_client,
)
from nv_config_manager.dcim import DCIMClient, create_dcim_client

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

    return ConfigStoreClient(**config_store_client_settings(config, file_type=file_type))


def config_store_ui_url(config: ConfigParser | None = None) -> str:
    """Get the Config Store UI URL.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The Config Store UI URL
    """
    config = resolve_config(config)
    return config["config_store.client"]["ui_url"]


def dhcp_client(config: ConfigParser | None = None) -> DHCPClient:
    """Create a DHCPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured DHCPClient instance
    """

    config = resolve_config(config)
    return DHCPClient.from_config(config)


def dcim_client(config: ConfigParser | None = None) -> DCIMClient:
    """Create a client for the configured DCIM provider.

    Existing deployments without a ``[dcim]`` section select the built-in
    Nautobot provider. New services should use this factory instead of
    constructing a backend-specific client.
    """

    config = resolve_config(config)
    return create_dcim_client(config)


def dcim_cache_ttl(config: ConfigParser | None = None, default: int = 86400) -> int:
    """Return the provider-neutral device metadata cache TTL.

    ``[nautobot] cache_ttl`` remains the legacy fallback until the generic
    configuration rendering in the next implementation checkpoint is in place.
    """

    config = resolve_config(config)

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

    return RedisClient(**redis_settings(config, db_key=db_key))


def nats_client(config: ConfigParser | None = None) -> NatsClient:
    """Create a NatsClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured NatsClient instance
    """

    return NatsClient(**nats_client_settings(config))


def render_client(config: ConfigParser | None = None) -> RenderClient:
    """Create a RenderClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured RenderClient instance
    """

    return RenderClient(**render_client_settings(config))


def temporal_client(config: ConfigParser | None = None) -> TemporalClient:
    """Create a TemporalClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured TemporalClient instance
    """

    config = resolve_config(config)
    return TemporalClient.from_config(config)


def ztp_client(config: ConfigParser | None = None) -> ZTPClient:
    """Create a ZTPClient for this environment.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Configured ZTPClient instance
    """

    config = resolve_config(config)
    return ZTPClient.from_config(config)
