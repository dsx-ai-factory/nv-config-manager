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
"""Replay contracts for SpX overlay workflows."""

from pathlib import Path
from typing import Any

import pytest
from temporalio.api.history.v1 import (
    ActivityTaskScheduledEventAttributes,
    StartChildWorkflowExecutionInitiatedEventAttributes,
)
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.spx_overlay import (
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_SPX_WORKFLOWS = [
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
]
_EXPECTED_ACTIVITY_NAMES = {
    "spx_overlay_creation.json": [
        "get_vrfs_by_overlay_id",
        "get_available_route_distinguishers",
        "provision_vrf",
        "get_vrfs_by_overlay_id",
        "publish_nats",
    ],
    "spx_overlay_deletion.json": [
        "get_vrfs_by_overlay_id",
        "delete_vrf",
        "delete_vrf",
        "delete_vrf",
        "delete_overlay",
        "publish_nats",
    ],
    "spx_overlay_assignment.json": [
        "get_network_device",
        "get_vrfs_by_overlay_id",
        "get_device_vrfs",
        "assign_vrf_to_device",
        "get_device_interfaces",
        "get_device_interfaces",
        "assign_vrf_to_interface",
        "assign_vrf_to_interface",
        "reconcile_spx_overlay_assignments",
        "remove_unmapped_device_vrfs",
        "publish_nats",
    ],
    "spx_overlay_tenant_change.json": [
        "get_network_device",
        "execute_render",
        "wait_for_tenant_render",
        "publish_nats",
    ],
}


def _history(filename: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (_FIXTURE_ROOT / filename).read_text(),
    )


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


def _scheduled_children(
    history: WorkflowHistory,
) -> list[StartChildWorkflowExecutionInitiatedEventAttributes]:
    return [
        event.start_child_workflow_execution_initiated_event_attributes
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]


async def _scheduled_activity_inputs(filename: str) -> list[tuple[str, dict[str, Any]]]:
    converter = get_data_converter()
    decoded = []
    for item in _scheduled_activities(_history(filename)):
        [activity_input] = await converter.decode(item.input.payloads)
        decoded.append((item.activity_type.name, activity_input))
    return decoded


async def _scheduled_child_inputs(filename: str) -> list[tuple[str, dict[str, Any]]]:
    converter = get_data_converter()
    decoded = []
    for item in _scheduled_children(_history(filename)):
        [workflow_input] = await converter.decode(item.input.payloads)
        decoded.append((item.workflow_type.name, workflow_input))
    return decoded


def _inputs_for(
    scheduled: list[tuple[str, dict[str, Any]]],
    command_name: str,
) -> list[dict[str, Any]]:
    return [command_input for name, command_input in scheduled if name == command_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_spx_overlay_histories_replay(history_filename: str) -> None:
    """The extracted activities remain deterministic against main-era histories."""
    replayer = Replayer(workflows=_SPX_WORKFLOWS, data_converter=get_data_converter())

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_spx_overlay_histories_preserve_activity_order(history_filename: str) -> None:
    """Captured histories freeze every scheduled activity type name and order."""
    scheduled = await _scheduled_activity_inputs(history_filename)

    assert [name for name, _ in scheduled] == _EXPECTED_ACTIVITY_NAMES[history_filename]


@pytest.mark.asyncio
async def test_spx_overlay_lifecycle_histories_preserve_dcim_arguments() -> None:
    creation = await _scheduled_activity_inputs("spx_overlay_creation.json")
    deletion = await _scheduled_activity_inputs("spx_overlay_deletion.json")

    [allocation] = _inputs_for(creation, "get_available_route_distinguishers")
    [provision] = _inputs_for(creation, "provision_vrf")
    deletions = _inputs_for(deletion, "delete_vrf")
    [overlay_deletion] = _inputs_for(deletion, "delete_overlay")

    assert allocation == {
        "namespace_tag": "mock_tag",
        "rd_max": 65000,
        "rd_min": 60000,
        "site": "mock_site",
    }
    assert provision == {
        "namespaces": ["mock_namespace1", "mock_namespace2", "mock_namespace3"],
        "overlay_id": "mock_overlay_id",
        "route_distinguisher": "*:60004",
        "site": "mock_site",
        "tenant": "mock_tenant",
    }
    assert deletions == [
        {"vnid": 60004, "vrf_id": "mock_namespace1"},
        {"vnid": 60004, "vrf_id": "mock_namespace2"},
        {"vnid": 60004, "vrf_id": "mock_namespace3"},
    ]
    assert overlay_deletion == {"overlay_id": "mock_overlay_id", "site": "mock_site"}


@pytest.mark.asyncio
async def test_spx_overlay_assignment_history_preserves_reconciliation_arguments() -> None:
    scheduled = await _scheduled_activity_inputs("spx_overlay_assignment.json")

    interface_queries = _inputs_for(scheduled, "get_device_interfaces")
    assignments = _inputs_for(scheduled, "assign_vrf_to_interface")
    [reconciliation] = _inputs_for(scheduled, "reconcile_spx_overlay_assignments")
    [cleanup] = _inputs_for(scheduled, "remove_unmapped_device_vrfs")

    assert interface_queries == [
        {"device_id": "mock_device_id", "interface_names": ["swp1", "swp2"]},
        {"device_id": "mock_device_id", "interface_names": None},
    ]
    assert assignments == [
        {"interface_id": "interface1_id", "vrf_id": "mock_namespace1"},
        {"interface_id": "interface2_id", "vrf_id": "mock_namespace1"},
    ]
    assert reconciliation == {
        "device_id": "mock_device_id",
        "device_interface_ids": ["interface1_id", "interface2_id", "interface3_id"],
        "interface_ids": ["interface1_id", "interface2_id"],
        "overlay_id": "mock_overlay_id",
        "site": "mock_site",
    }
    assert cleanup == {"device_id": "mock_device_id", "vrf_ids": []}


@pytest.mark.asyncio
async def test_spx_overlay_tenant_change_history_preserves_child_contracts() -> None:
    children = await _scheduled_child_inputs("spx_overlay_tenant_change.json")

    assert [name for name, _ in children] == [
        "SpXOverlayAssignmentWorkflow",
        "TenantDeployWorkflow",
    ]
    assignment_input, deploy_input = [command_input for _, command_input in children]
    assert assignment_input["overlay_id"] == "mock_overlay_id"
    assert assignment_input["port_names"] == ["swp1", "swp2", "swp3"]
    assert assignment_input["device"]["id"] == "mock_device_id_with_vrf"
    assert deploy_input["device"]["id"] == "mock_device_id_with_vrf"
    assert deploy_input["tenant_config_commit_id"] == "7"
    assert deploy_input["intended_config_commit_id"] == "11"
    assert deploy_input["use_full_intended_config"] is False
