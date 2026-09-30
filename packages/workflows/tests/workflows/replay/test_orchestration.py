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
"""Replay contracts for configuration orchestration workflows."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.workflows.deploy import TenantDeployWorkflow
from nv_config_manager_workflows.workflows.multi_deploy import (
    BatchDeployWorkflow,
    MultiDeployWorkflow,
)
from nv_config_manager_workflows.workflows.reprovision import ReprovisionWorkflow
from nv_config_manager_workflows.workflows.site_backup import SiteBackupWorkflow

from .history import load_history

_WORKFLOW_BY_HISTORY = {
    "batch_deploy.json": BatchDeployWorkflow,
    "multi_deploy.json": MultiDeployWorkflow,
    "tenant_deploy.json": TenantDeployWorkflow,
    "reprovision.json": ReprovisionWorkflow,
    "site_backup.json": SiteBackupWorkflow,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _WORKFLOW_BY_HISTORY)
async def test_main_orchestration_histories_replay(history_filename: str) -> None:
    """Current workflow code remains deterministic against main-era histories."""
    replayer = Replayer(
        workflows=[_WORKFLOW_BY_HISTORY[history_filename]],
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(load_history(history_filename))
