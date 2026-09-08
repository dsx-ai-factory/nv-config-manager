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
"""Service-owned adapters from INI configuration to plain client settings."""

from __future__ import annotations

from nv_config_manager.temporal.factories.config_store import (
    ConfigStoreClientSettings,
    config_store_client_settings,
)
from nv_config_manager.temporal.factories.device import (
    DeviceConnectionSettings,
    device_connection_settings,
)
from nv_config_manager.temporal.factories.nats import NatsClientSettings, nats_client_settings
from nv_config_manager.temporal.factories.redfish import (
    RedfishClientSettings,
    redfish_client_settings,
)
from nv_config_manager.temporal.factories.redis import redis_settings
from nv_config_manager.temporal.factories.render import RenderClientSettings, render_client_settings
from nv_config_manager.temporal.factories.ticketing import (
    TicketingClientSettings,
    ticketing_client_settings,
)
from nv_config_manager.temporal.factories.ufm import UFMClientSettings, ufm_client_settings

__all__ = [
    "ConfigStoreClientSettings",
    "DeviceConnectionSettings",
    "NatsClientSettings",
    "RedfishClientSettings",
    "RenderClientSettings",
    "TicketingClientSettings",
    "UFMClientSettings",
    "config_store_client_settings",
    "device_connection_settings",
    "nats_client_settings",
    "redfish_client_settings",
    "redis_settings",
    "render_client_settings",
    "ticketing_client_settings",
    "ufm_client_settings",
]
