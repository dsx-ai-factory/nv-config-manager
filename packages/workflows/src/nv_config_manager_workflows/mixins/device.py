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
"""Temporal helpers built on provider-neutral DCIM inventory models."""

from nv_config_manager_dcim.workflow_models import (
    DeviceData,
    NetworkDeviceData,
)

from nv_config_manager_workflows.mixins.base import BaseMixin
from nv_config_manager_workflows.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    upsert_missing_search_attributes,
)


class DeviceMixin(BaseMixin):
    """Temporal-only behavior applied to provider-neutral device models."""

    @staticmethod
    def attach_device_search_attributes(device: DeviceData) -> None:
        """Attach normalized DCIM device metadata to workflow search attributes."""
        attributes = {
            DEVICE_ID_SEARCH_ATTRIBUTE: [device.id],
            DEVICE_ROLE_SEARCH_ATTRIBUTE: [device.role],
            SITE_SEARCH_ATTRIBUTE: [device.site],
            DEVICE_NAME_SEARCH_ATTRIBUTE: [device.name],
        }
        if isinstance(device, NetworkDeviceData):
            attributes[DEVICE_PLATFORM_SEARCH_ATTRIBUTE] = [device.platform]
        upsert_missing_search_attributes(attributes)
