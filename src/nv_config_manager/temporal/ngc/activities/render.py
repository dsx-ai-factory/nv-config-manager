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
"""Render service activities."""

import asyncio
from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.client.config_store import ConfigStoreClient, ConfigStoreFileNotFound
from nv_config_manager.common.client.render import FileCommit
from nv_config_manager.common.config import (
    ConfigStoreType,
    config_store_client,
    get_storage_client,
    render_client,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.ztp.storage import ObjectStorageClient, ObjectStorageNotFoundException


class ExecuteRenderInput(BaseModel):
    """Input for executing a render operation."""

    device_id: str
    workflow_id: str


class ExecuteRenderOutput(BaseModel):
    """Output for render operation."""

    updated_files: list[FileCommit] = Field(default_factory=list)
    snapshot_files: list[FileCommit] = Field(default_factory=list)

    def get_commit(self, filename: str) -> str | None:
        """Look up a commit ID in the post-render Config Store snapshot."""
        return next((fc.commit for fc in self.snapshot_files if fc.filename == filename), None)


@activity.defn
async def execute_render(
    activity_input: ExecuteRenderInput,
) -> ExecuteRenderOutput:
    """Execute a render operation for a device.

    Args:
        activity_input: Input containing the device ID

    Returns:
        ExecuteRenderOutput containing changed files and the post-render snapshot
    """
    client = render_client()
    updated_files = await client.execute_render(
        activity_input.device_id, activity_input.workflow_id
    )

    # Config Store returns every latest file version in one response, keeping
    # commit IDs used together for deployment pinned to the same snapshot.
    config_client = config_store_client(ConfigStoreType.INTENDED)
    async with config_client:
        configs = await config_client.list_device_configs(activity_input.device_id)
    snapshot_files = [
        FileCommit(filename=str(config["filename"]), commit=str(config["version"]))
        for config in configs
    ]

    return ExecuteRenderOutput(
        updated_files=updated_files,
        snapshot_files=snapshot_files,
    )


class ValidateRenderedImageChangeInput(BaseModel):
    """Input for validating the rendered image change."""

    device_data: NetworkDeviceData
    desired_image: str


class ValidateRenderedPasswordChangeInput(BaseModel):
    """Input for validating the rendered password change."""

    device_data: NetworkDeviceData
    desired_password_string: str


_IMAGE_RENDER_POLL_TIMEOUT = timedelta(minutes=5)
_IMAGE_RENDER_POLL_INTERVAL_SECONDS = 30
_JUNIPER_INTENDED_CONFIG_FILE = "full-config"


# Temporary hack until we can get proper mTLS certificates issued
# for communication between the render service and the temporal service
@activity.defn
async def validate_rendered_image_change(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Validate that upgrade artifacts for the target image are in place."""
    platform = activity_input.device_data.platform
    if platform == Platform.CUMULUS_LINUX:
        return await _validate_cumulus_boot_script_image(activity_input)
    if platform == Platform.JUNIPER_JUNOS:
        return await _validate_juniper_upgrade_artifacts(activity_input)
    raise NotImplementedError(f"Platform {platform} not supported")


def _heartbeat_render_poll(start_time: datetime) -> None:
    elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
    activity.heartbeat(f"Validating render ({elapsed_minutes:.0f}m)")


async def _validate_cumulus_boot_script_image(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Poll until the boot-script names the desired Cumulus VERSION_ID."""
    client = config_store_client(ConfigStoreType.INTENDED)
    desired = activity_input.desired_image
    start_time = datetime.now()
    async with client:
        while datetime.now() - start_time < _IMAGE_RENDER_POLL_TIMEOUT:
            _heartbeat_render_poll(start_time)
            config_file = await client.load_file(
                device_uuid=activity_input.device_data.id, filename="boot-script"
            )
            if f"VERSION_ID={desired}" in config_file.content:
                return True
            await asyncio.sleep(_IMAGE_RENDER_POLL_INTERVAL_SECONDS)
    raise ApplicationError(
        f"Timeout waiting for image version {desired} to be present in boot script"
    )


async def _juniper_firmware_present(
    storage_client: ObjectStorageClient, platform: str, desired: str
) -> bool:
    try:
        await storage_client.get_firmware_checksum(platform, desired)
        return True
    except ObjectStorageNotFoundException:
        return False


async def _juniper_full_config_present(config_client: ConfigStoreClient, device_id: str) -> bool:
    try:
        await config_client.load_file(device_uuid=device_id, filename=_JUNIPER_INTENDED_CONFIG_FILE)
        return True
    except ConfigStoreFileNotFound:
        return False


async def _validate_juniper_upgrade_artifacts(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Poll until the Junos image is on the ZTP server and full-config is rendered."""
    desired = activity_input.desired_image
    device_id = activity_input.device_data.id
    platform = str(activity_input.device_data.platform)
    config_client = config_store_client(ConfigStoreType.INTENDED)
    storage_client = get_storage_client()
    start_time = datetime.now()
    async with config_client, storage_client:
        while datetime.now() - start_time < _IMAGE_RENDER_POLL_TIMEOUT:
            _heartbeat_render_poll(start_time)
            firmware_ready = await _juniper_firmware_present(storage_client, platform, desired)
            config_ready = await _juniper_full_config_present(config_client, device_id)
            if firmware_ready and config_ready:
                return True
            await asyncio.sleep(_IMAGE_RENDER_POLL_INTERVAL_SECONDS)
    raise ApplicationError(
        f"Timeout waiting for Juniper firmware {desired} and full-config for device {device_id}"
    )


@activity.defn
async def validate_rendered_password_change(
    activity_input: ValidateRenderedPasswordChangeInput,
) -> bool:
    """Validate the rendered password change.

    Polls the file content every 30 seconds for up to 5 minutes to check for the desired password string.
    Raises ApplicationError if the timeout is reached.
    """
    client = config_store_client(ConfigStoreType.INTENDED)
    filename = activity_input.device_data.intended_config_file

    if not filename:
        raise ApplicationError(
            f"No intended config filename found for device {activity_input.device_data.name}"
        )

    start_time = datetime.now()
    timeout = timedelta(minutes=5)
    poll_interval = 30  # seconds

    async with client:
        while datetime.now() - start_time < timeout:
            # Send heartbeat with progress information
            elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
            activity.heartbeat(f"Validating password render ({elapsed_minutes:.0f}m)")

            config_file = await client.load_file(
                device_uuid=activity_input.device_data.id, filename=filename
            )
            if activity_input.desired_password_string in config_file.content:
                return True
            await asyncio.sleep(poll_interval)

    raise ApplicationError(
        f"Timeout waiting for password string {activity_input.desired_password_string} to be present in configuration"
    )
