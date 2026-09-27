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
"""Operating-system image activities."""

import socket
import time
from datetime import datetime, timedelta

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.os.helpers import (
    check_ztp_success,
    mellanox_connection,
)
from nv_config_manager_workflows.activities.os.models import (
    CleanupMlnxOSInput,
    CleanupMlnxOSOutput,
    DownloadMlnxOSInput,
    DownloadMlnxOSOutput,
    ExecuteZTPInput,
    ExecuteZTPOutput,
    GetCurrentOSInput,
    GetCurrentOSOutput,
    GetMlnxOSVersionInput,
    GetMlnxOSVersionOutput,
    GetOSImageVersionsInput,
    GetOSImageVersionsOutput,
    InstallMlnxOSInput,
    InstallMlnxOSOutput,
    PollImageInput,
    PollImageOutput,
    PollZTPStatusInput,
    PollZTPStatusOutput,
    ReloadMlnxOSInput,
    ReloadMlnxOSOutput,
    UpdateIntendedOSImageInput,
    WaitRebootInput,
    WaitRebootOutput,
)
from nv_config_manager_workflows.runtime import get_dcim_client, get_device_connection


@activity.defn
def get_current_os(activity_input: GetCurrentOSInput) -> GetCurrentOSOutput:
    """Get the current OS version from the device without polling."""
    device = get_device_connection(activity_input.device_data)

    try:
        running_os = device.get_running_image()
        return GetCurrentOSOutput(running_os=running_os)
    except Exception as error:
        raise ApplicationError(f"Failed to get current OS: {str(error)}") from error


@activity.defn
async def get_os_image_versions(
    activity_input: GetOSImageVersionsInput,
) -> GetOSImageVersionsOutput:
    """Get the intended and desired OS image versions for a device."""
    client = get_dcim_client()
    async with client:
        versions = await client.get_os_image_versions(activity_input.device_id)

    return GetOSImageVersionsOutput(
        intended_firmware=versions.intended_firmware,
        desired_firmware=versions.desired_firmware,
        ztp_ipv4_address=versions.ztp_address,
    )


@activity.defn
async def update_intended_os_image(
    activity_input: UpdateIntendedOSImageInput,
) -> None:
    """Update the intended OS image through the selected DCIM provider."""
    client = get_dcim_client()
    async with client:
        await client.set_intended_os_image(
            activity_input.device_id,
            activity_input.desired_firmware,
        )


@activity.defn
def execute_ztp(
    activity_input: ExecuteZTPInput,
) -> ExecuteZTPOutput:
    """Execute ZTP through factory reset."""
    device = get_device_connection(activity_input.device_data)
    device.execute_ztp()
    return ExecuteZTPOutput(start_time=datetime.now().isoformat())


@activity.defn
def poll_image(
    activity_input: PollImageInput,
) -> PollImageOutput:
    """Poll the device until reachable and return its running image."""
    device = get_device_connection(activity_input.device_data)
    start_time = datetime.now()

    image = None
    while datetime.now() - start_time < timedelta(minutes=30):
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Polling image ({elapsed_minutes:.0f}m)")

        try:
            image = device.get_running_image()
            if image == activity_input.expected_image:
                return PollImageOutput(running_image=image)
            time.sleep(30)
        except Exception:
            time.sleep(30)

    if image:
        return PollImageOutput(running_image=image)
    raise ApplicationError("Device did not return running image")


@activity.defn
def poll_ztp_status(
    activity_input: PollZTPStatusInput,
) -> PollZTPStatusOutput:
    """Poll device ZTP status until success."""
    device = get_device_connection(activity_input.device_data)
    start_time = datetime.now()

    ztp_execution_time = None
    if activity_input.ztp_execution_timestamp:
        ztp_execution_time = datetime.fromisoformat(activity_input.ztp_execution_timestamp)

    while datetime.now() - start_time < timedelta(minutes=activity_input.timeout_minutes):
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Polling ZTP ({elapsed_minutes:.0f}m)")

        try:
            if check_ztp_success(device, ztp_execution_time):
                return PollZTPStatusOutput(success=True)
            time.sleep(30)
        except Exception:
            time.sleep(30)

    return PollZTPStatusOutput(success=False)


@activity.defn
def wait_reboot(
    activity_input: WaitRebootInput,
) -> WaitRebootOutput:
    """Wait for a device reboot by checking uptime."""
    device = get_device_connection(activity_input.device_data)
    start_time = datetime.now()
    ztp_execution_timestamp = datetime.fromisoformat(activity_input.ztp_execution_timestamp)

    while datetime.now() - start_time < timedelta(minutes=activity_input.timeout):
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Waiting for reboot ({elapsed_minutes:.0f}m)")
        try:
            uptime = device.get_uptime()
            elapsed_time = (datetime.now() - ztp_execution_timestamp).total_seconds()
            if uptime < elapsed_time:
                return WaitRebootOutput(success=True)
            time.sleep(30)
        except Exception:
            time.sleep(30)

    return WaitRebootOutput(success=False)


