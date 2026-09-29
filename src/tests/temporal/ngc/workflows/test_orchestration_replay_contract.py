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

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.deploy import TenantDeployWorkflow
from nv_config_manager.temporal.ngc.workflows.multi_deploy import (
    BatchDeployWorkflow,
    MultiDeployWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.reprovision import ReprovisionWorkflow
from nv_config_manager.temporal.ngc.workflows.site_backup import SiteBackupWorkflow

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_WORKFLOW_BY_HISTORY = {
    "batch_deploy.json": BatchDeployWorkflow,
    "multi_deploy.json": MultiDeployWorkflow,
    "tenant_deploy.json": TenantDeployWorkflow,
    "reprovision.json": ReprovisionWorkflow,
    "site_backup.json": SiteBackupWorkflow,
}
_EXPECTED_ACTIVITY_NAMES = {
    "batch_deploy.json": [
        "apply_approved_configuration",
        "apply_approved_configuration",
        "get_ui_base_url",
        "publish_nats",
    ],
    "multi_deploy.json": [
        "get_network_devices",
        "load_intended_configuration",
        "load_intended_configuration",
        "load_intended_configuration",
        "perform_candidate_diff",
        "perform_candidate_diff",
        "perform_candidate_diff",
        "get_ui_base_url",
        "publish_nats",
    ],
    "tenant_deploy.json": [
        "get_network_device",
        "load_partial_configuration",
        "perform_candidate_diff",
        "validate_config_diff",
        "apply_approved_configuration",
        "get_ui_base_url",
        "publish_nats",
    ],
    "reprovision.json": [
        "get_ui_base_url",
        "get_network_device",
        "execute_ztp",
        "poll_ztp_status",
        "get_ui_base_url",
        "publish_nats",
    ],
    "site_backup.json": [
        "get_network_devices",
        "get_ui_base_url",
        "publish_nats",
    ],
}
_EXPECTED_CHILD_WORKFLOW_NAMES = {
    "batch_deploy.json": ["BackupWorkflow", "BackupWorkflow"],
    "multi_deploy.json": ["BatchDeployWorkflow"],
    "tenant_deploy.json": ["BackupWorkflow"],
    "reprovision.json": ["BackupWorkflow", "BackupWorkflow"],
    "site_backup.json": ["BackupWorkflow", "BackupWorkflow"],
}


def _history(filename: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (_FIXTURE_ROOT / filename).read_text(),
    )


def _scheduled_activity_names(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


def _scheduled_child_workflow_names(history: WorkflowHistory) -> list[str]:
    return [
        event.start_child_workflow_execution_initiated_event_attributes.workflow_type.name
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _WORKFLOW_BY_HISTORY)
async def test_main_orchestration_histories_replay(history_filename: str) -> None:
    """Current workflow code remains deterministic against main-era histories."""
    replayer = Replayer(
        workflows=[_WORKFLOW_BY_HISTORY[history_filename]],
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.parametrize("history_filename", _WORKFLOW_BY_HISTORY)
def test_main_orchestration_histories_preserve_command_order(history_filename: str) -> None:
    """Fixtures exercise and freeze each orchestration scenario's command sequence."""
    history = _history(history_filename)

    assert _scheduled_activity_names(history) == _EXPECTED_ACTIVITY_NAMES[history_filename]
    assert (
        _scheduled_child_workflow_names(history) == _EXPECTED_CHILD_WORKFLOW_NAMES[history_filename]
    )
