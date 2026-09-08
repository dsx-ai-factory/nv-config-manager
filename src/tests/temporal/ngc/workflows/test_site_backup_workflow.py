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
# pylint: disable=B101,C0115,C0116
"""Test Suite for Site Backup Workflow."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from temporalio import activity, workflow
from temporalio.client import WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.backup import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
)
from nv_config_manager.temporal.ngc.activities.deploy import DiffActivityInput
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    GetNetworkDevicesInput,
    GetNetworkDevicesOutput,
)
from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from tests.temporal.conftest import mock_send_slack_message

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.ngc.workflows.site_backup import (
        BackupResultData,
        SiteBackupInput,
        SiteBackupWorkflow,
    )

TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
TEST_TIMEOUT = timedelta(seconds=10)

TEST_DEVICES = [
    NetworkDeviceData(
        id="device-1-uuid",
        name="site-tor-001",
        role="tan-leaf",
        platform="cumulus-linux",
        site="demo-site",
        device_type="sn2010",
        primary_ip4="10.1.1.1",
        primary_ip6=None,
        rack="a01",
        position=1,
        render_enabled=True,
        deploy_enabled=True,
        backup_enabled=True,
        ztp_enabled=False,
        intent=None,
    ),
    NetworkDeviceData(
        id="device-2-uuid",
        name="site-tor-002",
        role="tan-leaf",
        platform="cumulus-linux",
        site="demo-site",
        device_type="sn2010",
        primary_ip4="10.1.1.2",
        primary_ip6=None,
        rack="a01",
        position=2,
        render_enabled=True,
        deploy_enabled=True,
        backup_enabled=True,
        ztp_enabled=False,
        intent=None,
    ),
]


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(devices=TEST_DEVICES)


@activity.defn(name="get_network_devices")
async def mock_get_network_devices_empty(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(devices=[])


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    device = next(device for device in TEST_DEVICES if device.id == activity_input.device_id)
    return GetNetworkDeviceOutput(device=device)


@activity.defn(name="load_running_configuration")
async def mock_load_running_configuration(device_data: NetworkDeviceData) -> str:
    return "mock config"


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(
    device_data: NetworkDeviceData,
) -> tuple[str, str, str]:
    return (
        "mock intended config",
        "mock_intended_commit_id",
        "https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_intended_commit_id",
    )


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff(activity_input: DiffActivityInput) -> str:
    return ""


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(activity_input: PersistConfigBackupInput) -> str:
    return "mock_commit_id"


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(
    activity_input: RecordBackupConfigManagerPluginInput,
) -> tuple[bool, str]:
    return (
        False,
        "Backup unchanged.",
    )


@activity.defn(name="publish_nats")
async def mock_publish_nats(activity_input: PublishNatsInput) -> None:
    return None


@activity.defn(name="get_ui_base_url")
async def mock_get_ui_base_url() -> str:
    return "https://config-manager.example.com"


@activity.defn(name="get_ui_base_url")
async def mock_get_ui_base_url_unavailable() -> str:
    raise RuntimeError("UI base URL unavailable")


class TestSiteBackupInput:
    """Tests for SiteBackupInput validation."""

    def test_site_must_not_be_empty(self):
        """Reject an empty site before starting the workflow."""
        with pytest.raises(ValidationError):
            SiteBackupInput(site="", user="demo-user")

    def test_backup_enabled_only_defaults_to_true(self):
        """Default to backup-enabled devices only."""
        workflow_input = SiteBackupInput(site="demo-site", user="demo-user")
        assert workflow_input.backup_enabled_only is True


class TestBackupResultData:
    """Tests for BackupResultData model."""

    def test_backup_result_data_success(self):
        """Test successful backup result."""
        device = TEST_DEVICES[0]
        result = BackupResultData(
            device=device,
            success=True,
            changed=False,
            child_workflow_id="workflow-123",
        )
        assert result.device.name == "site-tor-001"
        assert result.success is True
        assert result.changed is False
        assert result.error is None

    def test_backup_result_data_failure(self):
        """Test failed backup result."""
        device = TEST_DEVICES[0]
        result = BackupResultData(
            device=device,
            success=False,
            error="backup failed",
            child_workflow_id="workflow-456",
        )
        assert result.success is False
        assert result.error == "backup failed"


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.site_backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    new=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.site_backup.timedelta", return_value=TEST_TIMEOUT)
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    new=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_site_backup_workflow(
    mock_backup_timedelta,
    mock_site_timedelta,
    mock_time,
    env,
):
    """Run a site backup across multiple devices via child backup workflows."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteBackupWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_devices,
            mock_get_network_device,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_get_ui_base_url,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        workflow_input = SiteBackupInput(
            site="demo-site",
            user="demo-user",
            user_domain="nvidia.com",
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            SiteBackupWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert "Site backup results for demo-site" in result
        assert "site-tor-001" in result
        assert "site-tor-002" in result
        assert "**Total devices:** 2" in result
        assert "**Successful:** 2" in result
        assert "**Failed:** 0" in result


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.site_backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    new=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.site_backup.timedelta", return_value=TEST_TIMEOUT)
@patch(
    "nv_config_manager.temporal.ngc.workflows.backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    new=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_site_backup_workflow_without_ui_base_url(
    mock_backup_timedelta,
    mock_site_timedelta,
    mock_time,
    env,
):
    """Return the summary without workflow links when UI base URL lookup fails."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteBackupWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_devices,
            mock_get_network_device,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_get_ui_base_url_unavailable,
            mock_send_slack_message,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        workflow_input = SiteBackupInput(
            site="demo-site",
            user="demo-user",
            user_domain="nvidia.com",
        )
        handle: WorkflowHandle = await env.client.start_workflow(
            SiteBackupWorkflow.run,
            workflow_input,
            id=str(uuid.uuid4()),
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert "Site backup results for demo-site" in result
        assert "site-tor-001" in result
        assert "config-manager.example.com/workflows/" not in result
        assert "workflow `" in result


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.site_backup.DEFAULT_ACTIVITY_RETRY_POLICY",
    new=TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.site_backup.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_site_backup_workflow_no_devices(
    mock_site_timedelta,
    mock_time,
    env,
):
    """Short-circuit when no devices match the site backup filters."""
    task_queue_name = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[SiteBackupWorkflow],
        activities=[mock_get_network_devices_empty, mock_get_ui_base_url, mock_publish_nats],
        activity_executor=ThreadPoolExecutor(100),
    ):
        workflow_input = SiteBackupInput(site="empty-site", user="demo-user")
        handle: WorkflowHandle = await env.client.start_workflow(
            SiteBackupWorkflow.run,
            workflow_input,
            id=str(uuid.uuid4()),
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()

        assert "No devices found matching the specified filters" in result
        stages = await handle.query("stages")
        perform_backups_stage = next(
            stage for stage in stages if stage["name"] == "perform_backups"
        )
        format_result_stage = next(stage for stage in stages if stage["name"] == "format_result")
        assert perform_backups_stage["state"] == "UNREACHABLE"
        assert format_result_stage["state"] == "UNREACHABLE"
