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
"""Tests for Multi-Deploy Workflow."""

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.backup import (
    load_running_configuration,
    persist_config_backup,
    record_backup_config_manager_plugin,
)
from nv_config_manager.temporal.ngc.activities.config import get_ui_base_url
from nv_config_manager.temporal.ngc.activities.deploy import (
    apply_approved_configuration,
    load_intended_configuration,
    perform_candidate_diff,
)
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    GetNetworkDevicesInput,
    GetNetworkDevicesOutput,
)
from nv_config_manager.temporal.ngc.workflows.backup import BackupInput, BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.multi_deploy import (
    BatchDeployInput,
    BatchDeployWorkflow,
    DeviceDiffData,
    DiffGroup,
    MultiDeployInput,
    MultiDeployWorkflow,
    _format_batch_status,
)


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    """Mock get_network_device activity."""
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name=f"mock_device_{activity_input.device_id[-1]}",
            role="spine",
            platform="cumulus-linux",
            device_type="sn4000",
            site="SITEA",
            primary_ip4=f"10.0.0.{activity_input.device_id[-1]}",
            primary_ip6=None,
        )
    )


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Mock get_network_devices activity."""
    devices = []
    if activity_input.roles and "spine" in activity_input.roles:
        # Create 3 mock devices
        for i in range(1, 4):
            devices.append(
                NetworkDeviceData(
                    id=f"device_id_{i}",
                    name=f"spine-{i:02d}",
                    role="spine",
                    platform="cumulus-linux",
                    device_type="sn4000",
                    site="SITEA",
                    primary_ip4=f"10.0.0.{i}",
                    primary_ip6=None,
                )
            )
    return GetNetworkDevicesOutput(devices=devices)


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(device_data: NetworkDeviceData) -> tuple[str, str, str]:
    return (
        "mock intended config",
        "mock_commit_id",
        "https://gitlab.example.com/example-user/intended-network-configs/-/blob/mock_commit_id/SITEA/MOCK_DEVICE/startup.yaml",
    )


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(activity_input) -> str:
    return "mock_commit_id"


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(activity_input) -> tuple[bool, str]:
    markdown = """
