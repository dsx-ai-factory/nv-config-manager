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

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.render import FileCommit
from nv_config_manager_dcim.workflow_models import Platform
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.render.helpers import (
    validate_cumulus_boot_script_image,
    validate_juniper_upgrade_artifacts,
)
from nv_config_manager_workflows.activities.render.models import (
    ExecuteRenderInput,
    ExecuteRenderOutput,
    ValidateRenderedImageChangeInput,
    ValidateRenderedPasswordChangeInput,
)
from nv_config_manager_workflows.runtime import (
    get_config_store_runtime,
    get_render_client,
)


@activity.defn
async def execute_render(
    activity_input: ExecuteRenderInput,
) -> ExecuteRenderOutput:
    """Execute a render and capture the resulting Config Store snapshot."""
    async with get_render_client() as client:
        updated_files = await client.execute_render(
            activity_input.device_id, activity_input.workflow_id
        )

    config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
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


@activity.defn
async def validate_rendered_image_change(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    """Validate that upgrade artifacts for the target image are in place."""
    platform = activity_input.device_data.platform
    if platform == Platform.CUMULUS_LINUX:
        return await validate_cumulus_boot_script_image(activity_input)
    if platform == Platform.JUNIPER_JUNOS:
        return await validate_juniper_upgrade_artifacts(activity_input)
    raise NotImplementedError(f"Platform {platform} not supported")


@activity.defn
async def validate_rendered_password_change(
    activity_input: ValidateRenderedPasswordChangeInput,
) -> bool:
    """Poll until the intended configuration contains the desired password string."""
    client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    filename = activity_input.device_data.intended_config_file

    if not filename:
        raise ApplicationError(
            f"No intended config filename found for device {activity_input.device_data.name}"
        )

    start_time = datetime.now()
    timeout = timedelta(minutes=5)
    poll_interval = 30

    async with client:
        while datetime.now() - start_time < timeout:
            elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
            activity.heartbeat(f"Validating password render ({elapsed_minutes:.0f}m)")

            config_file = await client.load_file(
                device_uuid=activity_input.device_data.id,
                filename=filename,
            )
            if activity_input.desired_password_string in config_file.content:
                return True
            await asyncio.sleep(poll_interval)

    raise ApplicationError(
        "Timeout waiting for the desired password string to be present in "
        f"{filename} for device {activity_input.device_data.name}"
    )


RENDER_ACTIVITIES = (
    execute_render,
    validate_rendered_image_change,
    validate_rendered_password_change,
)

__all__ = [
    "RENDER_ACTIVITIES",
    "ExecuteRenderInput",
    "ExecuteRenderOutput",
    "ValidateRenderedImageChangeInput",
    "ValidateRenderedPasswordChangeInput",
    "execute_render",
    "validate_rendered_image_change",
    "validate_rendered_password_change",
]
