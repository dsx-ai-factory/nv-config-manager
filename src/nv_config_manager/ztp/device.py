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

from nv_config_manager.common.config import config_store_client as create_config_store_client
from nv_config_manager.dcim.models import ZTPDevice
from nv_config_manager_workflows.clients.config_store import (
    ConfigStoreClient,
    ConfigStoreFileNotFound,
)


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
        return create_config_store_client()

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
