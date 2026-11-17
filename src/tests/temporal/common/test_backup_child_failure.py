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
"""Backup parents must observe failed child results before reporting success."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import workflow
from temporalio.exceptions import ChildWorkflowError, RetryState

from nv_config_manager_workflows.workflows.deploy import DeployWorkflow, TenantDeployWorkflow
from nv_config_manager_workflows.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.workflows.os_upgrade import SwitchOSUpgradeWorkflow
from nv_config_manager_workflows.workflows.reprovision import ReprovisionWorkflow


@pytest.mark.parametrize(
    "parent",
    [
        DeployWorkflow,
        TenantDeployWorkflow,
        DevicePasswordRotationWorkflow,
        NVLinkSwitchFirmwareUpgradeWorkflow,
        SwitchOSUpgradeWorkflow,
        ReprovisionWorkflow,
    ],
)
@pytest.mark.parametrize("retry_state", [RetryState.MAXIMUM_ATTEMPTS_REACHED, RetryState.TIMEOUT])
async def test_backup_stage_propagates_child_failure(monkeypatch, parent, retry_state):
    """A done child that failed or timed out never becomes a successful backup."""
    error = ChildWorkflowError(
        "backup did not complete",
        namespace="default",
        workflow_id="backup-child",
        run_id="backup-run",
        workflow_type="BackupWorkflow",
        initiated_event_id=1,
        started_event_id=2,
        retry_state=retry_state,
    )

    class FailedHandle:
        id = "backup-child"

        def done(self):
            return True

        async def result(self):
            raise error

        def __await__(self):
            return self.result().__await__()

    monkeypatch.setattr(workflow, "time", lambda: 0.0)
    monkeypatch.setattr(
        workflow, "patched", lambda patch: patch == "backup-child-result-propagation-v1"
    )
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="parent-workflow"))
    monkeypatch.setattr(workflow, "start_child_workflow", AsyncMock(return_value=FailedHandle()))
    monkeypatch.setattr(workflow, "execute_activity", AsyncMock(return_value="https://example.com"))
    wait = AsyncMock()
    monkeypatch.setattr(workflow, "wait_condition", wait)
    state = parent()
    monkeypatch.setattr(
        state, "_get_ui_base_url", AsyncMock(return_value="https://example.com"), raising=False
    )
    stage_input = parent.BackupStageInput(device_id="device-1", commit_id="commit-1")
    # Let the shared stage executor handle this error in production; here verify
    # each stage body actually surfaces it rather than only inspecting done().
    with pytest.raises(ChildWorkflowError) as caught:
        await parent.perform_backup.__wrapped__(state, stage_input)
    assert caught.value is error
    wait.assert_not_awaited()
