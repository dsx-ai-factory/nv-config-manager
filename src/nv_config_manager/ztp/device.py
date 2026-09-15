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
"""ZTP service model built from normalized DCIM device data."""

from __future__ import annotations

from dataclasses import dataclass

from nv_config_manager.common.client import (
    ConfigStoreClient,
    ConfigStoreFileNotFound,
)
from nv_config_manager.common.config import get_internal_auth_headers, load_config
from nv_config_manager.dcim.models import ZTPDevice


@dataclass
class DeviceData:  # pylint: disable=too-many-instance-attributes
    """ZTP-specific view of normalized device data."""

    id: str
    name: str
    addresses: list[str]
    platform_name: str
    version: str | None
    config_store_instance: str | None

    @property
    def platform(self) -> str:
        """Convert platform name to equivalent 1.x slug for compat."""
        return self.platform_name.lower().replace(" ", "-")

    def config_store_client(self) -> ConfigStoreClient:
        """Return the appropriate async config store client."""
        app_config = load_config()
        use_internal = app_config.getboolean(
            "config_store.client", "use_internal_endpoint", fallback=False
        )
        ui_url = app_config.get("config_store.client", "ui_url")

        if use_internal:
            # Internal HTTP cluster communication - no mTLS needed
            api_endpoint = app_config.get("config_store.client", "api_service")
            return ConfigStoreClient(
                api_endpoint,
                "intended",
                ui_url,
                verify=False,
                client_certificate=None,
                headers=get_internal_auth_headers,
            )
        else:
            # External mTLS communication
            api_endpoint = app_config.get("config_store.client", "api_url")

            client_cert_path = None
            if app_config.get("mtls", "tls_client_cert_path") and app_config.get(
                "mtls", "tls_client_key_path"
            ):
                cert_path = app_config.get("mtls", "tls_client_cert_path")
                key_path = app_config.get("mtls", "tls_client_key_path")
                client_cert_path = (cert_path, key_path)

            # Parse verify parameter - can be bool or path to CA cert
            verify: bool | str = True
            if app_config.get("config_store.client", "verify"):
                try:
                    verify = app_config.getboolean("config_store.client", "verify")
                except ValueError:
                    # Path to custom CA certificate
                    verify = str(app_config.get("config_store.client", "verify"))

            return ConfigStoreClient(
                api_endpoint,
                "intended",
                ui_url,
                verify=verify,
                client_certificate=client_cert_path,
            )

    async def load_file(self, filename: str) -> str:
        """Return file content for the given device."""
        if self.config_store_instance is None:
            raise ConfigStoreFileNotFound(f"No config store file found for device {self.name}")

        client = self.config_store_client()
        async with client:
            config_file = await client.load_file(self.id, filename)
        return config_file.content

    @classmethod
    def from_dcim(cls, device: ZTPDevice) -> DeviceData:
        """Build the service model from the public DCIM ZTP contract."""
        return cls(
            id=device.device_id,
            name=device.name,
            addresses=device.addresses,
            platform_name=device.platform_name,
            version=device.firmware_version,
            config_store_instance=device.config_store_instance,
        )
