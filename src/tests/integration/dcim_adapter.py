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
"""Provider boundary used by the live-cluster integration tests.

Production services use the DCIM SDK.  These tests additionally need to inspect
provider-owned lifecycle state after a service call, so that inspection lives in
an adapter instead of leaking provider GraphQL schemas into common tests.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol

import requests


class DCIMIntegrationAdapter(Protocol):
    """Provider-owned inspection operations required by common integration tests."""

    provider_name: str
    service_name: str
    service_port: int
    hostname_prefix: str
    secret_name: str
    secret_key: str
    url_environment_variable: str
    token_environment_variable: str

    def __init__(self, server: str, token: str): ...

    def list_devices(
        self,
        *,
        render_enabled: bool | None = None,
        ztp_enabled: bool | None = None,
        backup_enabled: bool | None = None,
        platform: str | None = None,
        include_interfaces: bool = False,
    ) -> list[dict[str, Any]]: ...

    def get_device_status(self, device_id: str) -> str: ...

    def backup_config_count(self) -> int: ...


def load_dcim_integration_adapter(spec: str) -> type[DCIMIntegrationAdapter]:
    """Load ``module:class`` without coupling the suite to external providers."""
    module_name, separator, class_name = spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("DCIM integration adapter must use the form 'module:class'")
    adapter = getattr(import_module(module_name), class_name)
    return adapter


class NautobotIntegrationAdapter:
    """Inspection adapter for the bundled Nautobot provider."""

    provider_name = "nautobot-2x"
    service_name = "nv-config-manager-nautobot"
    service_port = 80
    hostname_prefix = "nautobot"
    secret_name = "nautobot-admin"
    secret_key = "api_token"
    url_environment_variable = "NAUTOBOT_URL"
    token_environment_variable = "NAUTOBOT_TOKEN"

    def __init__(self, server: str, token: str):
        self.server = server.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Token {token}", "Content-Type": "application/json"}
        )
        self.session.verify = False

    def _graphql(self, query: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.server}/api/graphql/", json={"query": query}, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if errors := result.get("errors"):
            raise AssertionError(f"Nautobot GraphQL query failed: {errors}")
        return result.get("data", {})

    def list_devices(
        self,
        *,
        render_enabled: bool | None = None,
        ztp_enabled: bool | None = None,
        backup_enabled: bool | None = None,
        platform: str | None = None,
        include_interfaces: bool = False,
    ) -> list[dict[str, Any]]:
        arguments = []
        for name, value in (
            ("render_enabled", render_enabled),
            ("ztp_enabled", ztp_enabled),
            ("backup_enabled", backup_enabled),
        ):
            if value is not None:
                arguments.append(f"{name}: {str(value).lower()}")
        suffix = f"({', '.join(arguments)})" if arguments else ""
        data = self._graphql(
            """
            query {
              config_manager_devices%s {
                id
                render_enabled
                ztp_enabled
                backup_enabled
                intended_config { path commit_id }
                device {
                  id
                  name
                  serial
                  status { name }
                  platform { name }
                  role { name }
                  primary_ip4 { address }
                  interfaces {
                    name
                    mac_address
                    mgmt_only
                    role { name }
                    ip_addresses { address }
                  }
                }
              }
              reserved_ips: ip_addresses(tags: ["dhcp-reserve"]) {
                interfaces { device { id } }
              }
            }
            """.replace("%s", suffix)
        )
        reserved_device_ids = {
            str(interface["device"]["id"])
            for reserved_ip in data.get("reserved_ips", [])
            for interface in reserved_ip.get("interfaces", [])
            if interface.get("device")
        }
        devices = []
        for managed in data.get("config_manager_devices", []):
            device = managed.get("device") or {}
            platform_name = (device.get("platform") or {}).get("name")
            if platform is not None and platform_name != platform:
                continue
            interfaces = []
            if include_interfaces:
                for interface in device.get("interfaces") or []:
                    interfaces.append(
                        {
                            **interface,
                            "role": (interface.get("role") or {}).get("name"),
                        }
                    )
            devices.append(
                {
                    "id": str(device.get("id")),
                    "name": device.get("name"),
                    "serial": device.get("serial"),
                    "status": (device.get("status") or {}).get("name"),
                    "platform": platform_name,
                    "role": (device.get("role") or {}).get("name"),
                    "primary_ip4": (device.get("primary_ip4") or {}).get("address"),
                    "interfaces": interfaces,
                    "dhcp_reserved": str(device.get("id")) in reserved_device_ids,
                    "render_enabled": bool(managed.get("render_enabled")),
                    "ztp_enabled": bool(managed.get("ztp_enabled")),
                    "backup_enabled": bool(managed.get("backup_enabled")),
                    "intended_config": managed.get("intended_config"),
                }
            )
        return devices

    def get_device_status(self, device_id: str) -> str:
        data = self._graphql(f'query {{ device(id: "{device_id}") {{ status {{ name }} }} }}')
        return data["device"]["status"]["name"]

    def backup_config_count(self) -> int:
        response = self.session.get(
            f"{self.server}/api/plugins/nv-config-manager/backupconfig/", timeout=30
        )
        response.raise_for_status()
        payload = response.json()
        return int(payload.get("count", len(payload.get("results", []))))
