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
"""Device metadata enrichment utilities."""

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.config_store.api.schemas import DeviceMetadata
from nv_config_manager.config_store.core.device_cache_redis import DeviceCacheService

logger = get_logger(__name__, category=LogCategory.CONFIG_STORE)


async def enrich_with_device_metadata(
    device_uuid: str,
    cache_service: DeviceCacheService | None,
) -> DeviceMetadata | None:
    """Enrich response with device metadata from Redis cache.

    Args:
        device_uuid: DCIM provider device identifier
        cache_service: Device cache service (if available)

    Returns:
        DeviceMetadata or None if not found or disabled
    """
    if not cache_service:
        return None

    try:
        metadata = await cache_service.get_device_metadata(device_uuid, refresh_on_miss=True)

        if not metadata:
            return None

        return DeviceMetadata(
            name=metadata.name,
            site=metadata.site,
            platform=metadata.platform,
            role=metadata.role,
            rack=metadata.rack,
            primary_ip4=metadata.primary_ip4,
            device_url=metadata.device_url,
            nautobot_url=metadata.device_url,
            last_updated=None,  # Redis cache doesn't track this separately
        )
    except Exception as e:
        logger.error("Failed to enrich device %s: %s", device_uuid, e)
        return None
