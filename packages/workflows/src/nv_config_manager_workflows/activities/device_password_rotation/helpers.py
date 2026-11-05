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
# EOS session diffs are unified diffs: "--- "/"+++ " file headers, "@@ -a,b +c,d @@" hunk
# markers, context lines with a leading space, and changed lines whose sign is followed
# directly by the text. A changed line can itself start with "--" or "++" (a banner
# separator, say), so file headers are only recognised before the first hunk.
_EOS_DIFF_FILE_HEADER_RE = re.compile(r"^(?:---|\+\+\+) ")
_EOS_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


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


def _eos_password_signature(line: str, username: str) -> tuple[str, ...] | None:
    """Return a changed line's tokens without the hash, if it sets the user's sha512 password.

    The line must read "username <user> ... secret sha512 <hash>", or
    "aaa root secret sha512 <hash>" for root, whose account is configured
    outside "username". Any other line returns None.
    """
    tokens = tuple(line[1:].split())
    if len(tokens) < 5 or not tokens[-1].startswith("$6$"):
        return None
    signature = tokens[:-1]
    if username == "root":
        return signature if signature == ("aaa", "root", "secret", "sha512") else None
    if signature[:2] != ("username", username) or signature[-2:] != ("secret", "sha512"):
        return None
    return signature


def _validate_eos_diff(diff: str, username: str) -> ValidatePasswordDiffOutput:
    """Validate an EOS session diff touches only the target user's sha512 secret.

    The policy for auto-approval: exactly one removed and one added line, each
    setting the user's sha512 password, identical apart from the hash. So a
    change of role or privilege cannot ride along with the rotation, an added
    line alone (a new account) is not a rotation, and neither is a diff with
    several matching changes.
    """
    valid_lines: list[str] = []
    invalid_lines: list[str] = []
    removed: list[tuple[str, ...]] = []
    added: list[tuple[str, ...]] = []

    in_hunk = False
    for raw_line in diff.split("\n"):
        line = raw_line.rstrip()
        if not line.strip() or line[0] == " ":
            continue  # unchanged context
        if _EOS_DIFF_HUNK_RE.match(line):
            in_hunk = True
            continue
        if not in_hunk and _EOS_DIFF_FILE_HEADER_RE.match(line):
            continue  # file headers come before the first hunk
        signature = _eos_password_signature(line, username) if line[0] in "+-" else None
        if signature is None:
            invalid_lines.append(line)
            activity.logger.warning("Unexpected line in EOS password diff")
            continue
        valid_lines.append(line)
        (removed if line[0] == "-" else added).append(signature)

    def rejected(
        error_msg: str, *, invalid: list[str], valid: list[str]
    ) -> ValidatePasswordDiffOutput:
        return ValidatePasswordDiffOutput(
            is_valid=False, invalid_lines=invalid, valid_lines=valid, error_message=error_msg
        )

    if invalid_lines:
        return rejected(
            f"Diff contains non-password changes for user '{username}'",
            invalid=invalid_lines,
            valid=valid_lines,
        )
    if not added:
        return rejected(
            f"Diff contains no password changes for user '{username}'",
            invalid=[],
            valid=valid_lines,
        )
    if not removed:
        return rejected(
            f"Diff sets a password for user '{username}' without replacing one, "
            "which creates the account rather than rotating its password",
            invalid=valid_lines,
            valid=[],
        )
    if len(removed) != 1 or len(added) != 1:
        return rejected(
            f"Diff replaces more than one password line for user '{username}'",
            invalid=valid_lines,
            valid=[],
        )
    if removed[0] != added[0]:
        return rejected(
            f"Diff changes more than the password of user '{username}'",
            invalid=valid_lines,
            valid=[],
        )
    activity.logger.info(f"EOS password diff validation successful for user {username}")
    return ValidatePasswordDiffOutput(
        is_valid=True, invalid_lines=[], valid_lines=valid_lines, error_message=None
    )