@activity.defn
def get_mlnx_os_version(
    activity_input: GetMlnxOSVersionInput,
) -> GetMlnxOSVersionOutput:
    """Get the current running OS versions on a Mellanox device."""
    connection = mellanox_connection(activity_input.device_data)

    try:
        output = connection.execute_command("show images | include version")
        versions = []
        for line in output.splitlines():
            if "version:" in line:
                version = line.split()[2]
                if "-" in version:
                    version = version.split("-")[0]
                versions.append(version)

        if not versions:
            raise ValueError("No version information found in output")

        return GetMlnxOSVersionOutput(current_os_versions=versions)
    finally:
        connection.__del__()


@activity.defn
def download_mlnx_os(
    activity_input: DownloadMlnxOSInput,
) -> DownloadMlnxOSOutput:
    """Download the intended OS version on a Mellanox device."""
    connection = mellanox_connection(activity_input.device_data)

    try:
        ztp_ipv4_address = activity_input.ztp_ipv4_address
        image_name = f"image-X86_64-{activity_input.intended_version}.img"
        output = connection.execute_enable_command(
            command=(
                f"image fetch http://{ztp_ipv4_address}/v1/files/mlnx-os/"
                f"{activity_input.intended_version}/{image_name}"
            ),
            timeout=600,
        )
        return DownloadMlnxOSOutput(download_status=output, image_name=image_name)
    finally:
        connection.__del__()


@activity.defn
def install_mlnx_os(
    activity_input: InstallMlnxOSInput,
) -> InstallMlnxOSOutput:
    """Install the intended OS version on a Mellanox device."""
    connection = mellanox_connection(activity_input.device_data)

    try:
        install_output = connection.execute_enable_command(
            command=f"image install {activity_input.image_name}",
            timeout=1200,
        )

        start_time = datetime.now()
        while datetime.now() - start_time < timedelta(minutes=5):
            boot_next_output = connection.execute_enable_command(
                command="image boot next",
                timeout=600,
            )
            if "install in progress" not in boot_next_output:
                break
            time.sleep(60)

        return InstallMlnxOSOutput(install_status=install_output)
    finally:
        connection.__del__()


@activity.defn
def reload_mlnx_os(
    activity_input: ReloadMlnxOSInput,
) -> ReloadMlnxOSOutput:
    """Reload the device and wait for it to come back online."""
    connection = mellanox_connection(activity_input.device_data)

    try:
        save_config_output = connection.execute_enable_command(
            command="write memory",
            timeout=60,
        )
        reload_output = connection.execute_enable_command(
            command="reload",
            timeout=60,
        )

        time.sleep(60)
        device_ip = activity_input.device_data.host

        start_time = datetime.now()
        while datetime.now() - start_time < timedelta(minutes=30):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((device_ip, 22))
                sock.close()
                if result == 0:
                    return ReloadMlnxOSOutput(
                        save_config_status=save_config_output,
                        reload_status=reload_output,
                        is_online=True,
                    )
            except Exception:
                pass
            time.sleep(10)

        return ReloadMlnxOSOutput(
            save_config_status=save_config_output,
            reload_status=reload_output,
            is_online=False,
        )
    finally:
        connection.__del__()


@activity.defn
def cleanup_mlnx_os(
    activity_input: CleanupMlnxOSInput,
) -> CleanupMlnxOSOutput:
    """Clean up MLNX OS images."""
    connection = mellanox_connection(activity_input.device_data)

    try:
        cleanup_output = connection.execute_enable_command(
            command=f"image delete {activity_input.image_name}",
            timeout=60,
        )
        return CleanupMlnxOSOutput(cleanup_status=cleanup_output)
    finally:
        connection.__del__()


OS_ACTIVITIES = (
    get_current_os,
    get_os_image_versions,
    update_intended_os_image,
    execute_ztp,
    poll_image,
    poll_ztp_status,
    wait_reboot,
    get_mlnx_os_version,
    download_mlnx_os,
    install_mlnx_os,
    reload_mlnx_os,
    cleanup_mlnx_os,
)

__all__ = [
    "OS_ACTIVITIES",
    "CleanupMlnxOSInput",
    "CleanupMlnxOSOutput",
    "DownloadMlnxOSInput",
    "DownloadMlnxOSOutput",
    "ExecuteZTPInput",
    "ExecuteZTPOutput",
    "GetCurrentOSInput",
    "GetCurrentOSOutput",
    "GetMlnxOSVersionInput",
    "GetMlnxOSVersionOutput",
    "GetOSImageVersionsInput",
    "GetOSImageVersionsOutput",
    "InstallMlnxOSInput",
    "InstallMlnxOSOutput",
    "PollImageInput",
    "PollImageOutput",
    "PollZTPStatusInput",
    "PollZTPStatusOutput",
    "ReloadMlnxOSInput",
    "ReloadMlnxOSOutput",
    "UpdateIntendedOSImageInput",
    "WaitRebootInput",
    "WaitRebootOutput",
    "cleanup_mlnx_os",
    "download_mlnx_os",
    "execute_ztp",
    "get_current_os",
    "get_mlnx_os_version",
    "get_os_image_versions",
    "install_mlnx_os",
    "poll_image",
    "poll_ztp_status",
    "reload_mlnx_os",
    "update_intended_os_image",
    "wait_reboot",
]
