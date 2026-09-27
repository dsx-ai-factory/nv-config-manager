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

import asyncio
from datetime import datetime

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.config_store import ConfigStoreClient, ConfigStoreFileNotFound
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.render.models import (
    _IMAGE_RENDER_POLL_INTERVAL_SECONDS,
    _IMAGE_RENDER_POLL_TIMEOUT,
    _JUNIPER_INTENDED_CONFIG_FILE,
    ValidateRenderedImageChangeInput,
)
from nv_config_manager_workflows.runtime import (
    FirmwareStorage,
    get_config_store_runtime,
    get_firmware_storage,
)


def _heartbeat_render_poll(start_time: datetime) -> None:
    elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
    activity.heartbeat(f"Validating render ({elapsed_minutes:.0f}m)")


async def validate_cumulus_boot_script_image(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Poll until the boot-script names the desired Cumulus VERSION_ID."""
    config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    desired = activity_input.desired_image
    start_time = datetime.now()
    async with config_client:
        while datetime.now() - start_time < _IMAGE_RENDER_POLL_TIMEOUT:
            _heartbeat_render_poll(start_time)
            config_file = await config_client.load_file(
                device_uuid=activity_input.device_data.id, filename="boot-script"
            )
            if f"VERSION_ID={desired}" in config_file.content:
                return True
            await asyncio.sleep(_IMAGE_RENDER_POLL_INTERVAL_SECONDS)
    raise ApplicationError(
        f"Timeout waiting for image version {desired} to be present in boot script"
    )


async def _juniper_firmware_present(
    storage: FirmwareStorage,
    platform: str,
    desired: str,
) -> bool:
    return await storage.firmware_exists(platform, desired)


async def _juniper_full_config_present(
    config_client: ConfigStoreClient,
    device_id: str,
) -> bool:
    try:
        await config_client.load_file(
            device_uuid=device_id,
            filename=_JUNIPER_INTENDED_CONFIG_FILE,
        )
        return True
    except ConfigStoreFileNotFound:
        return False


async def validate_juniper_upgrade_artifacts(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Poll until the Junos image is available and full-config is rendered."""
    desired = activity_input.desired_image
    device_id = activity_input.device_data.id
    platform = str(activity_input.device_data.platform)
    config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    storage = get_firmware_storage()
    start_time = datetime.now()
    async with config_client, storage:
        while datetime.now() - start_time < _IMAGE_RENDER_POLL_TIMEOUT:
            _heartbeat_render_poll(start_time)
            firmware_ready = await _juniper_firmware_present(storage, platform, desired)
            config_ready = await _juniper_full_config_present(config_client, device_id)
            if firmware_ready and config_ready:
                return True
            await asyncio.sleep(_IMAGE_RENDER_POLL_INTERVAL_SECONDS)
    raise ApplicationError(
        f"Timeout waiting for Juniper firmware {desired} and full-config for device {device_id}"
    )
