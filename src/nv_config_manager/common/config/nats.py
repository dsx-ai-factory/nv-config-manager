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

from __future__ import annotations

import ssl
from collections.abc import Awaitable, Callable
from configparser import ConfigParser, SectionProxy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import certifi
import nats
import nats.aio.client
import nats.js.errors
from nv_config_manager_infrastructure.nats.client import DEFAULT_NATS_API_PREFIX

from nv_config_manager.common.client.nats import config_manager_api_prefix
from nv_config_manager.common.config.loader import resolve_section

DEFAULT_CONFIG_MANAGER_NATS_STREAM = "nv-config-manager"
DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT = "nv-config-manager.nautobotchange"
DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT = "nv-config-manager.devicechange"
DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT = "nv-config-manager.workflow.result"
DEFAULT_NAUTOBOT_NATS_STREAM = "nautobot"
DEFAULT_NAUTOBOT_NATS_SUBJECT = "nautobot"


class _NATS_ENUM(StrEnum):
    RENDER_CHANGE = "render_change"
    DEVICE_CHANGE = "device_change"
    ARCHIVE = "archive"
    DCIM_CHANGE = "dcim_change"
    NAUTOBOT = "nautobot"


@dataclass(frozen=True)
class _NatsConfigLookup:
    stream_key: str
    subject_key: str
    default_stream: str
    default_subject: str
    stream_fallback_key: str | None = None
    subject_fallback_key: str | None = None


_NATS_CONFIG_LOOKUPS = {
    _NATS_ENUM.RENDER_CHANGE: _NatsConfigLookup(
        stream_key="render_change_stream",
        subject_key="render_change_subject",
        stream_fallback_key="config_manager_stream",
        default_stream=DEFAULT_CONFIG_MANAGER_NATS_STREAM,
        default_subject=DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
    ),
    _NATS_ENUM.DEVICE_CHANGE: _NatsConfigLookup(
        stream_key="device_change_stream",
        subject_key="device_change_subject",
        stream_fallback_key="config_manager_stream",
        default_stream=DEFAULT_CONFIG_MANAGER_NATS_STREAM,
        default_subject=DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
    ),
    _NATS_ENUM.ARCHIVE: _NatsConfigLookup(
        stream_key="archive_stream",
        subject_key="archive_subject",
        stream_fallback_key="config_manager_stream",
        default_stream=DEFAULT_CONFIG_MANAGER_NATS_STREAM,
        default_subject=DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
    ),
    _NATS_ENUM.DCIM_CHANGE: _NatsConfigLookup(
        stream_key="dcim_change_stream",
        subject_key="dcim_change_subject",
        stream_fallback_key="nautobot_stream",
        subject_fallback_key="nautobot_subject",
        default_stream=DEFAULT_NAUTOBOT_NATS_STREAM,
        default_subject=DEFAULT_NAUTOBOT_NATS_SUBJECT,
    ),
    _NATS_ENUM.NAUTOBOT: _NatsConfigLookup(
        stream_key="nautobot_stream",
        subject_key="nautobot_subject",
        default_stream=DEFAULT_NAUTOBOT_NATS_STREAM,
        default_subject=DEFAULT_NAUTOBOT_NATS_SUBJECT,
    ),
}


class NATSConnectionManager:
    """Singleton to manage shared NATS connection."""

    _instance: NATSConnectionManager | None = None
    _connection: nats.aio.client.Client | None = None

    def __new__(cls) -> NATSConnectionManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def set_connection(self, connection: nats.aio.client.Client) -> None:
        """Set the shared NATS connection."""
        self._connection = connection

    def get_connection(self) -> nats.aio.client.Client | None:
        """Get the shared NATS connection."""
        return self._connection

    def clear_connection(self) -> None:
        """Clear the shared connection."""
        self._connection = None


def _nats_section(config: ConfigParser | None = None) -> SectionProxy:
    return resolve_section("nats", config)


