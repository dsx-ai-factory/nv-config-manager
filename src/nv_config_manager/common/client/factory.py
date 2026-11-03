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
"""Configured client factories for NVIDIA Config Manager services."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager_clients import ConfigStoreType

from nv_config_manager.common.client.config_store import ConfigStoreClient
from nv_config_manager.common.client.dhcp import DHCPClient
from nv_config_manager.common.client.nats import NatsClient
from nv_config_manager.common.client.redis import RedisClient
from nv_config_manager.common.client.render import RenderClient
from nv_config_manager.common.client.temporal import TemporalClient
from nv_config_manager.common.client.ztp import ZTPClient
from nv_config_manager.common.config.client_settings.config_store import (
    config_store_client_settings,
)
from nv_config_manager.common.config.client_settings.nats import nats_client_settings
from nv_config_manager.common.config.client_settings.redis import redis_settings
from nv_config_manager.common.config.client_settings.render import (
    render_client_settings,
)
from nv_config_manager.common.config.loader import resolve_config
from nv_config_manager.dcim import DCIMClient, create_dcim_client


def config_store_client(
    file_type: ConfigStoreType | str = ConfigStoreType.INTENDED,
    config: ConfigParser | None = None,
) -> ConfigStoreClient:
    """Create a ConfigStoreClient for this environment."""
    return ConfigStoreClient(**config_store_client_settings(config, file_type=file_type))


def dhcp_client(config: ConfigParser | None = None) -> DHCPClient:
    """Create a DHCPClient for this environment."""
    return DHCPClient.from_config(resolve_config(config))


def dcim_client(config: ConfigParser | None = None) -> DCIMClient:
    """Create a client for the configured DCIM provider."""
    return create_dcim_client(resolve_config(config))


def redis_client(
    db_key: str = "db",
    config: ConfigParser | None = None,
) -> RedisClient:
    """Create a RedisClient for this environment."""
    return RedisClient(**redis_settings(config, db_key=db_key))


def nats_client(config: ConfigParser | None = None) -> NatsClient:
    """Create a NatsClient for this environment."""
    return NatsClient(**nats_client_settings(config))


def render_client(config: ConfigParser | None = None) -> RenderClient:
    """Create a RenderClient for this environment."""
    return RenderClient(**render_client_settings(config))


def temporal_client(config: ConfigParser | None = None) -> TemporalClient:
    """Create a TemporalClient for this environment."""
    return TemporalClient.from_config(resolve_config(config))


def ztp_client(config: ConfigParser | None = None) -> ZTPClient:
    """Create a ZTPClient for this environment."""
    return ZTPClient.from_config(resolve_config(config))