[Configuration Backup](https://gitlab.example.com/example-user/deployed-network-configs/-/blob/main/SITEA/MOCK_DEVICE/startup.yaml)
[Latest Commit](https://gitlab.example.com/example-user/deployed-network-configs/-/commit/mock_commit_id)
"""
    return True, f"Persisted new backup configuration:\n{markdown}"


@workflow.defn(name="BackupWorkflow", sandboxed=False)
class MockBatchBackupWorkflow:
    """Return mixed terminal results for batch backup aggregation tests."""

    @workflow.run
    async def run(self, workflow_input: BackupInput) -> bool:
        """Fail one device and report a changed backup for the other."""
        if workflow_input.device_id == "device_2":
            raise ApplicationError("mock backup failure", non_retryable=True)
        return True


def _large_device_diffs(count: int, config_size: int = 30_000) -> list[DeviceDiffData]:
    """Build representative multi-deploy device data without exposing real configurations."""
    return [
        DeviceDiffData(
            device=NetworkDeviceData(
                id=f"device_{index}",
                name=f"spine-{index:03d}",
                role="spine",
                platform="cumulus-linux",
                device_type="sn4000",
                site="SITEA",
                primary_ip4=f"10.0.{index // 255}.{index % 255}",
                primary_ip6=None,
                config_context={"device_index": index, "feature_flags": ["nvcm"]},
            ),
            diff="- old config line\n+ new config line",
            intended_config=f"# device {index}\n" + ("x" * config_size),
            commit_id=f"commit-{index}",
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=0.0)
async def test_group_and_batch_creates_batch_subsets(_):
    """Group matching diffs and split their devices into bounded batches."""
    workflow_instance = MultiDeployWorkflow()
    device_diffs = _large_device_diffs(115)
    stage_input = MultiDeployWorkflow.GroupAndBatchStageInput(
        device_diffs=device_diffs,
        max_batch_size=5,
    )

    output = await MultiDeployWorkflow.group_and_batch.__wrapped__(  # type: ignore[attr-defined]
        workflow_instance,
        stage_input,
    )

    assert len(output.diff_groups) == 1
    assert len(output.diff_groups[0].devices) == 115
    assert len(output.batches) == 23
    assert all(len(batch) == 5 for batch in output.batches)


def test_batch_deploy_input_supports_legacy_and_canonical_device_fields():
    """Read legacy inputs while allowing new inputs to serialize each device once."""
    device_diffs = _large_device_diffs(115)
    assert BatchDeployInput.model_json_schema()["properties"]["batch_devices"]["deprecated"] is True
    legacy_input = BatchDeployInput.model_validate(
        {
            "diff_group": {
                "diff_hash": "shared-diff",
                "diff_content": device_diffs[0].diff,
                "devices": [device.model_dump(mode="json") for device in device_diffs],
            },
            "batch_devices": [device.model_dump(mode="json") for device in device_diffs[:5]],
            "parent_workflow_id": "parent-workflow",
            "batch_number": 1,
        }
    )

    assert len(legacy_input.diff_group.devices) == 115
    assert legacy_input.batch_devices is not None
    assert len(legacy_input.batch_devices) == 5
    assert len(legacy_input.resolved_batch_devices()) == 5

    canonical_input = BatchDeployInput(
        diff_group=DiffGroup(
            diff_hash="shared-diff",
            diff_content=str(device_diffs[0].diff),
            devices=device_diffs[:5],
        ),
        parent_workflow_id="parent-workflow",
        batch_number=1,
    )
    serialized = canonical_input.model_dump(mode="json")
    assert canonical_input.resolved_batch_devices() == device_diffs[:5]
    assert len(serialized["diff_group"]["devices"]) == 5
    assert serialized["batch_devices"] is None
    assert len(json.dumps(serialized).encode()) < 250_000


@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=0.0)
def test_parent_stage_serialization_excludes_operational_device_payloads(_):
    """Keep intended configurations out of the parent stages query."""
    workflow_instance = MultiDeployWorkflow()
    device_diffs = _large_device_diffs(115)
    diff_group = DiffGroup(
        diff_hash="shared-diff",
        diff_content=str(device_diffs[0].diff),
        devices=device_diffs,
    )
    batch = device_diffs[:5]

    workflow_instance.set_stage_input(
        "collect_diffs",
        MultiDeployWorkflow.CollectDiffsStageInput(
            devices=[device_diff.device for device_diff in device_diffs]
        ),
    )
    workflow_instance.set_stage_output(
        "collect_diffs",
        MultiDeployWorkflow.CollectDiffsStageOutput(
            device_diffs=device_diffs,
            failed_devices={},
            display="Collected diffs from 115 devices.",
        ),
    )
    workflow_instance.set_stage_input(
        "group_and_batch",
        MultiDeployWorkflow.GroupAndBatchStageInput(
            device_diffs=device_diffs,
            max_batch_size=5,
        ),
    )
    workflow_instance.set_stage_output(
        "group_and_batch",
        MultiDeployWorkflow.GroupAndBatchStageOutput(
            batches=[batch],
            diff_groups=[diff_group],
            display="Created one diff group.",
        ),
    )
    workflow_instance.set_stage_input(
        "execute_batches",
        MultiDeployWorkflow.ExecuteBatchesStageInput(
            batches=[batch],
            diff_groups=[diff_group],
        ),
    )

    serialized_stages = json.dumps(
        [stage.model_dump(mode="json") for stage in workflow_instance.stages()]
    )

    assert device_diffs[0].intended_config not in serialized_stages
    assert len(serialized_stages.encode()) < 50_000


@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=0.0)
def test_child_stage_serialization_excludes_operational_device_payloads(_):
    """Keep intended configurations out of the child stages query."""
    workflow_instance = BatchDeployWorkflow()
    device_diffs = _large_device_diffs(5)
    workflow_instance.set_stage_input(
        "review_shared_diff",
        BatchDeployWorkflow.ReviewDiffStageInput(
            diff_group=DiffGroup(
                diff_hash="shared-diff",
                diff_content=str(device_diffs[0].diff),
                devices=device_diffs,
            ),
            device_count=len(device_diffs),
            batch_devices=device_diffs,
            batch_number=1,
        ),
    )

    serialized_stages = json.dumps(
        [stage.model_dump(mode="json") for stage in workflow_instance.stages()]
    )

    assert device_diffs[0].intended_config not in serialized_stages
    assert len(serialized_stages.encode()) < 50_000


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_multi_deploy_workflow_basic_flow(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test basic multi-deploy workflow flow through initial stages."""
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[MultiDeployWorkflow, BatchDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_network_devices,
            get_ui_base_url,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(10),
    ):
        # Setup mocking - all devices have the same diff
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = (
            "- old config line\n+ new config line"
        )

        workflow_input = MultiDeployInput(
            role="spine", max_batch_size=10
        )  # All in one batch for simplicity

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            MultiDeployWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=5),
        )

        # Wait for discover_devices to complete
        max_wait_time = 10  # seconds
        start_time = 0
        while start_time < max_wait_time:
            stages = await handle.query("stages")
            discover_stage = next((s for s in stages if s["name"] == "discover_devices"), None)
            if discover_stage and discover_stage["state"] == "COMPLETE":
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert discover_stage and discover_stage["state"] == "COMPLETE"
        assert "3 devices with role 'spine'" in discover_stage["output"]["display"]

        # Wait for collect_diffs to complete
        start_time = 0
        while start_time < max_wait_time:
            stages = await handle.query("stages")
            collect_stage = next((s for s in stages if s["name"] == "collect_diffs"), None)
            if collect_stage and collect_stage["state"] == "COMPLETE":
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert collect_stage and collect_stage["state"] == "COMPLETE"
        assert "3 devices have configuration changes" in collect_stage["output"]["display"]

        # Wait for group_and_batch to complete
        start_time = 0
        while start_time < max_wait_time:
            stages = await handle.query("stages")
            group_stage = next((s for s in stages if s["name"] == "group_and_batch"), None)
            if group_stage and group_stage["state"] == "COMPLETE":
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert group_stage and group_stage["state"] == "COMPLETE"
        assert "1 diff groups" in group_stage["output"]["display"]
        assert (
            "1 total batches" in group_stage["output"]["display"]
        )  # 3 devices with max_batch_size=10

        # Wait for the batch child workflow to request approval
        start_time = 0
        while start_time < max_wait_time:
            stages = await handle.query("stages")
            execute_stage = next((s for s in stages if s["name"] == "execute_batches"), None)
            if execute_stage and execute_stage["child_workflows"]:
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert execute_stage and len(execute_stage["child_workflows"]) == 1
        batch_handle = client.get_workflow_handle(execute_stage["child_workflows"][0])

        start_time = 0
        while start_time < max_wait_time:
            batch_stages = await batch_handle.query("stages")
            review_stage = next(
                (s for s in batch_stages if s["name"] == "review_shared_diff"), None
            )
            if review_stage and review_stage["state"] == "PENDING_APPROVAL":
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert review_stage and review_stage["state"] == "PENDING_APPROVAL"
        await batch_handle.signal(
            "approve", {"stage_name": "review_shared_diff", "user": "TestUser"}
        )

        result = await handle.result()
        assert result["successful_devices"] == 3
        assert result["failed_devices"] == 0
        assert result["total_backups"] == 3
        assert result["successful_backups"] == 3
        assert result["failed_backups"] == 0

        stages = await handle.query("stages")
        execute_stage = next(stage for stage in stages if stage["name"] == "execute_batches")
        assert "**Deployment complete**" in execute_stage["output"]["display"]
        assert (
            "**Devices:** 3 configured · 0 failed · 0 rejected"
            in execute_stage["output"]["display"]
        )
        assert "Configured **3/3** · Backups **3/3**" in execute_stage["output"]["display"]


