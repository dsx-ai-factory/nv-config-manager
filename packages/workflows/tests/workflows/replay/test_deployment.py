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
"""Replay contracts for configuration deployment and firmware workflows."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.workflows.backup import BackupWorkflow
from nv_config_manager_workflows.workflows.config_diff import ConfigDiffWorkflow
from nv_config_manager_workflows.workflows.deploy import DeployWorkflow
from nv_config_manager_workflows.workflows.infiniband_mlnx_os_upgrade import (
    InfinibandMlnxOSUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.os_upgrade import SwitchOSUpgradeWorkflow

from .history import load_history

_DEPLOYMENT_WORKFLOWS = [
    BackupWorkflow,
    ConfigDiffWorkflow,
    DeployWorkflow,
    InfinibandMlnxOSUpgradeWorkflow,
    NVLinkSwitchFirmwareUpgradeWorkflow,
    SwitchOSUpgradeWorkflow,
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history_filename",
    [
        "backup.json",
        "config_diff.json",
        "deploy.json",
        "infiniband_mlnx_os_upgrade.json",
        "nvlinkswitch_firmware_upgrade.json",
        "switch_os_upgrade.json",
    ],
)
async def test_deployment_histories_replay(history_filename: str) -> None:
    """Package extraction must remain deterministic against successful histories."""
    replayer = Replayer(workflows=_DEPLOYMENT_WORKFLOWS, data_converter=get_data_converter())

    await replayer.replay_workflow(load_history(history_filename))
