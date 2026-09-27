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
"""NVLink switch firmware activities."""

import asyncio
from datetime import datetime, timedelta

from nv_config_manager_clients._types import ConfigStoreType
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.nvlinkswitch_firmware.helpers import (
    get_desired_firmware_and_os_from_context,
    get_firmware_bundle,
)
from nv_config_manager_workflows.activities.nvlinkswitch_firmware.models import (
    CompareRunningDesiredInput,
    CompareRunningDesiredOutput,
    GetRunningFirmwareInput,
    GetRunningFirmwareOutput,
    RebootDeviceInput,
    RebootDeviceOutput,
    UpdateDeviceContextInput,
    ValidateRenderTargetsInput,
    ValidateTargetFilesInput,
)
from nv_config_manager_workflows.runtime import (
    RuntimeConfigurationError,
    get_config_store_runtime,
    get_dcim_client,
    get_device_connection,
    get_ztp_client,
)


@activity.defn
def get_running_firmware(
    activity_input: GetRunningFirmwareInput,
) -> GetRunningFirmwareOutput:
    """Get the running firmware versions from the device."""
    device = get_device_connection(activity_input.device_data)

    try:
        running_firmware = device.get_firmware_versions()
        firmware_dict = {}
        for component, data in running_firmware.items():
            if isinstance(data, dict) and "actual-firmware" in data:
                firmware_dict[component.lower()] = data["actual-firmware"]
            else:
                firmware_dict[component.lower()] = str(data)

        return GetRunningFirmwareOutput(running_firmware=firmware_dict)
    except Exception as error:
        raise ApplicationError(f"Failed to get running firmware: {str(error)}") from error


@activity.defn
async def compare_running_desired(
    activity_input: CompareRunningDesiredInput,
) -> CompareRunningDesiredOutput:
    """Compare running vs desired firmware versions."""
    try:
        desired_firmware, desired_os = await get_desired_firmware_and_os_from_context(
            activity_input.device_data, activity_input.bundle_version
        )
        os_upgrade_needed = activity_input.running_os != desired_os

        differences = {}
        for component, desired_version in desired_firmware.items():
            if component == "cpld":
                running_version = activity_input.running_firmware.get("cpld1", "")
            else:
                running_version = activity_input.running_firmware.get(component, "")

            if running_version != desired_version:
                differences[component] = {
                    "actual": running_version,
                    "expected": desired_version,
                }

        return CompareRunningDesiredOutput(
            upgrade_needed=os_upgrade_needed or bool(differences),
            desired_os=desired_os,
            desired_firmware=desired_firmware,
            differences=differences,
        )
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to compare versions: {str(error)}") from error


@activity.defn
async def update_device_context(activity_input: UpdateDeviceContextInput) -> None:
    """Update the device's provider-owned firmware intent."""
    try:
        _, desired_os = await get_desired_firmware_and_os_from_context(
            activity_input.device_data, activity_input.bundle_version
        )
        client = get_dcim_client()
        async with client:
            await client.set_device_firmware_intent(
                activity_input.device_data.id,
                activity_input.bundle_version,
                desired_os,
            )
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to update device context: {str(error)}") from error


@activity.defn
async def validate_render_targets(activity_input: ValidateRenderTargetsInput) -> None:
    """Poll until rendered firmware commands contain all expected target files."""
    try:
        bundle = await get_firmware_bundle(activity_input.device_data)
        expected_files = {}
        for component in activity_input.desired_firmware.keys():
            component_info = bundle.components.get(component.lower())
            expected_files[component] = component_info.file_name if component_info else None

        config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
        fw_commands_filename = "fwupdate-commands.txt"
        start_time = datetime.now()
        timeout = timedelta(minutes=3)
        poll_interval = 10
        missing_files: list[str] = []
        last_exception = None

        async with config_client:
            while datetime.now() - start_time < timeout:
                elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
                activity.heartbeat(f"Validating targets ({elapsed_minutes:.0f}m)")

                try:
                    fw_commands_file = await config_client.load_file(
                        device_uuid=activity_input.device_data.id,
                        filename=fw_commands_filename,
                    )
                    missing_files = []

                    for component, expected_filename in expected_files.items():
                        if expected_filename:
                            if expected_filename not in fw_commands_file.content:
                                missing_files.append(f"{component}: {expected_filename}")
                        else:
                            missing_files.append(f"{component}: no file info found")

                    if not missing_files:
                        return
                except Exception as error:
                    last_exception = error

                await asyncio.sleep(poll_interval)

        error_msg = "Timeout waiting for firmware commands to be rendered with new targets. "
        if missing_files:
            error_msg += f"Last check showed missing files: {missing_files}"
        elif last_exception:
            error_msg += (
                f"Unable to load file (path: {fw_commands_file}). "
                f"Last exception: {str(last_exception)}"
            )
        else:
            error_msg += "No successful file loads occurred."

        raise ApplicationError(error_msg)
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to validate render targets: {str(error)}") from error


@activity.defn
async def validate_target_files(activity_input: ValidateTargetFilesInput) -> None:
    """Validate that target firmware files exist on the ZTP server."""
    try:
        bundle = await get_firmware_bundle(activity_input.device_data)
        component_s3_paths = {}
        for component in activity_input.desired_firmware.keys():
            component_info = bundle.components.get(component.lower())
            component_s3_paths[component] = component_info.source_path if component_info else None

        missing_files = []
        async with get_ztp_client() as ztp:
            for component, s3_path in component_s3_paths.items():
                if s3_path:
                    if not await ztp.check_file_exists(s3_path):
                        missing_files.append(f"{component}: {s3_path}")
                else:
                    missing_files.append(f"{component}: no s3_path found in firmware info")

        if missing_files:
            raise ApplicationError(f"Firmware files not found on ZTP server: {missing_files}")
    except RuntimeConfigurationError:
        raise
    except Exception as error:
        raise ApplicationError(f"Failed to validate target files: {str(error)}") from error


@activity.defn
def reboot_device(activity_input: RebootDeviceInput) -> RebootDeviceOutput:
    """Reboot the device through its management API."""
    device = get_device_connection(activity_input.device_data)

    try:
        device.reboot()
        return RebootDeviceOutput(start_time=datetime.now().isoformat())
    except Exception as error:
        raise ApplicationError(f"Failed to initiate reboot: {str(error)}") from error


NVLINKSWITCH_FIRMWARE_ACTIVITIES = (
    get_running_firmware,
    compare_running_desired,
    update_device_context,
    validate_render_targets,
    validate_target_files,
    reboot_device,
)

__all__ = [
    "NVLINKSWITCH_FIRMWARE_ACTIVITIES",
    "CompareRunningDesiredInput",
    "CompareRunningDesiredOutput",
    "GetRunningFirmwareInput",
    "GetRunningFirmwareOutput",
    "RebootDeviceInput",
    "RebootDeviceOutput",
    "UpdateDeviceContextInput",
    "ValidateRenderTargetsInput",
    "ValidateTargetFilesInput",
    "compare_running_desired",
    "get_running_firmware",
    "reboot_device",
    "update_device_context",
    "validate_render_targets",
    "validate_target_files",
]
