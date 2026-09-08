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
"""Configuration settings for the NVIDIA Config Manager Config Store Service."""

from sqlalchemy.engine import URL

from nv_config_manager.common.config import load_config


class Settings:
    """Application settings loaded from INI file."""

    def __init__(self) -> None:
        """Initialize settings from INI file."""
        self.config = load_config()

        # Database configuration (from INI file)
        db_host = self.config.get("config_store", "database_host")
        db_port = self.config.getint("config_store", "database_port")
        db_name = self.config.get("config_store", "database")
        db_user = self.config.get("config_store", "database_user")
        db_password = self.config.get("config_store", "database_password")
        # Use SQLAlchemy URL object to properly handle special characters in passwords
        self.database_url_obj = URL.create(
            drivername="postgresql+asyncpg",
            username=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
            database=db_name,
        )
        self.database_url = self.database_url_obj.render_as_string()
        self.database_pool_size = 20
        self.database_max_overflow = 10
        self.database_echo = False

        # Storage
        self.compression_level = 6
        self.max_version_history = 1000
        self.retention_days = 365

        # Metrics
        self.enable_metrics = True
        self.metrics_port = 9090

        # CORS - Allow the main UI to make credentialed requests
        if self.config.has_section("config_store.api"):
            cors_origins_str = self.config.get("config_store.api", "cors_origins", fallback="")
            self.cors_origins: list[str] = [
                origin.strip() for origin in cors_origins_str.split(",") if origin.strip()
            ]
        else:
            self.cors_origins = []

        # Redis configuration (from INI file)
        self.redis_host = self.config.get("redis", "host")
        self.redis_port = self.config.getint("redis", "port")
        self.redis_db = self.config.getint("redis", "db")
        self.redis_password = self.config.get("redis", "password", fallback=None)
        self.redis_ssl = self.config.getboolean("redis", "ssl")
        self.redis_socket_timeout = self.config.getint("redis", "socket_timeout")
        self.redis_socket_connect_timeout = self.config.getint("redis", "socket_connect_timeout")

        # DCIM integration. The selected provider owns connection semantics.
        self.dcim_url = self.config.get(
            "dcim",
            "server",
            fallback=self.config.get("nautobot", "server", fallback=""),
        )
        self.dcim_token = self.config.get(
            "dcim",
            "token",
            fallback=self.config.get("nautobot", "token", fallback=""),
        )
        self.dcim_cache_refresh_interval = self.config.getint(
            "dcim",
            "cache_refresh_interval",
            fallback=self.config.getint("nautobot", "cache_refresh_interval", fallback=3600),
        )
        self.dcim_cache_ttl = self.config.getint(
            "dcim",
            "cache_ttl",
            fallback=self.config.getint("nautobot", "cache_ttl", fallback=86400),
        )

    @property
    def nautobot_url(self) -> str:
        """Return the legacy alias for :attr:`dcim_url`."""
        return self.dcim_url

    @property
    def nautobot_token(self) -> str:
        """Return the legacy alias for :attr:`dcim_token`."""
        return self.dcim_token

    @property
    def nautobot_cache_refresh_interval(self) -> int:
        """Return the legacy alias for :attr:`dcim_cache_refresh_interval`."""
        return self.dcim_cache_refresh_interval

    @property
    def nautobot_cache_ttl(self) -> int:
        """Return the legacy alias for :attr:`dcim_cache_ttl`."""
        return self.dcim_cache_ttl


settings = Settings()
