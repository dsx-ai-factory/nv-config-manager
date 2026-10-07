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
"""``dcim.settings`` model for the bundled Nautobot provider."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from nv_config_manager_installer.schema import (
    JobPath,
    JobsConfig,
    NVConfigManagerInstallConfig,
    PostDeployJob,
)

PROVIDER_NAME = "nautobot-2x"
JOBS_PVC_NAME = "nautobot-custom-jobs"
TOKEN_SECRET_NAME = "nautobot-token"


class NautobotSettings(BaseModel):
    """Custom jobs and post-deploy job execution for a local Nautobot."""

    model_config = ConfigDict(extra="forbid")

    jobs: list[JobPath] = Field(default_factory=list)
    jobs_config: JobsConfig = Field(default_factory=JobsConfig)
    run_after_deploy: list[PostDeployJob] = Field(default_factory=list)

    @property
    def requires_local(self) -> bool:
        """Return whether the configured content needs the local Nautobot deployment."""
        return bool(self.jobs or self.run_after_deploy)


def nautobot_settings(config: NVConfigManagerInstallConfig) -> NautobotSettings:
    """Return the validated Nautobot settings block."""
    return NautobotSettings.model_validate(config.dcim.settings)


def store_settings(config: NVConfigManagerInstallConfig, settings: NautobotSettings) -> None:
    """Write *settings* back into ``dcim.settings`` without default noise."""
    config.dcim.settings = settings.model_dump(mode="json", exclude_defaults=True)
