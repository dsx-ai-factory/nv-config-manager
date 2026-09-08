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
"""VPC Creation Workflow Tests."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.worker import Worker

from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.activities.nautobot import (
    DeleteOverlayInput,
    DeleteOverlayOutput,
    GetAvailableRouteDistinguishersInput,
    GetAvailableRouteDistinguishersOutput,
    ProvisionVrfInput,
    QueryVRFByVPCInput,
    Vrf,
    VrfDeletionActivityInput,
)
from nv_config_manager.temporal.ngc.workflows.spx_overlay import (
    SpXOverlayCreationInput,
    SpXOverlayCreationWorkflow,
    SpXOverlayCreationWorkflowOutput,
    SpXOverlayDeletionInput,
    SpXOverlayDeletionWorkflow,
    SpXOverlayDeletionWorkflowOutput,
)


def make_test_vrf(namespace: int, with_interfaces: bool = False) -> Any:
    mock_interfaces = []
    if with_interfaces:
        mock_interfaces = [
            {
                "name": "swp1",
                "device": {"name": "mock_device1"},
            }
        ]
    return {
        "id": namespace,
        "name": "SpXTenant60004",
        "rd": "*:60004",
        "namespace": {"name": namespace, "location": {"name": "mock_site"}},
        "interfaces": mock_interfaces,
    }


# Global state for mock activities
_mock_state = {
    "failure_scenario": False,
    "vrfs_exist": False,
    "provision_succeeded": False,
    "with_interfaces": False,
}


@activity.defn(name="get_available_route_distinguishers")
async def mock_get_available_route_distinguishers(
    input: GetAvailableRouteDistinguishersInput,
) -> GetAvailableRouteDistinguishersOutput:
    """Mock activity for getting available route distinguishers."""
    return GetAvailableRouteDistinguishersOutput(
        route_distinguisher="*:60004",
        namespaces=["mock_namespace1", "mock_namespace2", "mock_namespace3"],
    )


@activity.defn(name="get_vrfs_by_overlay_id")
async def mock_get_vrfs_by_overlay_id(
    input: QueryVRFByVPCInput,
) -> list[Vrf] | None:
    """Mock activity for getting VRFs by VPC ID."""
    with_interfaces = _mock_state.get("with_interfaces", False)

    if _mock_state["vrfs_exist"] or _mock_state["provision_succeeded"]:
        # Return existing VRFs or successfully provisioned VRFs
        return [
            Vrf.from_nautobot_graphql(
                make_test_vrf("mock_namespace1", with_interfaces=with_interfaces)
            ),
            Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace2")),
            Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace3")),
        ]
    else:
        # No VRFs exist yet
        return None


@activity.defn(name="provision_vrf")
async def mock_provision_vrf(input: ProvisionVrfInput) -> None:
    """Mock activity for provisioning a VRF."""
    if _mock_state["failure_scenario"]:
        raise Exception("The fields namespace, rd must make a unique set.")

    # Only set provision_succeeded if we didn't raise an exception
    _mock_state["provision_succeeded"] = True


@activity.defn(name="delete_vrf")
async def mock_delete_vrf(input: VrfDeletionActivityInput) -> None:
    """Mock activity for deleting a VRF."""
    pass


@activity.defn(name="delete_overlay")
async def mock_delete_overlay(input: DeleteOverlayInput) -> DeleteOverlayOutput:
    """Mock activity for deleting a VPC overlay."""
    return DeleteOverlayOutput(deleted=True, overlay_name=input.overlay_id)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_spx_overlay_creation_workflow(
    mock_time,
    mock_nats_client,
    env,
):

    # Reset mock state
    _mock_state["failure_scenario"] = True
    _mock_state["vrfs_exist"] = False
    _mock_state["provision_succeeded"] = False

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SpXOverlayCreationWorkflow],
        activities=[
            mock_get_available_route_distinguishers,
            mock_get_vrfs_by_overlay_id,
            mock_provision_vrf,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(1),
    ):
        workflow_input = SpXOverlayCreationInput(
            namespace_tag="mock_tag",
            site="mock_site",
            overlay_id="mock_overlay_id",
            tenant="mock_tenant",
        )
        workflow_id = str(uuid.uuid4())

        handle = await env.client.start_workflow(
            SpXOverlayCreationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )

        stages = await handle.query("stages")
        while stages[0]["state"] != "FAILED":
            await asyncio.sleep(1)
            stages = await handle.query("stages")

        # Disable failure scenario for retry
        _mock_state["failure_scenario"] = False

        await handle.signal("retry", "create_spx_overlay")

        result = await handle.result()
        assert result == SpXOverlayCreationWorkflowOutput(
            created_vrfs=[
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace1")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace2")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace3")),
            ],
            existing_vrfs=[],
        )

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Assign an RD and create VRF.",
                "execution_time": 0.0,
                "input": {
                    "namespace_tag": "mock_tag",
                    "rd_max": 65000,
                    "rd_min": 60000,
                    "site": "mock_site",
                    "tenant": "mock_tenant",
                    "overlay_id": "mock_overlay_id",
                },
                "name": "create_spx_overlay",
                "output": {
                    "created_vrfs": [
                        {
                            "id": "mock_namespace1",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace1",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace2",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace2",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace3",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace3",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                    ],
                    "display": (
                        "Created VRFs:\n"
                        "|     name     |   namespace   |   site  |       id      |   rd  |interface_count|\n"
                        "|--------------|---------------|---------|---------------|-------|---------------|\n"
                        "|SpXTenant60004|mock_namespace1|mock_site|mock_namespace1|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace2|mock_site|mock_namespace2|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace3|mock_site|mock_namespace3|*:60004|       0       |"
                    ),
                    "existing_vrfs": [],
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 1,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "FAILED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            }
        ]

        # Run again against a VPC that already has VRFS
        _mock_state["vrfs_exist"] = True
        _mock_state["provision_succeeded"] = False

        workflow_id_2 = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SpXOverlayCreationWorkflow.run,
            workflow_input,
            id=workflow_id_2,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )

        result = await handle.result()
        assert result == SpXOverlayCreationWorkflowOutput(
            created_vrfs=[],
            existing_vrfs=[
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace1")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace2")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace3")),
            ],
        )

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Assign an RD and create VRF.",
                "execution_time": 0.0,
                "input": {
                    "namespace_tag": "mock_tag",
                    "rd_max": 65000,
                    "rd_min": 60000,
                    "site": "mock_site",
                    "tenant": "mock_tenant",
                    "overlay_id": "mock_overlay_id",
                },
                "name": "create_spx_overlay",
                "output": {
                    "created_vrfs": [],
                    "display": (
                        "VRFs already exist for Overlay ID mock_overlay_id:\n"
                        " |     name     |   namespace   |   site  |       id      |   rd  |interface_count|\n"
                        "|--------------|---------------|---------|---------------|-------|---------------|\n"
                        "|SpXTenant60004|mock_namespace1|mock_site|mock_namespace1|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace2|mock_site|mock_namespace2|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace3|mock_site|mock_namespace3|*:60004|       0       |"
                    ),
                    "existing_vrfs": [
                        {
                            "id": "mock_namespace1",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace1",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace2",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace2",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace3",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace3",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                    ],
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            }
        ]

        assert mock_nats_client.return_value.publish.called


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_spx_overlay_deletion_workflow(
    mock_time,
    mock_nats_client,
    env,
):

    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SpXOverlayDeletionWorkflow],
        activities=[
            mock_get_vrfs_by_overlay_id,
            mock_delete_vrf,
            mock_delete_overlay,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(1),
    ):
        workflow_input = SpXOverlayDeletionInput(
            overlay_id="mock_overlay_id",
            site="mock_site",
            namespace_tag="mock_tag",
        )
        workflow_id = str(uuid.uuid4())

        # Test with No VRFS found for VPC
        _mock_state["vrfs_exist"] = False
        _mock_state["provision_succeeded"] = False

        handle = await env.client.start_workflow(
            SpXOverlayDeletionWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )
        result = await handle.result()
        assert result == SpXOverlayDeletionWorkflowOutput(
            deleted_vrfs=[],
            in_use_vrfs=[],
        )

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Validate and delete DCIM VRFs tied to the VPC.",
                "execution_time": 0.0,
                "input": {
                    "namespace_tag": "mock_tag",
                    "site": "mock_site",
                    "overlay_id": "mock_overlay_id",
                },
                "name": "delete_spx_overlay",
                "output": {
                    "deleted_vrfs": [],
                    "display": "Deleted overlay mock_overlay_id",
                    "in_use_vrfs": [],
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            }
        ]

        # Test with In Use VRFS - mock should return VRFs with interfaces
        _mock_state["vrfs_exist"] = False
        _mock_state["provision_succeeded"] = True  # Simulate VRFs were successfully provisioned
        _mock_state["with_interfaces"] = True

        workflow_id_2 = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SpXOverlayDeletionWorkflow.run,
            workflow_input,
            id=workflow_id_2,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )
        result = await handle.result()
        assert result == SpXOverlayDeletionWorkflowOutput(
            deleted_vrfs=[],
            in_use_vrfs=[
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace1", with_interfaces=True))
            ],
        )

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Validate and delete DCIM VRFs tied to the VPC.",
                "execution_time": 0.0,
                "input": {
                    "namespace_tag": "mock_tag",
                    "site": "mock_site",
                    "overlay_id": "mock_overlay_id",
                },
                "name": "delete_spx_overlay",
                "output": {
                    "deleted_vrfs": [],
                    "display": (
                        "Unable to delete Overlay mock_overlay_id, the following VRFs are in use:\n"
                        " |     name     |   namespace   |   site  |       id      |   rd  |interface_count|\n"
                        "|--------------|---------------|---------|---------------|-------|---------------|\n"
                        "|SpXTenant60004|mock_namespace1|mock_site|mock_namespace1|*:60004|       1       |"
                    ),
                    "in_use_vrfs": [
                        {
                            "id": "mock_namespace1",
                            "interface_count": 1,
                            "interfaces": ["mock_device1:swp1"],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace1",
                            "rd": "*:60004",
                            "site": "mock_site",
                        }
                    ],
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            }
        ]

        # Test clean delete
        _mock_state["vrfs_exist"] = False
        _mock_state["provision_succeeded"] = True  # VRFs were successfully provisioned
        _mock_state["with_interfaces"] = False  # No interfaces, can be deleted

        workflow_id_3 = str(uuid.uuid4())
        handle = await env.client.start_workflow(
            SpXOverlayDeletionWorkflow.run,
            workflow_input,
            id=workflow_id_3,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )
        result = await handle.result()
        assert result == SpXOverlayDeletionWorkflowOutput(
            deleted_vrfs=[
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace1")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace2")),
                Vrf.from_nautobot_graphql(make_test_vrf("mock_namespace3")),
            ],
            in_use_vrfs=[],
        )

        stages = await handle.query("stages")
        assert stages == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Validate and delete DCIM VRFs tied to the VPC.",
                "execution_time": 0.0,
                "input": {
                    "namespace_tag": "mock_tag",
                    "site": "mock_site",
                    "overlay_id": "mock_overlay_id",
                },
                "name": "delete_spx_overlay",
                "output": {
                    "deleted_vrfs": [
                        {
                            "id": "mock_namespace1",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace1",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace2",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace2",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                        {
                            "id": "mock_namespace3",
                            "interface_count": 0,
                            "interfaces": [],
                            "name": "SpXTenant60004",
                            "namespace": "mock_namespace3",
                            "rd": "*:60004",
                            "site": "mock_site",
                        },
                    ],
                    "display": (
                        "VRFs deleted for Overlay ID mock_overlay_id:\n"
                        "|     name     |   namespace   |   site  |       id      |   rd  |interface_count|\n"
                        "|--------------|---------------|---------|---------------|-------|---------------|\n"
                        "|SpXTenant60004|mock_namespace1|mock_site|mock_namespace1|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace2|mock_site|mock_namespace2|*:60004|       0       |\n"
                        "|SpXTenant60004|mock_namespace3|mock_site|mock_namespace3|*:60004|       0       |\n"
                        "\nDeleted overlay mock_overlay_id"
                    ),
                    "in_use_vrfs": [],
                },
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            }
        ]
