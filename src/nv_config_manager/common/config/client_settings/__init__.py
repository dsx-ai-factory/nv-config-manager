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
"""Service-owned adapters from INI configuration to explicit client settings."""

from nv_config_manager.common.config.client_settings.config_store import (
    ConfigStoreClientSettings,
    config_store_client_settings,
)
from nv_config_manager.common.config.client_settings.device import (
    DeviceConnectionSettings,
    device_connection_settings,
)
from nv_config_manager.common.config.client_settings.nats import (
    NatsClientSettings,
    NatsConsumerSettings,
    nats_client_settings,
    nats_consumer_settings,
)
from nv_config_manager.common.config.client_settings.redfish import (
    BmcCredentials,
    RedfishClientSettings,
    RedfishCredentialKind,
    get_bmc_credentials,
    redfish_client_settings,
)
from nv_config_manager.common.config.client_settings.redis import (
    RedisSettings,
    redis_settings,
)
from nv_config_manager.common.config.client_settings.render import (
    RenderClientSettings,
    render_client_settings,
)
from nv_config_manager.common.config.client_settings.ticketing import (
    TicketingClientSettings,
    ticketing_client_settings,
)
from nv_config_manager.common.config.client_settings.ufm import (
    UFMClientSettings,
    ufm_client_settings,
)

__all__ = [
    "BmcCredentials",
    "ConfigStoreClientSettings",
    "DeviceConnectionSettings",
    "NatsClientSettings",
    "NatsConsumerSettings",
    "RedisSettings",
    "RedfishClientSettings",
    "RedfishCredentialKind",
    "RenderClientSettings",
    "TicketingClientSettings",
    "UFMClientSettings",
    "config_store_client_settings",
    "device_connection_settings",
    "get_bmc_credentials",
    "nats_client_settings",
    "nats_consumer_settings",
    "redis_settings",
    "redfish_client_settings",
    "render_client_settings",
    "ticketing_client_settings",
    "ufm_client_settings",
]
