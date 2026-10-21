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
"""NVIDIA Config Manager common configuration."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_infrastructure.nats import DEFAULT_NATS_API_PREFIX  # noqa: F401

from nv_config_manager.common.config.environment import (  # noqa: F401
    is_aggregate_environment,
    is_local_environment,
)
from nv_config_manager.common.config.http import (  # noqa: F401
    SpiffeJwtUnavailableError,
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
    ensure_local_streams,
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


def config_store_ui_url(config: ConfigParser | None = None) -> str:
    """Get the Config Store UI URL.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The Config Store UI URL
    """
    config = resolve_config(config)
    return config["config_store.client"]["ui_url"]


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
