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
"""Service INI factory and compatibility exports for redis."""

from __future__ import annotations

from configparser import ConfigParser
from typing import Self

from nv_config_manager_infrastructure.redis import RedisClient as _RedisClient


class RedisClient(_RedisClient):
    """Application client retaining the legacy INI factory."""

    @classmethod
    def from_config(
        cls,
        config: ConfigParser,
        db_key: str = "db",
    ) -> Self:
        """Create RedisClient from INI configuration.

        Args:
            config: ConfigParser with redis section
            db_key: Key for database number ("db" or "lock_db")

        Returns:
            Configured RedisClient instance
        """
        return cls(
            host=config.get("redis", "host"),
            port=config.getint("redis", "port", fallback=6379),
            db=config.getint("redis", db_key, fallback=0),
            ssl=config.getboolean("redis", "ssl", fallback=False),
            password=config.get("redis", "password", fallback=None),
            socket_timeout=config.getint("redis", "socket_timeout", fallback=5),
            socket_connect_timeout=config.getint("redis", "socket_connect_timeout", fallback=5),
        )
