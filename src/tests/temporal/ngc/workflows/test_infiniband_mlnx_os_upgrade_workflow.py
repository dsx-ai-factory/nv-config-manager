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
"""Test Infiniband Mellanox OS Upgrade Workflow."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.activities.os import (
    CleanupMlnxOSInput,
    CleanupMlnxOSOutput,
    DownloadMlnxOSInput,
    DownloadMlnxOSOutput,
    GetMlnxOSVersionInput,
    GetMlnxOSVersionOutput,
    GetOSImageVersionsInput,
    GetOSImageVersionsOutput,
    InstallMlnxOSInput,
    InstallMlnxOSOutput,
    ReloadMlnxOSInput,
    ReloadMlnxOSOutput,
    UpdateIntendedOSImageInput,
)
from nv_config_manager.temporal.ngc.workflows.infiniband_mlnx_os_upgrade import (
    InfinibandMlnxOSUpgradeInput,
    InfinibandMlnxOSUpgradeWorkflow,
)

# Test-specific retry policy
TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    """Mock get_network_device activity."""
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="ib-switch-01",
            role="infiniband-switch",
            platform="mlnx-os",
            site="SITEA",
            device_type="sn4600",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
        )
    )


@activity.defn(name="get_os_image_versions")
async def mock_get_os_image_versions(
    activity_input: GetOSImageVersionsInput,
) -> GetOSImageVersionsOutput:
    """Mock get_os_image_versions activity."""
    return GetOSImageVersionsOutput(
        intended_firmware="3.10.4000",
        desired_firmware="3.10.4000",
        ztp_ipv4_address="192.168.1.100",
    )


@activity.defn(name="update_intended_os_image")
async def mock_update_intended_os_image(
    activity_input: UpdateIntendedOSImageInput,
) -> None:
    """Mock update_intended_os_image activity."""
    return None


@activity.defn(name="get_mlnx_os_version")
def mock_get_mlnx_os_version(
    activity_input: GetMlnxOSVersionInput,
) -> GetMlnxOSVersionOutput:
    """Mock get_mlnx_os_version activity - returns two versions already on target."""
    return GetMlnxOSVersionOutput(current_os_versions=["3.10.4000", "3.10.4000"])


@activity.defn(name="get_mlnx_os_version")
def mock_get_mlnx_os_version_needs_upgrade(
    activity_input: GetMlnxOSVersionInput,
) -> GetMlnxOSVersionOutput:
    """Mock get_mlnx_os_version activity - returns versions that need upgrade."""
    return GetMlnxOSVersionOutput(current_os_versions=["3.9.1000", "3.9.1000"])


@activity.defn(name="download_mlnx_os")
def mock_download_mlnx_os(
    activity_input: DownloadMlnxOSInput,
) -> DownloadMlnxOSOutput:
    """Mock download_mlnx_os activity."""
    return DownloadMlnxOSOutput(
        download_status="completed",
        image_name="image-X86_64-3.10.4000.img",
    )


@activity.defn(name="install_mlnx_os")
def mock_install_mlnx_os(
    activity_input: InstallMlnxOSInput,
) -> InstallMlnxOSOutput:
    """Mock install_mlnx_os activity."""
    return InstallMlnxOSOutput(install_status="success")


@activity.defn(name="reload_mlnx_os")
def mock_reload_mlnx_os(
    activity_input: ReloadMlnxOSInput,
) -> ReloadMlnxOSOutput:
    """Mock reload_mlnx_os activity."""
    return ReloadMlnxOSOutput(
        save_config_status="Configuration saved",
        reload_status="Device reload initiated",
        is_online=True,
    )


@activity.defn(name="cleanup_mlnx_os")
def mock_cleanup_mlnx_os(
    activity_input: CleanupMlnxOSInput,
) -> CleanupMlnxOSOutput:
    """Mock cleanup_mlnx_os activity."""
    return CleanupMlnxOSOutput(cleanup_status="success")


@pytest.mark.asyncio
@pytest.mark.timeout(60)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_single_stage_already_on_target(
    _,
    env,
):
    """Test single stage workflow when device is already on target version."""
    task_queue_name = str(uuid.uuid4())

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[InfinibandMlnxOSUpgradeWorkflow],
        activities=[
            mock_get_network_device,
            mock_get_os_image_versions,
            mock_update_intended_os_image,
            mock_get_mlnx_os_version,
            mock_download_mlnx_os,
            mock_install_mlnx_os,
            mock_reload_mlnx_os,
            mock_cleanup_mlnx_os,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_id = str(uuid.uuid4())
        result = await env.client.execute_workflow(
            InfinibandMlnxOSUpgradeWorkflow.run,
            InfinibandMlnxOSUpgradeInput(
                device_id="test-device-id",
                intended_version="3.10.4000",
                approved=True,
                approved_by=["TestUser"],
            ),
            id=workflow_id,
            task_queue=task_queue_name,
            execution_timeout=timedelta(seconds=20),
            retry_policy=TEST_RETRY_POLICY,
        )

        # result is a string message
        assert "already running the intended version" in result


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_single_stage_with_approval(_, time_skipping_env):
    """Test single stage workflow with approval signal and full completion."""
    async with time_skipping_env() as env:
        task_queue_name = str(uuid.uuid4())

        async with Worker(
            env.client,
            task_queue=task_queue_name,
            workflows=[InfinibandMlnxOSUpgradeWorkflow],
            activities=[
                mock_get_network_device,
                mock_get_os_image_versions,
                mock_update_intended_os_image,
                mock_get_mlnx_os_version_needs_upgrade,
                mock_download_mlnx_os,
                mock_install_mlnx_os,
                mock_reload_mlnx_os,
                mock_cleanup_mlnx_os,
            ],
            activity_executor=ThreadPoolExecutor(5),
        ):
            workflow_id = str(uuid.uuid4())

            handle = await env.client.start_workflow(
                InfinibandMlnxOSUpgradeWorkflow.run,
                InfinibandMlnxOSUpgradeInput(
                    device_id="test-device-id",
                    intended_version="3.10.4000",
                ),
                id=workflow_id,
                task_queue=task_queue_name,
                run_timeout=timedelta(minutes=10),
            )

            while await handle.query("pending_approval") is False:
                await asyncio.sleep(0.1)

            await handle.signal("approve", {"stage_name": "approve_upgrade", "user": "Test"})

            result = await handle.result()

            assert result is not None
            assert "Successfully upgraded device" in result

            final_stages = await handle.query("stages")
            for stage in final_stages:
                assert stage["state"] in ["COMPLETE", "UNREACHABLE", "NOT_STARTED"], (
                    f"Stage {stage['name']} in unexpected state: {stage['state']}"
                )

            approve_stage = next(s for s in final_stages if s["name"] == "approve_upgrade")
            assert approve_stage["state"] == "COMPLETE"
            assert len(approve_stage["approvers"]) == 1
            assert approve_stage["approvers"][0]["user"] == "Test"
            assert approve_stage["output"]["approved"] is True
