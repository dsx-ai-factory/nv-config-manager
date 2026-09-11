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
"""Network device client exceptions."""

from __future__ import annotations

import json
from typing import Any

from temporalio.exceptions import ApplicationError


class NetworkDeviceException(ApplicationError):
    """Exception when interacting with a network device."""


class DiffChangedException(NetworkDeviceException):
    """To be thrown if the approved diff is no longer valid."""


class InvalidConfigException(NetworkDeviceException):
    """To be thrown if the config cannot be applied."""


class ConfigApplyFailureException(NetworkDeviceException):
    """To be thrown when config apply fails with ignore_fail state."""

    @staticmethod
    def format_nvue_apply_error(transition_data: dict[str, Any]) -> str:
        """Format NVUE API transition error into human-readable string."""
        if not transition_data or "issue" not in transition_data or not transition_data["issue"]:
            msg = f"Configuration apply failed: {json.dumps(transition_data)}"
            return msg

        formatted_errors = []
        for issue_data in transition_data["issue"].values():
            code = issue_data.get("code", "unknown")
            message = issue_data.get("message", "No message provided")
            severity = issue_data.get("severity", "unknown")
            formatted_errors.append(f"[{severity.upper()}] {code}: {message}")

        error_summary = "\n".join(formatted_errors)
        progress = transition_data.get("progress", "Configuration apply failed")
        return f"{progress}\n\n{error_summary}"


class ConfigSyntaxException(ApplicationError):
    """To be thrown for syntactically invalid config."""

    def __init__(self, message: str) -> None:
        """Initialize with a stable Temporal failure type for retry policies."""
        super().__init__(message, type="ConfigSyntaxException")

    @staticmethod
    def format_nvue_error(error_json: dict[str, Any]) -> str:
        """Formats an NVUE API error response into a human-readable string."""
        if (
            not error_json
            or "validation" not in error_json
            or "selected_errors" not in error_json["validation"]
        ):
            return f"Unknown NVUE API error: {json.dumps(error_json)}"

        errors = error_json["validation"]["selected_errors"]
        if not errors:
            return f"Unknown NVUE API error details: {json.dumps(error_json)}"

        formatted_errors = []
        for error in errors:
            error_message = error.get("error", "Unknown error")
            location = error.get("instanceLocation", "Unknown location")
            formatted_errors.append(f"Error at '{location}': {error_message}")

        return "\n".join(formatted_errors)


class DiffValidationError(NetworkDeviceException):
    """Exception for diff validation failures with detailed information."""

    def __init__(
        self,
        message: str,
        invalid_diff: str,
        valid_lines: list[str] | None = None,
        device_name: str | None = None,
        username: str | None = None,
    ) -> None:
        super().__init__(message, non_retryable=True)
        self.invalid_diff = invalid_diff
        self.valid_lines = valid_lines
        self.device_name = device_name
        self.username = username
