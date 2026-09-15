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
"""Password Rotation Activities."""

import re
from typing import Any

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nv_config_manager.dcim import create_dcim_client
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.ngc.activities.config import build_workflow_url


class ValidatePasswordDiffInput(BaseModel):
    """Input for validating password diff."""

    diff: str
    username: str
    platform: str


class ValidatePasswordDiffOutput(BaseModel):
    """Output for validating password diff."""

    is_valid: bool
    invalid_lines: list[str]
    valid_lines: list[str]
    error_message: str | None = None


class GetPasswordMappingsInput(BaseModel):
    """Input for getting password mappings."""

    device: NetworkDeviceData
    username: str


class GetPasswordMappingsOutput(BaseModel):
    """Output for getting password mappings."""

    username: str


class ValidatePlatformSupportInput(BaseModel):
    """Input for validating platform support."""

    platform: Platform


class ValidatePlatformSupportOutput(BaseModel):
    """Output for validating platform support."""

    normalized_platform: str


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
        return _validate_cumulus_diff(diff, username)
    elif platform == "junos":
        return _validate_junos_diff(diff, username)
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
    client = create_dcim_client()
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


def _validate_cumulus_diff(diff: str, username: str) -> ValidatePasswordDiffOutput:
    """Validate Cumulus/NVOS nv set/unset command diff for password-only changes with detailed output."""
    lines = [line.strip() for line in diff.split("\n") if line.strip()]

    valid_lines = []
    invalid_lines = []

    password_pattern = f"^nv (un)?set system aaa user {username} (hashed-)?password \\S+$"

    for line in lines:
        if re.match(password_pattern, line):
            valid_lines.append(line)
        else:
            invalid_lines.append(line)
            activity.logger.warning(f"Unexpected command in diff: {line}")

    is_valid = len(invalid_lines) == 0

    if is_valid:
        activity.logger.info(f"Cumulus password diff validation successful for user {username}")
        return ValidatePasswordDiffOutput(
            is_valid=True, invalid_lines=[], valid_lines=valid_lines, error_message=None
        )
    else:
        error_msg = f"Diff contains non-password changes for user '{username}'"
        return ValidatePasswordDiffOutput(
            is_valid=False,
            invalid_lines=invalid_lines,
            valid_lines=valid_lines,
            error_message=error_msg,
        )


_JUNOS_EDIT_HEADER_RE = re.compile(r"^\[edit\s+(.+)\]$")
_JUNOS_PASSWORD_LINE_RE = re.compile(
    r'^[+-]\s*encrypted-password\s+"\$[0-9]\$\S+";(\s*##\s*SECRET-DATA)?$'
)


def _junos_expected_path(username: str) -> str:
    """Return the Junos config-diff path holding a user's encrypted-password."""
    if username == "root":
        return "system root-authentication"
    return f"system login user {username} authentication"


def _validate_junos_diff(diff: str, username: str) -> ValidatePasswordDiffOutput:
    """Validate a Junos hierarchical diff touches only the target user's encrypted-password."""
    expected_path = _junos_expected_path(username)
    valid_lines: list[str] = []
    invalid_lines: list[str] = []
    current_path: str | None = None

    for raw_line in diff.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        header_match = _JUNOS_EDIT_HEADER_RE.match(line)
        if header_match:
            current_path = header_match.group(1)
            continue

        if current_path == expected_path and _JUNOS_PASSWORD_LINE_RE.match(line):
            valid_lines.append(line)
        else:
            invalid_lines.append(line)
            activity.logger.warning("Unexpected line found outside target password stanza")

    if not invalid_lines and valid_lines:
        activity.logger.info(f"Junos password diff validation successful for user {username}")
        return ValidatePasswordDiffOutput(
            is_valid=True, invalid_lines=[], valid_lines=valid_lines, error_message=None
        )
    if not invalid_lines and not valid_lines:
        error_msg = f"Diff contains no password changes for user '{username}'"
        return ValidatePasswordDiffOutput(
            is_valid=False, invalid_lines=[], valid_lines=[], error_message=error_msg
        )
    error_msg = f"Diff contains non-password changes for user '{username}'"
    return ValidatePasswordDiffOutput(
        is_valid=False,
        invalid_lines=invalid_lines,
        valid_lines=valid_lines,
        error_message=error_msg,
    )


class FormatPasswordRotationResultsInput(BaseModel):
    """Format password rotation results activity input."""

    successful_devices: dict[str, Any]
    failed_devices: dict[str, Any]
    total_devices: int
    ui_base_url: str


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