def nats_config_manager_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for the config-manager-owned stream.

    Render-change, device-change and archive events are subjects on this one stream,
    so they share its prefix.
    """
    return config_manager_api_prefix(_nats_section(config))


def _nats_change_config(
    nats_option: _NATS_ENUM,
    config: ConfigParser | None = None,
) -> tuple[str, str]:
    nats_config = _nats_section(config)
    lookup = _NATS_CONFIG_LOOKUPS[nats_option]
    stream = nats_config.get(
        lookup.stream_key,
        nats_config.get(lookup.stream_fallback_key, lookup.default_stream)
        if lookup.stream_fallback_key is not None
        else lookup.default_stream,
    )
    subject = nats_config.get(
        lookup.subject_key,
        nats_config.get(lookup.subject_fallback_key, lookup.default_subject)
        if lookup.subject_fallback_key is not None
        else lookup.default_subject,
    )
    return stream, subject


def nats_render_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for render-triggering changes."""
    return _nats_change_config(_NATS_ENUM.RENDER_CHANGE, config)


def nats_device_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for device-change notifications."""
    return _nats_change_config(_NATS_ENUM.DEVICE_CHANGE, config)


def nats_archive_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for workflow archive events."""
    return _nats_change_config(_NATS_ENUM.ARCHIVE, config)


def nats_dcim_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the stream and subject for provider-neutral DCIM change events.

    The legacy Nautobot settings remain the fallback while its publisher is
    upgraded. External providers should configure ``dcim_change_*`` directly.
    """
    return _nats_change_config(_NATS_ENUM.DCIM_CHANGE, config)


def nats_nautobot_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for Nautobot changelog events."""
    return _nats_change_config(_NATS_ENUM.NAUTOBOT, config)


def nats_nautobot_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for Nautobot changelog events."""
    nats_config = _nats_section(config)
    return nats_config.get("nautobot_api_prefix", DEFAULT_NATS_API_PREFIX)


async def nats_connection(
    closed_cb: Callable[[], Awaitable[None]] | None = None,
    error_cb: Callable[[Exception], Awaitable[None]] | None = None,
    disconnected_cb: Callable[[], Awaitable[None]] | None = None,
    reconnected_cb: Callable[[], Awaitable[None]] | None = None,
) -> nats.aio.client.Client:
    """Return a connected NATS client for this environment.

    Args:
        closed_cb: Callback when connection is closed
        error_cb: Callback on error
        disconnected_cb: Callback on disconnect
        reconnected_cb: Callback on reconnect

    Returns:
        Connected NATS client
    """
    ssl_context = ssl.create_default_context()
    ssl_context.load_verify_locations(certifi.where())

    nats_config = _nats_section()

    servers = nats_config["server"]
    auth_method = nats_config.get("auth_method", "password")

    options: dict[str, Any] = {
        "tls": ssl_context,
        "connect_timeout": 30,
        "closed_cb": closed_cb,
        "error_cb": error_cb,
        "disconnected_cb": disconnected_cb,
        "reconnected_cb": reconnected_cb,
        "allow_reconnect": True,
        "ping_interval": 50,
    }

    if auth_method == "JWT":
        options["user_credentials"] = nats_config["credentials"]
    elif auth_method == "password":
        if "user" in nats_config:
            options["user"] = nats_config["user"]
        if "password" in nats_config:
            options["password"] = nats_config["password"]

    # Match the native TLS-first policy used by the archive NATS client.
    # WSS and bundled nats:// connections retain their transport behavior.
    if urlparse(servers).scheme == "tls":
        options["tls_handshake_first"] = True

    conn = await nats.connect(servers, **options)

    # Create streams locally if needed
    if nats_config.getboolean("local", fallback=False):
        jetstream = conn.jetstream()
        for stream in ["nv-config-manager", "nautobot"]:
            try:
                await jetstream.stream_info(stream)
            except nats.js.errors.NotFoundError:
                # nv-config-manager uses hierarchical subjects (nv_config_manager.render.events, etc.)
                # nautobot uses exact subject (nautobot_broker_nats publishes to "nautobot")
                subjects = [f"{stream}.>"] if stream == "nv-config-manager" else [stream]
                await jetstream.add_stream(name=stream, subjects=subjects)

    return conn
