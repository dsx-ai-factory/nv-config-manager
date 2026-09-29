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
"""Test NVLinkSwitch Firmware Upgrade Workflow."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.nautobot import (
    CheckRecordedConfigDriftInput,
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeInput,
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager_workflows.activities.backup import (
    PersistConfigBackupInput,
    RecordBackupConfigManagerPluginInput,
)
from nv_config_manager_workflows.activities.deploy import DiffActivityInput
from nv_config_manager_workflows.activities.nvlinkswitch_firmware import (
    CompareRunningDesiredInput,
    CompareRunningDesiredOutput,
    GetRunningFirmwareInput,
    GetRunningFirmwareOutput,
    RebootDeviceInput,
    RebootDeviceOutput,
    UpdateDeviceContextInput,
    ValidateRenderTargetsInput,
    ValidateTargetFilesInput,
)
from nv_config_manager_workflows.activities.os import (
    ExecuteZTPInput,
    ExecuteZTPOutput,
    GetCurrentOSInput,
    GetCurrentOSOutput,
    PollZTPStatusInput,
    PollZTPStatusOutput,
    WaitRebootInput,
    WaitRebootOutput,
)

# Test-specific retry policy and timeout
TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
TEST_TIMEOUT = timedelta(seconds=10)


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id="mock_device_uuid",
            name="test-nvswitch",
            host="192.168.1.100",
            platform="nv-os",
            role="nvlink_switch",
            site="test_site",
            device_type="ConnectX-7",
            primary_ip4="192.168.1.100",
            primary_ip6=None,
            intended_config_path="test/path/startup.yaml",
            render_enabled=True,
            deploy_enabled=True,
            backup_enabled=True,
            ztp_enabled=True,
        )
    )


@activity.defn(name="get_current_os")
async def mock_get_current_os(activity_input: GetCurrentOSInput) -> GetCurrentOSOutput:
    return GetCurrentOSOutput(running_os="25.02.2340")


@activity.defn(name="get_running_firmware")
async def mock_get_running_firmware(
    activity_input: GetRunningFirmwareInput,
) -> GetRunningFirmwareOutput:
    return GetRunningFirmwareOutput(
        running_firmware={
            "asic": "35.2014.1748",
            "bios": "0ACTV_00.01.015",
            "bmc": "88.0002.1138",
            "cpld1": "CPLD000370_REV0500",
        }
    )


# Global variable to track compare_running_desired calls for success test
_compare_calls_count = 0


@activity.defn(name="compare_running_desired")
async def mock_compare_running_desired(
    activity_input: CompareRunningDesiredInput,
) -> CompareRunningDesiredOutput:
    global _compare_calls_count
    _compare_calls_count += 1

    # First call: upgrade needed
    # Second call (post-upgrade validation): upgrade successful
    if _compare_calls_count == 1:
        return CompareRunningDesiredOutput(
            upgrade_needed=True,
            desired_os="25.02.2344",
            desired_firmware={
                "asic": "35.2014.1750",
                "bios": "0ACTV_00.01.017",
                "bmc": "88.0002.1140",
                "cpld": "CPLD000370_REV0600",
            },
            differences={
                "asic": {"actual": "35.2014.1748", "expected": "35.2014.1750"},
                "bios": {"actual": "0ACTV_00.01.015", "expected": "0ACTV_00.01.017"},
                "bmc": {"actual": "88.0002.1138", "expected": "88.0002.1140"},
                "cpld": {"actual": "CPLD000370_REV0500", "expected": "CPLD000370_REV0600"},
            },
        )
    else:
        # Post-upgrade: no differences, upgrade successful
        return CompareRunningDesiredOutput(
            upgrade_needed=False,
            desired_os="25.02.2344",
            desired_firmware={
                "asic": "35.2014.1750",
                "bios": "0ACTV_00.01.017",
                "bmc": "88.0002.1140",
                "cpld": "CPLD000370_REV0600",
            },
            differences={},
        )


@activity.defn(name="compare_running_desired")
async def mock_compare_running_desired_no_upgrade(
    activity_input: CompareRunningDesiredInput,
) -> CompareRunningDesiredOutput:
    # Simulate no upgrade needed scenario
    return CompareRunningDesiredOutput(
        upgrade_needed=False,
        desired_os="25.02.2340",
        desired_firmware={
            "asic": "35.2014.1748",
            "bios": "0ACTV_00.01.015",
            "bmc": "88.0002.1138",
            "cpld": "CPLD000370_REV0500",
        },
        differences={},
    )


@activity.defn(name="update_device_context")
async def mock_update_device_context(activity_input: UpdateDeviceContextInput) -> None:
    pass


@activity.defn(name="validate_render_targets")
async def mock_validate_render_targets(activity_input: ValidateRenderTargetsInput) -> None:
    pass


@activity.defn(name="validate_target_files")
async def mock_validate_target_files(activity_input: ValidateTargetFilesInput) -> None:
    pass


@activity.defn(name="execute_ztp")
async def mock_execute_ztp(activity_input: ExecuteZTPInput) -> ExecuteZTPOutput:
    return ExecuteZTPOutput(start_time="2024-01-01T12:00:00")


@activity.defn(name="poll_ztp_status")
async def mock_poll_ztp_status(activity_input: PollZTPStatusInput) -> PollZTPStatusOutput:
    # The activity now accepts timeout_minutes and ztp_execution_timestamp parameters
    # but we just return success for tests
    return PollZTPStatusOutput(success=True)


@activity.defn(name="reboot_device")
async def mock_reboot_device(activity_input: RebootDeviceInput) -> RebootDeviceOutput:
    return RebootDeviceOutput(start_time="2024-01-01T12:00:00")


@activity.defn(name="wait_reboot")
async def mock_wait_reboot(activity_input: WaitRebootInput) -> WaitRebootOutput:
    return WaitRebootOutput(success=True)


@activity.defn(name="wait_reboot")
async def mock_wait_reboot_failure(activity_input: WaitRebootInput) -> WaitRebootOutput:
    return WaitRebootOutput(success=False)


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(activity_input: PersistConfigBackupInput) -> None:
    pass


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(
    activity_input: RecordBackupConfigManagerPluginInput,
) -> None:
    pass


@activity.defn(name="check_recorded_config_drift")
async def mock_check_recorded_config_drift(activity_input: CheckRecordedConfigDriftInput) -> bool:
    return False


@activity.defn(name="load_running_configuration")
async def mock_load_running_configuration(device_data: NetworkDeviceData) -> str:
    return "mock running config"


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(device_data: NetworkDeviceData) -> tuple[str, str, str]:
    return (
        "mock intended config",
        "mock_intended_commit_id",
        "https://gitlab.example.com/mock/path",
    )


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff(activity_input: DiffActivityInput) -> str:
    return ""


@activity.defn(name="publish_nats")
async def mock_publish_nats(activity_input: PublishNatsInput) -> None:
    pass


@pytest.mark.asyncio
async def test_nvlinkswitch_firmware_upgrade_workflow_success(env):
    """Test successful NVLinkSwitch firmware upgrade workflow."""
    # Reset the global counter for this test
    global _compare_calls_count
    _compare_calls_count = 0

    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[NVLinkSwitchFirmwareUpgradeWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_current_os,
            mock_get_running_firmware,
            mock_compare_running_desired,
            mock_update_device_context,
            mock_validate_render_targets,
            mock_validate_target_files,
            mock_execute_ztp,
            mock_poll_ztp_status,
            mock_reboot_device,
            mock_wait_reboot,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_check_recorded_config_drift,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = NVLinkSwitchFirmwareUpgradeInput(
            device_id="mock_device_uuid", bundle_version="1.2.2"
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            NVLinkSwitchFirmwareUpgradeWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result is True  # Workflow should succeed

        # Verify all stages completed successfully
        stages = await handle.query("stages")

        # Verify get_current_state stage
        get_state_stage = next(s for s in stages if s["name"] == "get_current_state")
        assert get_state_stage["state"] == "COMPLETE"
        assert "## Current Device State" in get_state_stage["output"]["display"]

        # Verify compare_versions stage
        compare_stage = next(s for s in stages if s["name"] == "compare_versions")
        assert compare_stage["state"] == "COMPLETE"
        assert "## 🔄 Firmware Upgrade Required" in compare_stage["output"]["display"]

        # Verify perform_backup stage
        backup_stage = next(s for s in stages if s["name"] == "perform_backup")
        assert backup_stage["state"] == "COMPLETE"

        # Verify update_context_and_validate stage
        update_stage = next(s for s in stages if s["name"] == "update_context_and_validate")
        assert update_stage["state"] == "COMPLETE"
        assert "## ✅ Device Context Updated & Validated" in update_stage["output"]["display"]

        # Verify execute_firmware_upgrade stage
        execute_stage = next(s for s in stages if s["name"] == "execute_firmware_upgrade")
        assert execute_stage["state"] == "COMPLETE"
        assert "## ✅ Firmware Upgrade Execution Complete" in execute_stage["output"]["display"]

        # Verify validate_firmware_upgrade stage
        validate_stage = next(s for s in stages if s["name"] == "validate_firmware_upgrade")
        assert validate_stage["state"] == "COMPLETE"


@pytest.mark.asyncio
async def test_nvlinkswitch_firmware_upgrade_workflow_no_upgrade_needed(env):
    """Test NVLinkSwitch firmware upgrade workflow when no upgrade is needed."""
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[NVLinkSwitchFirmwareUpgradeWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_current_os,
            mock_get_running_firmware,
            mock_compare_running_desired_no_upgrade,  # Use no upgrade version for this test
            mock_update_device_context,
            mock_validate_render_targets,
            mock_validate_target_files,
            mock_execute_ztp,
            mock_poll_ztp_status,
            mock_reboot_device,
            mock_wait_reboot,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_check_recorded_config_drift,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = NVLinkSwitchFirmwareUpgradeInput(
            device_id="mock_device_uuid", bundle_version="1.2.0"
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            NVLinkSwitchFirmwareUpgradeWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result is False  # Workflow should return False (no upgrade needed)

        # Verify stages
        stages = await handle.query("stages")

        # Verify get_current_state stage completed
        get_state_stage = next(s for s in stages if s["name"] == "get_current_state")
        assert get_state_stage["state"] == "COMPLETE"

        # Verify compare_versions stage completed
        compare_stage = next(s for s in stages if s["name"] == "compare_versions")
        assert compare_stage["state"] == "COMPLETE"
        assert "## ✅ No Upgrade Required" in compare_stage["output"]["display"]

        # Verify remaining stages are unreachable
        backup_stage = next(s for s in stages if s["name"] == "perform_backup")
        assert backup_stage["state"] == "UNREACHABLE"

        update_stage = next(s for s in stages if s["name"] == "update_context_and_validate")
        assert update_stage["state"] == "UNREACHABLE"

        execute_stage = next(s for s in stages if s["name"] == "execute_firmware_upgrade")
        assert execute_stage["state"] == "UNREACHABLE"

        validate_stage = next(s for s in stages if s["name"] == "validate_firmware_upgrade")
        assert validate_stage["state"] == "UNREACHABLE"


@activity.defn(name="get_network_device")
async def mock_get_network_device_unsupported(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id="mock_device_uuid",
            name="test-switch",
            host="192.168.1.100",
            platform="cumulus-linux",  # Unsupported platform
            role="switch",
            site="test_site",
            device_type="switch",
            primary_ip4="192.168.1.100",
            primary_ip6=None,
            intended_config_path="test/path/startup.yaml",
        )
    )


@pytest.mark.asyncio
async def test_nvlinkswitch_firmware_upgrade_workflow_unsupported_platform(env):
    """Test NVLinkSwitch firmware upgrade workflow with unsupported platform."""
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[NVLinkSwitchFirmwareUpgradeWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device_unsupported,  # Use unsupported platform version
            mock_get_current_os,
            mock_get_running_firmware,
            mock_compare_running_desired,
            mock_update_device_context,
            mock_validate_render_targets,
            mock_validate_target_files,
            mock_execute_ztp,
            mock_poll_ztp_status,
            mock_reboot_device,
            mock_wait_reboot,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_check_recorded_config_drift,
            mock_load_running_configuration,
            mock_load_intended_configuration,
            mock_perform_candidate_diff,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(100),
    ):
        input = NVLinkSwitchFirmwareUpgradeInput(
            device_id="mock_device_uuid", bundle_version="1.2.2"
        )
        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            NVLinkSwitchFirmwareUpgradeWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result is False  # Workflow should return False (unsupported)

        # Verify stages
        stages = await handle.query("stages")

        # Verify get_current_state stage completed with unsupported message
        get_state_stage = next(s for s in stages if s["name"] == "get_current_state")
        assert get_state_stage["state"] == "COMPLETE"
        assert "not supported for this platform" in get_state_stage["output"]["display"]

        # Verify all other stages are unreachable
        compare_stage = next(s for s in stages if s["name"] == "compare_versions")
        assert compare_stage["state"] == "UNREACHABLE"

        backup_stage = next(s for s in stages if s["name"] == "perform_backup")
        assert backup_stage["state"] == "UNREACHABLE"
