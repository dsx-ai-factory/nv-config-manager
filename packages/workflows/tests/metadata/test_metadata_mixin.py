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

from collections.abc import Sequence

import pytest
from pydantic import BaseModel
from temporalio import activity

from nv_config_manager_workflows.metadata import RequiredActivity, WorkflowMetadataMixin
from nv_config_manager_workflows.registration import workflow_required_activity_names


class WorkflowInput(BaseModel):
    device: str


@activity.defn
async def collect_facts() -> None: ...


class DeviceBackupWorkflow(WorkflowMetadataMixin):
    """Back up one device."""

    workflow_name = "Device Backup"
    workflow_description = "Back up one device"
    workflow_input_class = WorkflowInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/backup"
    workflow_required_activities = (collect_facts,)


def test_metadata_defaults_fail_closed() -> None:
    assert not WorkflowMetadataMixin.workflow_api_enabled
    assert not WorkflowMetadataMixin.workflow_mcp_enabled
    assert WorkflowMetadataMixin.workflow_api_endpoint is None
    assert WorkflowMetadataMixin.workflow_required_activities == ()
    assert WorkflowMetadataMixin.get_workflow_required_activities() == ()


def test_required_activities_are_composed_across_the_mro() -> None:
    class Publisher:
        workflow_required_activities: Sequence[RequiredActivity] = (collect_facts,)

    class ArchivedWorkflow(WorkflowMetadataMixin, Publisher):
        workflow_required_activities = ("store_results",)

    assert ArchivedWorkflow.get_workflow_required_activities() == (
        collect_facts,
        "store_results",
    )


def test_a_requirement_shared_with_a_mixin_is_reported_once() -> None:
    """Restating what a base already requires is a redundancy, not a second entry."""

    class Publisher:
        workflow_required_activities: Sequence[RequiredActivity] = ("collect_facts",)

    class ArchivedWorkflow(WorkflowMetadataMixin, Publisher):
        workflow_required_activities = (collect_facts, "store_results")

    assert workflow_required_activity_names(ArchivedWorkflow) == (
        "collect_facts",
        "store_results",
    )


def test_metadata_accessors_read_subclass_declarations() -> None:
    assert DeviceBackupWorkflow.get_workflow_name() == "Device Backup"
    assert DeviceBackupWorkflow.get_workflow_description() == "Back up one device"
    assert DeviceBackupWorkflow.get_workflow_input_class() is WorkflowInput
    assert DeviceBackupWorkflow.get_workflow_api_enabled()
    assert DeviceBackupWorkflow.get_workflow_api_endpoint() == "/backup"
    assert DeviceBackupWorkflow.get_workflow_required_activities() == (collect_facts,)
    assert DeviceBackupWorkflow.get_workflow_cli_name() == "device-backup"


def test_missing_name_is_reported_by_the_accessor() -> None:
    with pytest.raises(ValueError, match="missing workflow_name"):
        WorkflowMetadataMixin.get_workflow_name()


@pytest.mark.asyncio
async def test_input_canonicalization_is_identity_by_default() -> None:
    body = WorkflowInput(device="leaf-01")

    assert await DeviceBackupWorkflow.canonicalize_input(body) is body
