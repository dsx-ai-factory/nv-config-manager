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
"""Compatibility exports for package-owned configuration deployment activities."""

from nv_config_manager_workflows.activities.deploy import (
    DEPLOY_ACTIVITIES,
    ConfigApplyActivityInput,
    DiffActivityInput,
    LoadPartialConfigurationActivityInput,
    ValidateConfigDiffActivityInput,
    ValidateConfigDiffActivityOutput,
    WaitForTenantRenderInput,
    WaitForTenantRenderOutput,
    apply_approved_configuration,
    load_intended_configuration,
    load_partial_configuration,
    perform_candidate_diff,
    validate_config_diff,
    wait_for_tenant_render,
)

__all__ = [
    "DEPLOY_ACTIVITIES",
    "ConfigApplyActivityInput",
    "DiffActivityInput",
    "LoadPartialConfigurationActivityInput",
    "ValidateConfigDiffActivityInput",
    "ValidateConfigDiffActivityOutput",
    "WaitForTenantRenderInput",
    "WaitForTenantRenderOutput",
    "apply_approved_configuration",
    "load_intended_configuration",
    "load_partial_configuration",
    "perform_candidate_diff",
    "validate_config_diff",
    "wait_for_tenant_render",
]
