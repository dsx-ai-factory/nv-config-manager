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
"""Configuration shared by all Temporal SDK clients."""

from __future__ import annotations

from configparser import SectionProxy
from pathlib import Path
from typing import Any

from temporalio.client import TLSConfig

from nv_config_manager.common.config_loader import load_config

_DEFAULT_ADDRESS = "localhost:7233"
_DEFAULT_NAMESPACE = "default"


def client_connect_options() -> dict[str, Any]:
    """Build ``Client.connect`` options from the unified INI configuration."""
    temporal = _temporal_config()
    options: dict[str, Any] = {
        "namespace": temporal.get("namespace", _DEFAULT_NAMESPACE)
        if temporal
        else _DEFAULT_NAMESPACE
    }
    if temporal and (temporal.getboolean("tls_enabled") if "tls_enabled" in temporal else False):
        options["tls"] = TLSConfig(
            server_root_ca_cert=_read_optional_file(temporal.get("tls_ca_cert_path")),
            domain=temporal.get("tls_server_name") or None,
            client_cert=_read_required_file(
                temporal.get("tls_client_cert_path"), "tls_client_cert_path"
            ),
            client_private_key=_read_required_file(
                temporal.get("tls_client_key_path"), "tls_client_key_path"
            ),
        )
    return options


def temporal_address() -> str:
    """Return the configured Temporal endpoint from the unified INI file."""
    temporal = _temporal_config()
    return temporal.get("grpc_service", _DEFAULT_ADDRESS) if temporal else _DEFAULT_ADDRESS


def _temporal_config() -> SectionProxy | None:
    config = load_config()
    if config.has_section("temporal"):
        return config["temporal"]
    return None


def _read_optional_file(path: str | None) -> bytes | None:
    return Path(path).read_bytes() if path else None


def _read_required_file(path: str | None, setting: str) -> bytes:
    if not path:
        raise ValueError(f"temporal.{setting} is required when temporal.tls_enabled is true")
    return Path(path).read_bytes()
