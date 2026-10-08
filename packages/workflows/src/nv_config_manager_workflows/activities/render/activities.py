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
"""Render activity implementation."""

from nv_config_manager_clients._types import ConfigStoreType
from nv_config_manager_clients.render import FileCommit
from temporalio import activity

from nv_config_manager_workflows.activities.render.models import (
    ExecuteRenderInput,
    ExecuteRenderOutput,
)
from nv_config_manager_workflows.runtime import get_config_store_runtime, get_render_client


@activity.defn
async def execute_render(
    activity_input: ExecuteRenderInput,
) -> ExecuteRenderOutput:
    """Execute a render and capture the resulting Config Store snapshot."""
    async with get_render_client() as client:
        updated_files = await client.execute_render(
            activity_input.device_id, activity_input.workflow_id
        )

    config_client = get_config_store_runtime().client(ConfigStoreType.INTENDED)
    async with config_client:
        configs = await config_client.list_device_configs(activity_input.device_id)
    snapshot_files = [
        FileCommit(filename=str(config["filename"]), commit=str(config["version"]))
        for config in configs
    ]

    return ExecuteRenderOutput(
        updated_files=updated_files,
        snapshot_files=snapshot_files,
    )
