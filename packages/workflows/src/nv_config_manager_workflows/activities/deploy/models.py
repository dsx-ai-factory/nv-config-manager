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
"""Temporal payload models for configuration deployment activities."""

from nv_config_manager_dcim.workflow_models import NetworkDeviceData
from pydantic import BaseModel


class LoadPartialConfigurationActivityInput(BaseModel):
    """Input class for load partial configuration activity."""

    device_data: NetworkDeviceData
    config_file: str
    commit_id: str | None = None


class DiffActivityInput(BaseModel):
    """Input class for diff activity."""

    device_data: NetworkDeviceData
    configuration: str
    partial: bool = False


class ConfigApplyActivityInput(BaseModel):
    """Input class for config apply activity."""

    device_data: NetworkDeviceData
    configuration: str
    approved_diff: str
    partial: bool = False
    commit_confirm: bool = True


class ValidateConfigDiffActivityInput(BaseModel):
    """Input class for validate config diff activity."""

    tenant_config: str
    diff: str
    allowed_patterns: list[str] | None = None
    disallowed_patterns: list[str] | None = None


class ValidateConfigDiffActivityOutput(BaseModel):
    """Output class for validate config diff activity."""

    valid: bool
    message: str | None = None


class WaitForTenantRenderInput(BaseModel):
    """Input class for wait for tenant render activity."""

    device: NetworkDeviceData
    config_id: str | None
    interval: int = 10
    max_attempts: int = 60


class WaitForTenantRenderOutput(BaseModel):
    """Output class for wait for tenant render activity."""

    config_id: str | None


__all__ = [
    "ConfigApplyActivityInput",
    "DiffActivityInput",
    "LoadPartialConfigurationActivityInput",
    "ValidateConfigDiffActivityInput",
    "ValidateConfigDiffActivityOutput",
    "WaitForTenantRenderInput",
    "WaitForTenantRenderOutput",
]
