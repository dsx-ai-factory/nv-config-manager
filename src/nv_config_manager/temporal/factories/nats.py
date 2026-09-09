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
"""INI adapter for NATS clients."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager.common.config_loader import resolve_config
from nv_config_manager.common.http_config import config_manager_api_prefix


class NatsClientSettings(TypedDict):
    """Constructor settings shared by NATS clients and producers."""

    api_prefix: str
    server: str
    queue: str
    local: bool
    auth_method: str
    user: str | None
    password: str | None
    creds_path: str | None
    default_stream_name: str
    default_stream_subjects: list[str]


def nats_client_settings(config: ConfigParser | None = None) -> NatsClientSettings:
    """Translate the NATS INI section into common constructor settings."""
    nats_config = resolve_config(config)["nats"]
    subjects = [
        subject.strip()
        for subject in nats_config.get("config_manager_subjects", "nv-config-manager.>").split(",")
        if subject.strip()
    ]
    return {
        "api_prefix": config_manager_api_prefix(nats_config),
        "server": nats_config["server"],
        "queue": nats_config.get("queue", "nv-config-manager"),
        "local": nats_config.getboolean("local", fallback=False),
        "auth_method": nats_config.get("auth_method", "password"),
        "user": nats_config.get("user"),
        "password": nats_config.get("password"),
        "creds_path": nats_config.get("creds_path"),
        "default_stream_name": nats_config.get("config_manager_stream", "nv-config-manager"),
        "default_stream_subjects": subjects,
    }
