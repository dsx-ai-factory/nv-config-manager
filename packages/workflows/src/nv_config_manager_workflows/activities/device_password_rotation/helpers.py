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
"""Password diff validation helpers."""

import re

from temporalio import activity

from nv_config_manager_workflows.activities.device_password_rotation.models import (
    ValidatePasswordDiffOutput,
)

_JUNOS_EDIT_HEADER_RE = re.compile(r"^\[edit\s+(.+)\]$")
_JUNOS_PASSWORD_LINE_RE = re.compile(
    r'^[+-]\s*encrypted-password\s+"\$[0-9]\$\S+";(\s*##\s*SECRET-DATA)?$'
)


def _validate_cumulus_diff(diff: str, username: str) -> ValidatePasswordDiffOutput:
    """Validate Cumulus/NVOS nv set/unset command diff for password-only changes with detailed output."""
    lines = [line.strip() for line in diff.split("\n") if line.strip()]

    valid_lines = []
    invalid_lines = []

    password_pattern = (
        f"^nv (un)?set system aaa user {re.escape(username)} (hashed-)?password \\S+$"
    )

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
