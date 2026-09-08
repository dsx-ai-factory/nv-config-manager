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
"""OS Image related activities."""

import socket
import time
from datetime import datetime, timedelta

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.dcim import create_dcim_client
from nv_config_manager.temporal.client.device import (
    MellanoxConnection,
    NetworkConnection,
    NetworkDeviceData,
)


class GetCurrentOSInput(BaseModel):
    """Input for getting current OS version."""

    device_data: NetworkDeviceData


class GetCurrentOSOutput(BaseModel):
    """Output for getting current OS version."""

    running_os: str


class GetOSImageVersionsInput(BaseModel):
    """Input for getting firmware versions."""

    device_id: str


class GetOSImageVersionsOutput(BaseModel):
    """Output for getting firmware versions."""

    intended_firmware: str
    desired_firmware: str
    ztp_ipv4_address: str


class UpdateIntendedOSImageInput(BaseModel):
    """Input for updating intended firmware."""

    device_id: str
    desired_firmware: str


class ExecuteZTPInput(BaseModel):
    """Input for executing ZTP."""

    device_data: NetworkDeviceData


class ExecuteZTPOutput(BaseModel):
    """Output for executing ZTP."""

    start_time: str  # ISO format string


class PollImageInput(BaseModel):
    """Input for polling device image."""

    device_data: NetworkDeviceData
    expected_image: str


class PollImageOutput(BaseModel):
    """Output for image polling."""

    running_image: str | None = None


class PollZTPStatusInput(BaseModel):
    """Input for polling ZTP status."""

    device_data: NetworkDeviceData
    timeout_minutes: int = 30
    ztp_execution_timestamp: str | None = (
        None  # ISO format string, optional for backward compatibility
    )


class PollZTPStatusOutput(BaseModel):
    """Output for ZTP status polling."""

    success: bool


class WaitRebootInput(BaseModel):
    """Input for waiting for reboot."""

    device_data: NetworkDeviceData
    ztp_execution_timestamp: str  # ISO format string
    timeout: int = 10


class WaitRebootOutput(BaseModel):
    """Output for waiting for reboot."""

    success: bool


class GetMlnxOSVersionInput(BaseModel):
    """Input for getting Mellanox OS version."""

    device_data: NetworkDeviceData


class GetMlnxOSVersionOutput(BaseModel):
    """Output for getting Mellanox OS version."""

    current_os_versions: list[str]


class DownloadMlnxOSInput(BaseModel):
    """Input for downloading Mellanox OS."""

    device_data: NetworkDeviceData
    ztp_ipv4_address: str
    intended_version: str


class DownloadMlnxOSOutput(BaseModel):
    """Output for downloading Mellanox OS."""

    download_status: str
    image_name: str


class InstallMlnxOSInput(BaseModel):
    """Input for installing MLNX OS."""

    device_data: NetworkDeviceData
    image_name: str


class InstallMlnxOSOutput(BaseModel):
    """Install MLNX OS Output."""

    install_status: str


class ReloadMlnxOSInput(BaseModel):
    """Input for reloading MLNX OS."""

    device_data: NetworkDeviceData


class ReloadMlnxOSOutput(BaseModel):
    """Output for reloading MLNX OS."""

    save_config_status: str
    reload_status: str
    is_online: bool


class CleanupMlnxOSInput(BaseModel):
    """Input for cleaning up MLNX OS images."""

    device_data: NetworkDeviceData
    image_name: str


class CleanupMlnxOSOutput(BaseModel):
    """Output for cleaning up MLNX OS images."""

    cleanup_status: str


@activity.defn
def get_current_os(activity_input: GetCurrentOSInput) -> GetCurrentOSOutput:
    """Get the current OS version from the device (no polling/waiting)."""
    device = NetworkConnection.from_device_data(activity_input.device_data)

    try:
        # Get the OS version using the device API
        running_os = device.get_running_image()
        return GetCurrentOSOutput(running_os=running_os)
    except Exception as e:
        raise ApplicationError(f"Failed to get current OS: {str(e)}") from e


@activity.defn
async def get_os_image_versions(
    activity_input: GetOSImageVersionsInput,
) -> GetOSImageVersionsOutput:
    """Get the intended and desired os image versions for a device."""
    client = create_dcim_client()
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
    """Update the intended OS image version through the selected DCIM provider."""
    client = create_dcim_client()
    async with client:
        await client.set_intended_os_image(  # type: ignore[attr-defined]
            activity_input.device_id,
            activity_input.desired_firmware,
        )


@activity.defn
def execute_ztp(
    activity_input: ExecuteZTPInput,
) -> ExecuteZTPOutput:
    """Execute ZTP through factory reset."""
    device = NetworkConnection.from_device_data(activity_input.device_data)
    device.execute_ztp()
    return ExecuteZTPOutput(start_time=datetime.now().isoformat())


@activity.defn
def poll_image(
    activity_input: PollImageInput,
) -> PollImageOutput:
    """Poll device until reachable and return running image."""
    device = NetworkConnection.from_device_data(activity_input.device_data)
    start_time = datetime.now()

    # Poll for up to 30 minutes
    image = None
    while datetime.now() - start_time < timedelta(minutes=30):
        # Send heartbeat with progress information
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Polling image ({elapsed_minutes:.0f}m)")

        try:
            # Check if the upgrade has completed
            # This activity may check too soon before reboot
            # so keep looping until we see the expected image
            # or timeout
            image = device.get_running_image()
            if image == activity_input.expected_image:
                return PollImageOutput(
                    running_image=image,
                )
            time.sleep(30)
        except Exception:
            # Wait 30 seconds before retrying
            time.sleep(30)

    # If the device was reachable but never returned the expected image
    # return the last polled image
    if image:
        return PollImageOutput(
            running_image=image,
        )
    raise ApplicationError("Device did not return running image")


