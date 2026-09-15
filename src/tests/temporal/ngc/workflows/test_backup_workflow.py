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
"""Test Backup Workflow."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.backup import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
)
from nv_config_manager.temporal.ngc.activities.deploy import (
    DiffActivityInput,
)
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.workflows.backup import BackupInput, BackupWorkflow, TriggerEnum
from tests.temporal.conftest import mock_send_slack_message

# Test-specific retry policy and timeout
TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
TEST_TIMEOUT = timedelta(seconds=10)


def test_backup_input_optional_metadata_defaults_to_none() -> None:
    workflow_input = BackupInput(device_id="device-id", trigger=TriggerEnum.API)

    assert workflow_input.user is None
    assert workflow_input.user_domain is None
    assert workflow_input.workflow_id is None
    assert workflow_input.intended_config_commit_id is None
    assert workflow_input.suppress_drift_notification is False
    assert workflow_input.terminate_on_failure is False
    assert BackupInput.model_json_schema()["required"] == ["device_id", "trigger"]


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="mock_device",
            role="mock_role",
            platform="cumulus-linux",
            site="SITEA",
            device_type="sn4200",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
            rack="a01",
            position=1,
            render_enabled=False,
            deploy_enabled=False,
            backup_enabled=False,
            ztp_enabled=False,
            intent=None,
        )
    )


@activity.defn(name="load_running_configuration")
async def mock_load_running_configuration(device_data: NetworkDeviceData) -> str:
    """Mock load running configuration activity."""
    return "mock config"


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(
    device_data: NetworkDeviceData,
) -> tuple[str, str, str]:
    """Mock load intended configuration activity."""
    return (
        "mock intended config",
        "mock_intended_commit_id",
        "https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_intended_commit_id",
    )


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff(activity_input: DiffActivityInput) -> str:
    """Mock perform candidate diff activity."""
    return "mock_diff"


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff_no_drift(
    activity_input: DiffActivityInput,
) -> str:
    """Mock perform candidate diff activity."""
    return ""


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff_with_secret(
    activity_input: DiffActivityInput,
) -> str:
    """Mock perform candidate diff activity returning a Junos secret value."""
    return '+   authentication-key "$9$AbCdEfGhIjKlMnOp"; ## SECRET-DATA'


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(activity_input: PersistConfigBackupInput) -> str:
    """Mock persist config backup activity."""
    return "mock_commit_id"


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(
    activity_input: RecordBackupConfigManagerPluginInput,
) -> tuple[bool, str]:
    """Mock record backup nv-config-manager plugin activity."""
    return (
        True,
        "Persisted new backup configuration:\n\n[Configuration Backup](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=backup)\n[Latest Commit](https://config-manager.example.com/commits/mock_commit_id)\n",
    )


@activity.defn(name="publish_nats")
async def mock_publish_nats(activity_input: PublishNatsInput) -> None:
    """Mock publish nats activity."""
    return None


@pytest.mark.asyncio
async def test_check_drift_suppresses_slack_notification() -> None:
    device = await mock_get_network_device(GetNetworkDeviceInput(device_id="mock_device_uuid"))
    with patch(
        "nv_config_manager_workflows.stage.mixin.workflow.time",
        return_value=0.0,
    ):
        workflow_instance = BackupWorkflow()
    execute_activity = AsyncMock(
        side_effect=[
            device,
            (
                "mock intended config",
                "mock_intended_commit_id",
                "https://config-manager.example.com/device/mock_device_uuid/startup.yaml",
            ),
            "mock_diff",
        ]
    )

    with patch(
        "nv_config_manager.temporal.ngc.workflows.backup.workflow.execute_activity",
        new=execute_activity,
    ):
        output = await BackupWorkflow.check_drift.__wrapped__(  # type: ignore[attr-defined]
            workflow_instance,
            BackupWorkflow.CheckDriftStageInput(
                device_id="mock_device_uuid",
                intended_config_commit_id=None,
                suppress_drift_notification=True,
            ),
        )

    assert output.has_drift is True
    assert [call.args[0].__name__ for call in execute_activity.await_args_list] == [
        "get_network_device",
        "load_intended_configuration",
        "perform_candidate_diff",
    ]


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow(
    mock_timedelta,
    mock_retry_policy,
    mock_time,
    env,
):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = BackupInput(
            device_id="mock_device_uuid",
            trigger=TriggerEnum.API,
            user="test_user",
            user_domain="nvidia.com",
            workflow_id=None,
            intended_config_commit_id=None,
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            BackupWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert result
        history = await handle.fetch_history()
        scheduled_activity_types = {
            event.activity_task_scheduled_event_attributes.activity_type.name
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
        }
        assert "send_slack_message" in scheduled_activity_types
        assert await handle.query("stages") == [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the running configuration from the device.",
                "execution_time": 0.0,
                "input": {"device_id": "mock_device_uuid"},
                "name": "load_running_configuration",
                "output": {
                    "device_data": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_uuid/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": 1,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": "a01",
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "```\nmock config\n```",
                    "running_config": "mock config",
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
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Check for configuration drift.",
                "execution_time": 0.0,
                "input": {
                    "device_id": "mock_device_uuid",
                    "intended_config_commit_id": None,
                    "suppress_drift_notification": False,
                },
                "name": "check_drift",
                "output": {
                    "commit_id": "mock_intended_commit_id",
                    "diff": "mock_diff",
                    "display": "Loaded intended configuration from [mock_device_uuid/startup.yaml](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_intended_commit_id).\nConfiguration Drift Detected:\n```\nmock_diff\n```",
                    "has_drift": True,
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
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["load_running_configuration", "check_drift"],
                "description": "Persist Running Configuration to the Config Store and NVIDIA Config Manager plugin.",
                "execution_time": 0.0,
                "input": {
                    "device_data": {
                        "backup_enabled": False,
                        "backup_path": "mock_device_uuid/startup.yaml",
                        "backup_file": "startup.yaml",
                        "intended_config_file": "startup.yaml",
                        "deploy_enabled": False,
                        "device_type": "sn4200",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": 1,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": "a01",
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "intended_config_commit_id": None,
                    "running_config": "mock config",
                    "trigger": "API",
                    "user": "test_user",
                    "user_domain": "nvidia.com",
                    "workflow_id": None,
                },
                "name": "persist_backup",
                "output": {
                    "changed": True,
                    "display": "Persisted new backup configuration:\n\n[Configuration Backup](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=backup)\n[Latest Commit](https://config-manager.example.com/commits/mock_commit_id)\n",
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
            },
        ]

        expected_search_attributes = {
            "DeviceID": ["mock_device_uuid"],
            "DeviceRole": ["mock_role"],
            "Site": ["SITEA"],
            "DeviceName": ["mock_device"],
            "DevicePlatform": ["cumulus-linux"],
        }
        desc = await handle.describe()
        search_attrs = desc.search_attributes
        for attr, val in expected_search_attributes.items():
            assert search_attrs[attr] == val


REDACTED_RUNNING_CONFIG = 'system {\n    authentication-key "$9$<redacted>"; ## SECRET-DATA\n}\n'


@activity.defn(name="load_running_configuration")
async def mock_load_running_configuration_with_secret(device_data: NetworkDeviceData) -> str:
    """Mock load_running_configuration, returning output as if already redacted."""
    return REDACTED_RUNNING_CONFIG


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow_persists_and_displays_already_redacted_running_config(
    mock_timedelta,
    mock_retry_policy,
    mock_time,
    env,
):
    """load_running_config passes the (already-redacted) activity result through unchanged."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_running_configuration_with_secret,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = BackupInput(
            device_id="mock_device_uuid",
            trigger=TriggerEnum.API,
            user="test_user",
            user_domain="nvidia.com",
            workflow_id=None,
            intended_config_commit_id=None,
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            BackupWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        assert await handle.result()
        stages = await handle.query("stages")

        load_config_stage = next(
            stage for stage in stages if stage["name"] == "load_running_configuration"
        )
        assert load_config_stage["output"]["running_config"] == REDACTED_RUNNING_CONFIG
        assert load_config_stage["output"]["display"] == f"```\n{REDACTED_RUNNING_CONFIG}\n```"

        persist_stage = next(stage for stage in stages if stage["name"] == "persist_backup")
        assert persist_stage["input"]["running_config"] == REDACTED_RUNNING_CONFIG


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow_redacts_secrets_in_drift_diff(
    mock_timedelta,
    mock_retry_policy,
    mock_time,
    env,
):
    """check_drift redacts Junos secrets from both the diff field and its display."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff_with_secret,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = BackupInput(
            device_id="mock_device_uuid",
            trigger=TriggerEnum.API,
            user="test_user",
            user_domain="nvidia.com",
            workflow_id=None,
            intended_config_commit_id=None,
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            BackupWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        assert await handle.result()
        stages = await handle.query("stages")
        check_drift_stage = next(stage for stage in stages if stage["name"] == "check_drift")

        assert "$9$AbCdEfGhIjKlMnOp" not in check_drift_stage["output"]["diff"]
        assert '"$9$<redacted>"' in check_drift_stage["output"]["diff"]
        assert "$9$AbCdEfGhIjKlMnOp" not in check_drift_stage["output"]["display"]
        assert '"$9$<redacted>"' in check_drift_stage["output"]["display"]


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow_no_drift(
    mock_timedelta,
    mock_retry_policy,
    mock_time,
    env,
):
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff_no_drift,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = BackupInput(
            device_id="mock_device_uuid",
            trigger=TriggerEnum.API,
            user="test_user",
            user_domain="nvidia.com",
            workflow_id=None,
            intended_config_commit_id=None,
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            BackupWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert result
        stages = await handle.query("stages")
        check_drift_stage = next(stage for stage in stages if stage["name"] == "check_drift")
        assert check_drift_stage["output"]["has_drift"] is False
        assert check_drift_stage["output"]["diff"] == ""
        assert "No drift detected" in check_drift_stage["output"]["display"]

        persist_backup_stage = next(stage for stage in stages if stage["name"] == "persist_backup")
        assert (
            persist_backup_stage["input"]["intended_config_commit_id"] == "mock_intended_commit_id"
        )
