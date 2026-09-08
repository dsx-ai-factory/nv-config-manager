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
"""Test Switch OS Upgrade Workflow."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import sleep
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.ngc.activities.backup import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
)
from nv_config_manager.temporal.ngc.activities.deploy import DiffActivityInput
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.nautobot import (
    CheckRecordedConfigDriftInput,
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.activities.os import (
    ExecuteZTPInput,
    GetOSImageVersionsInput,
    GetOSImageVersionsOutput,
    PollImageInput,
    PollImageOutput,
    PollZTPStatusInput,
    PollZTPStatusOutput,
    UpdateIntendedOSImageInput,
)
from nv_config_manager.temporal.ngc.activities.render import ValidateRenderedImageChangeInput
from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.os_upgrade import (
    SUPPORTED_PLATFORMS,
    SwitchOSUpgradeInput,
    SwitchOSUpgradeWorkflow,
)

# Test-specific retry policy and timeout
TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
TEST_TIMEOUT = timedelta(seconds=10)


def test_supported_platforms_include_juniper_junos():
    """Switch OS Upgrade accepts Juniper Junos in addition to Cumulus Linux."""
    assert Platform.CUMULUS_LINUX in SUPPORTED_PLATFORMS
    assert Platform.JUNIPER_JUNOS in SUPPORTED_PLATFORMS


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
        )
    )


@activity.defn(name="get_os_image_versions")
async def mock_get_os_image_versions(
    activity_input: GetOSImageVersionsInput,
) -> GetOSImageVersionsOutput:
    return GetOSImageVersionsOutput(
        intended_firmware="5.0.0",
        desired_firmware="5.1.0",
        ztp_ipv4_address="192.168.1.100",
    )


@activity.defn(name="update_intended_os_image")
async def mock_update_intended_os_image(
    activity_input: UpdateIntendedOSImageInput,
) -> None:
    return None


@activity.defn(name="validate_rendered_image_change")
async def mock_validate_rendered_image_change(
    activity_input: ValidateRenderedImageChangeInput,
) -> bool:
    return True


@activity.defn(name="execute_ztp")
async def mock_execute_ztp(
    activity_input: ExecuteZTPInput,
) -> None:
    return None


@activity.defn(name="poll_image")
async def mock_poll_image(
    activity_input: PollImageInput,
) -> PollImageOutput:
    return PollImageOutput(running_image="5.1.0")


@activity.defn(name="poll_ztp_status")
async def mock_poll_ztp_status(
    activity_input: PollZTPStatusInput,
) -> PollZTPStatusOutput:
    return PollZTPStatusOutput(success=True)


@activity.defn(name="load_running_configuration")
async def mock_load_running_configuration(
    device_data: NetworkDeviceData,
) -> str:
    return "mock running config"


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(
    activity_input: PersistConfigBackupInput,
) -> str:
    return "mock_commit_id"


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(
    activity_input: RecordBackupConfigManagerPluginInput,
) -> tuple[bool, str]:
    return True, "Persisted new backup configuration"


@activity.defn(name="publish_nats")
async def mock_publish_nats(activity_input: PublishNatsInput) -> None:
    """Mock publish nats activity."""
    return None


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(device_data: NetworkDeviceData) -> tuple[str, str, str]:
    """Mock load intended configuration activity."""
    return (
        "mock intended config",
        "mock_intended_commit_id",
        "https://gitlab.example.com/example-user/intended-network-configs/-/blob/mock_intended_commit_id/SITEA/MOCK_DEVICE/startup.yaml",
    )


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff(activity_input: DiffActivityInput) -> str:
    """Mock perform candidate diff activity."""
    return ""


@activity.defn(name="check_recorded_config_drift")
async def mock_check_recorded_config_drift(activity_input: CheckRecordedConfigDriftInput) -> bool:
    """Mock check recorded config drift activity."""
    return False


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.os_upgrade.DEFAULT_ACTIVITY_RETRY_POLICY",
    TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.os_upgrade.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow(
    mock_timedelta,
    mock_time,
    env,
):
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[SwitchOSUpgradeWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_os_image_versions,
            mock_update_intended_os_image,
            mock_validate_rendered_image_change,
            mock_execute_ztp,
            mock_poll_image,
            mock_poll_ztp_status,
            mock_load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_check_recorded_config_drift,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = SwitchOSUpgradeInput(device_id="mock_device_uuid")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            SwitchOSUpgradeWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        # Wait for workflow to reach approval stage
        while await handle.query("pending_approval") is False:
            sleep(1)

        # Verify pre-approval stages
        stages = await handle.query("stages")
        assert (
            len(stages) == 4
        )  # approve_upgrade, update_device_configuration, perform_backup, perform_upgrade

        # Verify approve_upgrade stage is pending approval
        approve_stage = next(s for s in stages if s["name"] == "approve_upgrade")
        assert approve_stage["state"] == "PENDING_APPROVAL"
        assert approve_stage["output"]["current_firmware"] == "5.0.0"
        assert approve_stage["output"]["desired_firmware"] == "5.1.0"
        assert approve_stage["output"]["diff"] == "Upgrade from 5.0.0 to 5.1.0"
        assert approve_stage["output"]["supported"] is True

        # Send approval signal
        await handle.signal("approve", {"stage_name": "approve_upgrade", "user": "Test"})

        result = await handle.result()
        assert result

        # Verify all stages
        stages = await handle.query("stages")

        # Verify approve_upgrade stage after approval
        approve_stage = next(s for s in stages if s["name"] == "approve_upgrade")
        assert approve_stage["state"] == "COMPLETE"
        assert approve_stage["output"]["current_firmware"] == "5.0.0"
        assert approve_stage["output"]["desired_firmware"] == "5.1.0"
        assert approve_stage["output"]["diff"] == "Upgrade from 5.0.0 to 5.1.0"
        assert approve_stage["output"]["supported"] is True

        # Verify update_device_configuration stage
        update_stage = next(s for s in stages if s["name"] == "update_device_configuration")
        assert update_stage["state"] == "COMPLETE"
        assert (
            "Successfully updated intended os image to 5.1.0" in update_stage["output"]["display"]
        )
        assert "Validated image change for device mock_device" in update_stage["output"]["display"]

        # Verify perform_backup stage
        backup_stage = next(s for s in stages if s["name"] == "perform_backup")
        assert backup_stage["state"] == "COMPLETE"
        assert "Configuration backup completed via workflow" in backup_stage["output"]["display"]
        assert "Configuration Drift Detected" not in backup_stage["output"]["display"]

        # Verify perform_upgrade stage
        upgrade_stage = next(s for s in stages if s["name"] == "perform_upgrade")
        assert upgrade_stage["state"] == "COMPLETE"
        assert "Successfully upgraded device to 5.1.0" in upgrade_stage["output"]["display"]
        assert "ZTP completed successfully" in upgrade_stage["output"]["display"]


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.os_upgrade.DEFAULT_ACTIVITY_RETRY_POLICY",
    TEST_RETRY_POLICY,
)
@patch("nv_config_manager.temporal.ngc.workflows.os_upgrade.timedelta", return_value=TEST_TIMEOUT)
async def test_execute_workflow_with_config_drift(
    mock_timedelta,
    mock_time,
    env,
):
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    @activity.defn(name="check_recorded_config_drift")
    async def mock_check_recorded_config_drift_with_drift(
        activity_input: CheckRecordedConfigDriftInput,
    ) -> bool:
        """Mock check recorded config drift activity with drift."""
        return True

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[SwitchOSUpgradeWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_os_image_versions,
            mock_update_intended_os_image,
            mock_validate_rendered_image_change,
            mock_execute_ztp,
            mock_poll_image,
            mock_poll_ztp_status,
            mock_load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_publish_nats,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_check_recorded_config_drift_with_drift,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = SwitchOSUpgradeInput(device_id="mock_device_uuid")
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            SwitchOSUpgradeWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        # Wait for workflow to reach approval stage
        while await handle.query("pending_approval") is False:
            sleep(1)

        # Send approval signal
        await handle.signal("approve", {"stage_name": "approve_upgrade", "user": "Test"})

        result = await handle.result()
        assert not result  # Workflow should fail due to config drift

        # Verify all stages
        stages = await handle.query("stages")

        # Verify perform_backup stage
        backup_stage = next(s for s in stages if s["name"] == "perform_backup")
        assert backup_stage["state"] == "COMPLETE"
        assert "Configuration backup completed via workflow" in backup_stage["output"]["display"]
        assert "Configuration Drift Detected, Halting Workflow" in backup_stage["output"]["display"]

        # Verify update_device_configuration and perform_upgrade stages are unreachable
        update_stage = next(s for s in stages if s["name"] == "update_device_configuration")
        assert update_stage["state"] == "UNREACHABLE"

        upgrade_stage = next(s for s in stages if s["name"] == "perform_upgrade")
        assert upgrade_stage["state"] == "UNREACHABLE"
