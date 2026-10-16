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
"""INI settings adapters for NATS clients."""

from __future__ import annotations

from configparser import ConfigParser
from typing import TypedDict

from nv_config_manager_infrastructure.nats import DEFAULT_NATS_API_PREFIX

from nv_config_manager.common.config.loader import resolve_config, resolve_section


class NatsClientSettings(TypedDict):
    """Constructor settings shared by NATS clients and producers."""

    server: str
    queue: str
    local: bool
    auth_method: str
    user: str | None
    password: str | None
    creds_path: str | None
    default_stream_name: str
    default_stream_subjects: list[str]
    api_prefix: str


class NatsConsumerSettings(NatsClientSettings):
    """Config-derived constructor settings for a NATS consumer."""

    durable_name: str
    deliver_subject: str


def nats_client_settings(config: ConfigParser | None = None) -> NatsClientSettings:
    """Translate the NATS INI section into common constructor settings."""
    nats = resolve_section("nats", config)
    subjects = [
        subject.strip()
        for subject in nats.get("config_manager_subjects", "nv-config-manager.>").split(",")
        if subject.strip()
    ]
    return {
        "server": nats["server"],
        "queue": nats.get("queue", "nv-config-manager"),
        "local": nats.getboolean("local", fallback=False),
        "auth_method": nats.get("auth_method", "password"),
        "user": nats.get("user"),
        "password": nats.get("password"),
        "creds_path": nats.get("creds_path"),
        "default_stream_name": nats.get("config_manager_stream", "nv-config-manager"),
        "default_stream_subjects": subjects,
        "api_prefix": nats.get("config_manager_api_prefix", DEFAULT_NATS_API_PREFIX),
    }


def nats_consumer_settings(
    config: ConfigParser | None = None,
    *,
    queue_suffix: str,
) -> NatsConsumerSettings:
    """Return NATS connection and configured archive-consumer settings."""
    resolved = resolve_config(config)
    nats = resolved["nats"]
    common = nats_client_settings(resolved)
    return {
        "server": common["server"],
        "queue": common["queue"],
        "local": common["local"],
        "auth_method": common["auth_method"],
        "user": common["user"],
        "password": common["password"],
        "creds_path": common["creds_path"],
        "default_stream_name": common["default_stream_name"],
        "default_stream_subjects": common["default_stream_subjects"],
        "api_prefix": common["api_prefix"],
        "durable_name": nats.get("archive_consumer_name", f"nv-config-manager-{queue_suffix}"),
        "deliver_subject": nats.get(
            "archive_deliver_subject", "nv-config-manager.archive.delivery"
        ),
    }
