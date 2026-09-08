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
"""INI adapter for Redis clients."""

from __future__ import annotations

from configparser import ConfigParser

from nv_config_manager.temporal.factories._config import resolve_config
from nv_config_manager_workflows.clients.redis import RedisSettings


def redis_settings(
    config: ConfigParser | None = None,
    *,
    db_key: str = "db",
) -> RedisSettings:
    """Translate the Redis INI section into reusable-client settings."""
    redis = resolve_config(config)["redis"]
    return {
        "host": redis["host"],
        "port": redis.getint("port", fallback=6379),
        "db": redis.getint(db_key, fallback=0),
        "ssl": redis.getboolean("ssl", fallback=False),
        "password": redis.get("password"),
        "socket_timeout": redis.getint("socket_timeout", fallback=5),
        "socket_connect_timeout": redis.getint("socket_connect_timeout", fallback=5),
    }