def _verify_device_rebooted(device: NetworkConnection, ztp_execution_time: datetime) -> bool:
    """
    Verify device has rebooted since ZTP execution.

    Returns True if device has rebooted (uptime < elapsed time), False otherwise.
    """
    try:
        uptime = device.get_uptime()
        elapsed_time = (datetime.now() - ztp_execution_time).total_seconds()
        # Only accept success if device has rebooted since ZTP was executed
        # (uptime is less than elapsed time since ZTP execution)
        return uptime < elapsed_time
    except Exception:
        # If we can't get uptime, device might be rebooting
        return False


def _check_ztp_success(device: NetworkConnection, ztp_execution_time: datetime | None) -> bool:
    """
    Check if ZTP completed successfully.

    If ztp_execution_time is provided, also verifies device has rebooted.
    Returns True if ZTP is successful and (if applicable) device has rebooted.
    """
    status = device.get_ztp_status()
    if status != "success":
        return False

    # If we have a ZTP execution timestamp, verify device has rebooted
    if ztp_execution_time:
        return _verify_device_rebooted(device, ztp_execution_time)

    # Backward compatibility: if no timestamp provided, accept success immediately
    return True


@activity.defn
def poll_ztp_status(
    activity_input: PollZTPStatusInput,
) -> PollZTPStatusOutput:
    """Poll device ZTP status until success."""
    device = NetworkConnection.from_device_data(activity_input.device_data)
    start_time = datetime.now()

    # Parse ZTP execution timestamp if provided
    ztp_execution_time = None
    if activity_input.ztp_execution_timestamp:
        ztp_execution_time = datetime.fromisoformat(activity_input.ztp_execution_timestamp)

    # Poll for the specified timeout duration
    while datetime.now() - start_time < timedelta(minutes=activity_input.timeout_minutes):
        # Send heartbeat with progress information
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Polling ZTP ({elapsed_minutes:.0f}m)")

        try:
            if _check_ztp_success(device, ztp_execution_time):
                return PollZTPStatusOutput(success=True)
            # Wait 30 seconds before retrying
            time.sleep(30)
        except Exception:
            # Wait 30 seconds before retrying
            time.sleep(30)

    return PollZTPStatusOutput(success=False)


@activity.defn
def wait_reboot(
    activity_input: WaitRebootInput,
) -> WaitRebootOutput:
    """Wait for device to reboot by checking uptime."""
    device = NetworkConnection.from_device_data(activity_input.device_data)
    start_time = datetime.now()
    ztp_execution_timestamp = datetime.fromisoformat(activity_input.ztp_execution_timestamp)

    # Poll for up to the timeout period
    while datetime.now() - start_time < timedelta(minutes=activity_input.timeout):
        # Send heartbeat with progress information
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        activity.heartbeat(f"Waiting for reboot ({elapsed_minutes:.0f}m)")
        try:
            uptime = device.get_uptime()
            elapsed_time = (datetime.now() - ztp_execution_timestamp).total_seconds()
            # If uptime is less than the time elapsed since ZTP was initiated,
            # we've rebooted
            if uptime < elapsed_time:
                return WaitRebootOutput(success=True)
            # Wait 30 seconds before retrying
            time.sleep(30)
        except Exception:
            # Wait 30 seconds before retrying, expected to have failures during reboot
            time.sleep(30)

    return WaitRebootOutput(success=False)


@activity.defn
def get_mlnx_os_version(
    activity_input: GetMlnxOSVersionInput,
) -> GetMlnxOSVersionOutput:
    """Get the current running OS version on Mellanox device."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")

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
    """Download the intended OS version on Mellanox device."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")

    try:
        ztp_ipv4_address = activity_input.ztp_ipv4_address
        image_name = f"image-X86_64-{activity_input.intended_version}.img"
        output = connection.execute_enable_command(
            command=f"image fetch http://{ztp_ipv4_address}/v1/files/mlnx-os/{activity_input.intended_version}/{image_name}",
            timeout=600,
        )
        return DownloadMlnxOSOutput(download_status=output, image_name=image_name)
    finally:
        connection.__del__()


@activity.defn
def install_mlnx_os(
    activity_input: InstallMlnxOSInput,
) -> InstallMlnxOSOutput:
    """Install the intended OS version on Mellanox device."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")

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

        return InstallMlnxOSOutput(
            install_status=install_output,
        )
    finally:
        connection.__del__()


@activity.defn
def reload_mlnx_os(
    activity_input: ReloadMlnxOSInput,
) -> ReloadMlnxOSOutput:
    """Reload the device and wait for it to come back online."""
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")

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
    connection = NetworkConnection.from_device_data(activity_input.device_data)
    if not isinstance(connection, MellanoxConnection):
        raise ValueError("Failed to create MellanoxConnection")

    try:
        cleanup_output = connection.execute_enable_command(
            command=f"image delete {activity_input.image_name}",
            timeout=60,
        )
        return CleanupMlnxOSOutput(cleanup_status=cleanup_output)
    finally:
        connection.__del__()