def test_format_batch_status_with_backup_failure():
    """Show partial backup completion compactly and flag it for attention."""
    result = {
        "approved": True,
        "successful_devices": ["spine-01", "spine-02"],
        "failed_devices": {},
        "backups": {"total": 2, "successful": 1, "failed": 1},
    }

    assert _format_batch_status(result, 2) == (
        "⚠️",
        "Configured **2/2** · Backups **1/2**",
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_multi_deploy_workflow_no_devices(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test multi-deploy workflow when no devices are found."""
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    # Mock to return no devices
    @activity.defn(name="get_network_devices")
    async def mock_get_no_devices(
        activity_input: GetNetworkDevicesInput,
    ) -> GetNetworkDevicesOutput:
        return GetNetworkDevicesOutput(devices=[])

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[MultiDeployWorkflow, BatchDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_no_devices,
            load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            persist_config_backup,
            record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(10),
    ):
        workflow_input = MultiDeployInput(role="nonexistent", max_batch_size=2)

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            MultiDeployWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=5),
        )

        result = await handle.result()

        # Verify the result
        assert result["total_devices"] == 0
        assert result["successful_devices"] == 0
        assert result["failed_devices"] == 0
        assert result["rejected_devices"] == 0
        assert result["total_backups"] == 0
        assert result["successful_backups"] == 0
        assert result["failed_backups"] == 0
        assert result["message"] == "No devices found with role 'nonexistent'"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_multi_deploy_workflow_no_diffs(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test multi-deploy workflow when devices have no configuration changes."""
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[MultiDeployWorkflow, BatchDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_network_devices,
            get_ui_base_url,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(10),
    ):
        # Setup mocking - all devices have no diff
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = ""

        workflow_input = MultiDeployInput(role="spine", max_batch_size=2)

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            MultiDeployWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=5),
        )

        result = await handle.result()

        # Verify the result
        assert result["total_devices"] == 3
        assert result["successful_devices"] == 0
        assert result["failed_devices"] == 0
        assert result["rejected_devices"] == 0
        assert result["total_backups"] == 0
        assert result["successful_backups"] == 0
        assert result["failed_backups"] == 0
        assert result["message"] == "No devices have configuration changes to deploy"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_multi_deploy_workflow_grouping_logic(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test multi-deploy workflow grouping and batching logic."""
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[MultiDeployWorkflow, BatchDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_network_devices,
            get_ui_base_url,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(10),
    ):
        # Setup mocking - all devices have the same diff
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = (
            "- old config line\n+ new config line"
        )

        workflow_input = MultiDeployInput(role="spine", max_batch_size=2)  # Test batching

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            MultiDeployWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=5),
        )

        # Wait for group_and_batch stage to complete
        max_wait_time = 10  # seconds
        start_time = 0
        while start_time < max_wait_time:
            stages = await handle.query("stages")
            group_stage = next((s for s in stages if s["name"] == "group_and_batch"), None)
            if group_stage and group_stage["state"] == "COMPLETE":
                break
            await asyncio.sleep(0.5)
            start_time += 0.5

        assert group_stage and group_stage["state"] == "COMPLETE"
        # Verify proper batching: 3 devices with max_batch_size=2 should create 2 batches
        assert "1 diff groups" in group_stage["output"]["display"]
        assert "2 total batches" in group_stage["output"]["display"]

        # Cancel the workflow after verification
        await handle.cancel()


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_batch_deploy_workflow_directly(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test the BatchDeployWorkflow directly."""
    from nv_config_manager.temporal.ngc.activities.nats import publish_nats

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[BatchDeployWorkflow, MockBatchBackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            get_ui_base_url,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(10),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )

        # Create test data
        devices = [
            NetworkDeviceData(
                id="device_1",
                name="spine-01",
                role="spine",
                platform="cumulus-linux",
                device_type="sn4000",
                site="SITEA",
                primary_ip4="10.0.0.1",
                primary_ip6=None,
            ),
            NetworkDeviceData(
                id="device_2",
                name="spine-02",
                role="spine",
                platform="cumulus-linux",
                device_type="sn4000",
                site="SITEA",
                primary_ip4="10.0.0.2",
                primary_ip6=None,
            ),
        ]

        device_diffs = [
            DeviceDiffData(
                device=devices[0],
                diff="- old line\n+ new line",
                intended_config="mock config",
                commit_id="commit123",
            ),
            DeviceDiffData(
                device=devices[1],
                diff="- old line\n+ new line",
                intended_config="mock config",
                commit_id="commit123",
            ),
        ]

        diff_group = DiffGroup(
            diff_hash="abc123",
            diff_content="- old line\n+ new line",
            devices=device_diffs,
        )

        batch_input = BatchDeployInput(
            diff_group=diff_group,
            batch_devices=device_diffs,
            parent_workflow_id="parent123",
        )

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            BatchDeployWorkflow.run,
            batch_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        # Wait for approval stage (with timeout to avoid hanging on insufficient mocks)
        max_wait = 15
        elapsed = 0
        while elapsed < max_wait:
            stages = await handle.query("stages")
            review_stage = next((s for s in stages if s["name"] == "review_shared_diff"), None)
            if review_stage and review_stage["state"] == "PENDING_APPROVAL":
                break
            await asyncio.sleep(0.5)
            elapsed += 0.5
        assert review_stage and review_stage["state"] == "PENDING_APPROVAL", (
            f"Workflow did not reach PENDING_APPROVAL within {max_wait}s; stages={stages}"
        )

        # Approve the diff
        await handle.signal("approve", {"stage_name": "review_shared_diff", "user": "TestUser"})

        # Wait for completion
        result = await handle.result()

        # Verify the result
        assert result["approved"] is True
        assert len(result["successful_devices"]) == 2
        assert len(result["failed_devices"]) == 0
        assert "spine-01" in result["successful_devices"]
        assert "spine-02" in result["successful_devices"]
        assert result["backups"]["total"] == 2
        assert result["backups"]["successful"] == 1
        assert result["backups"]["failed"] == 1
        assert set(result["backups"]["results"]) == {"spine-01", "spine-02"}
        assert result["backups"]["results"]["spine-01"]["success"] is True
        assert result["backups"]["results"]["spine-01"]["changed"] is True
        assert result["backups"]["results"]["spine-02"]["success"] is False
        assert "mock backup failure" in result["backups"]["results"]["spine-02"]["error"]
        assert all(
            backup_result["child_workflow_id"]
            for backup_result in result["backups"]["results"].values()
        )

        stages = await handle.query("stages")
        backups_stage = next(stage for stage in stages if stage["name"] == "perform_backups")
        assert backups_stage["state"] == "COMPLETE"
        assert backups_stage["output"]["successful_backups"] == 1
        assert backups_stage["output"]["failed_backups"] == 1
        backup_display = backups_stage["output"]["display"]
        assert "**Backups complete**" in backup_display
        assert "**Devices:** 1 successful · 1 failed" in backup_display
        assert "**Backup workflows**" in backup_display
        assert "[spine-01 backup]" in backup_display
        assert "[spine-02 backup]" in backup_display
        for backup_result in result["backups"]["results"].values():
            assert backup_result["child_workflow_id"] in backup_display
