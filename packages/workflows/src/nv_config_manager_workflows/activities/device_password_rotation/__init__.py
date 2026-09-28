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
"""Device Password Rotation activity exports."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform

from nv_config_manager_workflows.activities.device_password_rotation.activities import (
    format_password_rotation_results,
    get_password_mappings,
    validate_password_diff,
    validate_platform_support,
    validate_rendered_password_change,
)
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
from nv_config_manager_workflows.workflow_urls import build_workflow_url

DEVICE_PASSWORD_ROTATION_ACTIVITIES = (
    validate_password_diff,
    get_password_mappings,
    validate_platform_support,
    format_password_rotation_results,
    validate_rendered_password_change,
)


__all__ = [
    "DEVICE_PASSWORD_ROTATION_ACTIVITIES",
    "FormatPasswordRotationResultsInput",
    "GetPasswordMappingsInput",
    "GetPasswordMappingsOutput",
    "NetworkDeviceData",
    "Platform",
    "ValidatePasswordDiffInput",
    "ValidatePasswordDiffOutput",
    "ValidatePlatformSupportInput",
    "ValidatePlatformSupportOutput",
    "ValidateRenderedPasswordChangeInput",
    "build_workflow_url",
    "format_password_rotation_results",
    "get_password_mappings",
    "validate_password_diff",
    "validate_platform_support",
    "validate_rendered_password_change",
]
