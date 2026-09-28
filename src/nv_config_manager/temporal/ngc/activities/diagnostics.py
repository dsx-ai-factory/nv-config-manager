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
"""Compatibility facade for package-owned diagnostics activities."""

from nv_config_manager_workflows.activities.diagnostics import (
    COMMAND_DESCRIPTIONS,
    DIAGNOSTICS_ACTIVITIES,
    PLATFORM_COMMANDS,
    RunDiagnosticsInput,
    RunDiagnosticsOutput,
    TechSupportInput,
    TechSupportOutput,
    UploadAttachmentOutput,
    UploadTechSupportFromRedisInput,
    collect_tech_support_bundle,
    get_available_commands,
    run_diagnostic_commands,
    upload_tech_support_from_redis,
    validate_commands,
)

__all__ = [
    "upload_tech_support_from_redis",
    "UploadAttachmentOutput",
    "UploadTechSupportFromRedisInput",
    "COMMAND_DESCRIPTIONS",
    "DIAGNOSTICS_ACTIVITIES",
    "PLATFORM_COMMANDS",
    "RunDiagnosticsInput",
    "RunDiagnosticsOutput",
    "TechSupportInput",
    "TechSupportOutput",
    "collect_tech_support_bundle",
    "get_available_commands",
    "run_diagnostic_commands",
    "validate_commands",
]
