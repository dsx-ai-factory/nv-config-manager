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
"""Device Password Rotation activity implementations."""

import asyncio
from datetime import datetime, timedelta

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_dcim.workflow_models import Platform
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.device_password_rotation import helpers
from nv_config_manager_workflows.activities.device_password_rotation.models import (
    FormatPasswordRotationResultsInput,
    GetPasswordMappingsInput,
    GetPasswordMappingsOutput,
    ValidatePasswordDiffInput,
    ValidatePasswordDiffOutput,
    ValidatePlatformSupportInput,
    ValidatePlatformSupportOutput,
    ValidateRenderedPasswordChangeInput,
)
from nv_config_manager_workflows.runtime import get_config_store_runtime, get_dcim_client
from nv_config_manager_workflows.workflow_urls import build_workflow_url


@activity.defn
async def validate_password_diff(
    activity_input: ValidatePasswordDiffInput,
) -> ValidatePasswordDiffOutput:
    """Validate that the diff only contains password changes for the specified user."""

    platform = activity_input.platform.lower()
    username = activity_input.username
    diff = activity_input.diff.strip()

    if not diff:
        activity.logger.warning("Empty diff provided")
        return ValidatePasswordDiffOutput(
            is_valid=False,
            invalid_lines=[],
            valid_lines=[],
            error_message="Empty diff provided",
        )

    if platform in ["cumulus", "nvos"]:
        return helpers._validate_cumulus_diff(diff, username)
    elif platform == "junos":
        return helpers._validate_junos_diff(diff, username)
    else:
        error_msg = f"No diff parser available for platform: {platform}"
        activity.logger.error(error_msg)
        return ValidatePasswordDiffOutput(
            is_valid=False,
            invalid_lines=diff.split("\n"),
            valid_lines=[],
            error_message=error_msg,
        )


@activity.defn
async def get_password_mappings(
    activity_input: GetPasswordMappingsInput,
) -> GetPasswordMappingsOutput:
    """Get password mapping configuration for a device and username."""
    device = activity_input.device
    username = activity_input.username
    client = get_dcim_client()
    async with client:
        password_mapping_users = await client.get_device_password_mapping_users(device.id)

    if not password_mapping_users:
        raise ApplicationError(
            f"No password mappings found for device {device.name}",
            non_retryable=True,
        )

    if username not in password_mapping_users:
        raise ApplicationError(
            f"No password mapping found for '{username}' on device {device.name}",
            non_retryable=True,
        )

    activity.logger.info(f"Retrieved password mapping for user '{username}')")

    return GetPasswordMappingsOutput(
        username=username,
    )


@activity.defn
async def validate_platform_support(
    activity_input: ValidatePlatformSupportInput,
) -> ValidatePlatformSupportOutput:
    """Validate that the platform is supported for password rotation and return normalized platform name."""
    platform = activity_input.platform

    # Platform mapping for password rotation workflows
    platform_map = {
        Platform.CUMULUS_LINUX: "cumulus",
        Platform.NV_OS: "nvos",
        Platform.JUNIPER_JUNOS: "junos",
    }

    slugified_platform = platform_map.get(platform)

    if not slugified_platform:
        raise ApplicationError(
            f"Platform {platform} is not supported for password rotation workflows",
            non_retryable=True,
        )

    activity.logger.info(f"Platform {platform} is supported.")

    return ValidatePlatformSupportOutput(
        normalized_platform=slugified_platform,
    )


@activity.defn
def format_password_rotation_results(
    activity_input: FormatPasswordRotationResultsInput,
) -> str:
    """Format password rotation results in markdown."""
    successful_count = len(activity_input.successful_devices)
    failed_count = len(activity_input.failed_devices)

    display_lines = [
        f"**Total devices**: {activity_input.total_devices}",
        f"**Updated**: {successful_count}",
        f"**Not Updated**: {failed_count}",
    ]

    if failed_count > 0:
        display_lines.append("")
        display_lines.append("**Devices not updated:**")
        for device_name, failure_data in activity_input.failed_devices.items():
            # Include link to child workflow if available
            if failure_data.get("child_workflow_id"):
                child_workflow_url = build_workflow_url(
                    activity_input.ui_base_url, failure_data["child_workflow_id"]
                )
                display_lines.append(f"[{device_name}]({child_workflow_url})")

    if successful_count > 0:
        display_lines.append("")
        display_lines.append("**Successfully updated devices:**")
        for device_name, success_data in activity_input.successful_devices.items():
            # Include link to child workflow if available
            if success_data.get("child_workflow_id"):
                child_workflow_url = build_workflow_url(
                    activity_input.ui_base_url, success_data["child_workflow_id"]
                )
                display_lines.append(
                    f"[{device_name}]({child_workflow_url}): Password updated successfully"
                )
            else:
                display_lines.append(f"{device_name}: Password updated successfully")

    # Add child workflow links section
    all_devices = {**activity_input.successful_devices, **activity_input.failed_devices}
    if all_devices:
        display_lines.append("")
        display_lines.append("**Child Workflow Links:**")
        for device_name, device_data in all_devices.items():
            if device_data.get("child_workflow_id"):
                child_workflow_url = build_workflow_url(
                    activity_input.ui_base_url, device_data["child_workflow_id"]
                )
                status = "Success" if device_data.get("success", False) else "Failed"
                display_lines.append(f"[{device_name}]({child_workflow_url}) - {status}")

    return "\n".join(display_lines)


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
