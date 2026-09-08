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
"""NVIDIA Config Manager Common Clients - Shared service clients."""

# Config Store Client
from nv_config_manager.common.client.config_store import (
    ConfigFile,
    ConfigFileMetadata,
    ConfigStoreClient,
    ConfigStoreException,
    ConfigStoreFileNotFound,
)

# DHCP Client
from nv_config_manager.common.client.dhcp import DHCPClient, DHCPClientException

# NATS Client
from nv_config_manager.common.client.nats import (
    DEFAULT_NATS_API_PREFIX,
    NatsClient,
    NatsConsumer,
    NatsProducer,
    config_manager_api_prefix,
)

# NVDataflow Client
from nv_config_manager.common.client.nvdataflow import (
    NVDataflowClient,
    NVDataflowException,
)

# Redis Client
from nv_config_manager.common.client.redis import RedisClient

# Render Client
from nv_config_manager.common.client.render import (
    FileCommit,
    RenderClient,
    RenderClientException,
)

# Temporal Client
from nv_config_manager.common.client.temporal import (
    TemporalClient,
    TemporalClientException,
)

# ZTP Client
from nv_config_manager.common.client.ztp import (
    ZTPClient,
    ZTPClientException,
)

__all__ = [
    # Config Store
    "ConfigFile",
    "ConfigFileMetadata",
    "ConfigStoreClient",
    "ConfigStoreException",
    "ConfigStoreFileNotFound",
    # DHCP
    "DHCPClient",
    "DHCPClientException",
    # NATS
    "DEFAULT_NATS_API_PREFIX",
    "NatsClient",
    "NatsConsumer",
    "NatsProducer",
    "config_manager_api_prefix",
    # NVDataflow
    "NVDataflowClient",
    "NVDataflowException",
    # Redis
    "RedisClient",
    # Render
    "FileCommit",
    "RenderClient",
    "RenderClientException",
    # Temporal
    "TemporalClient",
    "TemporalClientException",
    # ZTP
    "ZTPClient",
    "ZTPClientException",
]
