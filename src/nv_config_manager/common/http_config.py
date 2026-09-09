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

import os
from configparser import ConfigParser, SectionProxy

from nv_config_manager.common.config_loader import (
    load_config,
    resolve_config,
    resolve_config_section,
)

DEFAULT_NATS_API_PREFIX = "$JS.API"
DEFAULT_CONFIG_MANAGER_NATS_STREAM = "nv-config-manager"
DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT = "nv-config-manager.nautobotchange"
DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT = "nv-config-manager.devicechange"
DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT = "nv-config-manager.workflow.result"
DEFAULT_NAUTOBOT_NATS_STREAM = "nautobot"
DEFAULT_NAUTOBOT_NATS_SUBJECT = "nautobot"


def config_manager_api_prefix(nats_config: SectionProxy) -> str:
    """Return the JetStream API prefix for the config-manager-owned stream."""
    return nats_config.get("config_manager_api_prefix", DEFAULT_NATS_API_PREFIX)


def _nats_section(config: ConfigParser | None = None) -> SectionProxy:
    return resolve_config_section("nats", config)


def nats_config_manager_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for the config-manager-owned stream.

    Render-change, device-change and archive events are subjects on this one stream,
    so they share its prefix.
    """
    return config_manager_api_prefix(_nats_section(config))


def nats_render_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for render-triggering changes."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "render_change_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "render_change_subject",
        DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
    )
    return stream, subject


def nats_device_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for device-change notifications."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "device_change_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "device_change_subject",
        DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
    )
    return stream, subject


def nats_archive_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for workflow archive events."""
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "archive_stream",
        nats_config.get("config_manager_stream", DEFAULT_CONFIG_MANAGER_NATS_STREAM),
    )
    subject = nats_config.get(
        "archive_subject",
        DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
    )
    return stream, subject


def nats_nautobot_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the configured stream and subject for Nautobot changelog events."""
    nats_config = _nats_section(config)
    stream = nats_config.get("nautobot_stream", DEFAULT_NAUTOBOT_NATS_STREAM)
    subject = nats_config.get("nautobot_subject", DEFAULT_NAUTOBOT_NATS_SUBJECT)
    return stream, subject


def nats_nautobot_api_prefix(config: ConfigParser | None = None) -> str:
    """Return the JetStream API prefix for Nautobot changelog events."""
    nats_config = _nats_section(config)
    return nats_config.get("nautobot_api_prefix", DEFAULT_NATS_API_PREFIX)


def nats_dcim_change_config(config: ConfigParser | None = None) -> tuple[str, str]:
    """Return the stream and subject for provider-neutral DCIM change events.

    The legacy Nautobot settings remain the fallback while its publisher is
    upgraded. External providers should configure ``dcim_change_*`` directly.
    """
    nats_config = _nats_section(config)
    stream = nats_config.get(
        "dcim_change_stream",
        nats_config.get("nautobot_stream", DEFAULT_NAUTOBOT_NATS_STREAM),
    )
    subject = nats_config.get(
        "dcim_change_subject",
        nats_config.get("nautobot_subject", DEFAULT_NAUTOBOT_NATS_SUBJECT),
    )
    return stream, subject


def parse_verify_param(
    config_section: SectionProxy,
    key: str = "verify",
    fallback: bool = True,
) -> bool | str:
    """Parse SSL verify parameter from config.

    Handles boolean values ("true", "false", "yes", "no", "1", "0")
    or string paths to CA certificate files.

    Args:
        config_section: Config section to read from
        key: Key name for the verify parameter
        fallback: Default value if key doesn't exist

    Returns:
        Boolean True/False or string path to CA cert file
    """
    try:
        return config_section.getboolean(key, fallback=fallback)
    except ValueError:
        return config_section[key]


def get_mtls_cert_paths(config: ConfigParser | None = None) -> tuple[str, str] | None:
    """Get mTLS certificate paths from config.

    Args:
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        Tuple of (cert_path, key_path) or None if not configured
    """
    config = resolve_config(config)

    if not config.has_section("mtls"):
        return None

    mtls = config["mtls"]
    cert_path = mtls.get("tls_client_cert_path")
    key_path = mtls.get("tls_client_key_path")

    if cert_path and key_path:
        return (cert_path, key_path)
    return None


def use_internal_endpoint(section: str, config: ConfigParser | None = None) -> bool:
    """Check if a service should use internal endpoints.

    Args:
        section: Config section name (e.g., "render", "temporal")
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        True if use_internal_endpoint is set, False otherwise
    """
    config_section = resolve_config_section(section, config)
    return config_section.getboolean("use_internal_endpoint", fallback=False)


def _read_spiffe_jwt() -> str | None:
    """Read the current JWT-SVID from the file written by spiffe-helper.

    The path is read from ``[auth.spiffe] jwt_svid_path`` in the INI.
    Returns the raw JWT string, or None if SPIFFE is not configured or
    the file is unavailable.
    """
    config = load_config()
    jwt_path = config.get("auth.spiffe", "jwt_svid_path", fallback="")
    if not jwt_path:
        return None
    try:
        with open(jwt_path) as f:
            token = f.read().strip()
        return token or None
    except OSError:
        return None


def get_internal_auth_headers(
    service_name: str | None = None,
    group: str = "nv-config-manager",
) -> dict[str, str]:
    """Get auth headers for internal service-to-service calls.

    When SPIFFE is configured (``[auth.spiffe] jwt_svid_path`` is set),
    reads the JWT-SVID from disk and returns an ``Authorization: Bearer``
    header.  The receiving service validates the JWT against the Workload
    API trust bundle.

    When SPIFFE is not configured, falls back to ``X-Auth-Request-*``
    headers for environments that trust the caller's identity headers
    directly (the receiving service must have
    ``[auth] accept_request_headers = true``).

    Callers should invoke this function per-request (not cache the result)
    because JWT-SVIDs have short TTLs and are refreshed on disk by
    spiffe-helper.

    Args:
        service_name: Name of the calling service. If not provided, derives
            from HOSTNAME (e.g., "nv-config-manager-ztp-6c98b9b6cb-xyz" -> "nv-config-manager-ztp")
        group: RBAC group for authorization (default: "nv-config-manager")

    Returns:
        Dict of auth headers to include in HTTP requests
    """
    jwt = _read_spiffe_jwt()
    if jwt:
        return {"Authorization": f"Bearer {jwt}"}

    if service_name:
        caller = service_name
    else:
        caller = os.environ.get("HOSTNAME", "internal-service")
    return {
        "X-Auth-Request-Email": caller,
        "X-Auth-Request-User": caller,
        "X-Auth-Request-Groups": group,
    }


def get_service_url(
    section: str,
    internal_key: str = "api_service",
    external_key: str = "api_url",
    config: ConfigParser | None = None,
) -> str:
    """Get the appropriate service URL based on internal/external config.

    Args:
        section: Config section name
        internal_key: Key for internal URL
        external_key: Key for external URL
        config: ConfigParser instance (uses load_config() if None)

    Returns:
        The appropriate URL for the current environment
    """
    config = resolve_config(config)

    if use_internal_endpoint(section, config):
        return config[section][internal_key]
    return config[section][external_key]
