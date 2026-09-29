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
"""Execution coverage for password-rotation workflows."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from nv_config_manager_dcim.workflow_models import NetworkDeviceData, Platform
from nv_config_manager_dcim_nautobot_2x.workflow_models import (
    network_device_from_nautobot_graphql,
)
from pydantic import ValidationError
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nv_config_manager.temporal.ngc.workflows.backup import BackupInput
from nv_config_manager.temporal.ngc.workflows.device_password_rotation import (
    DevicePasswordRotationInput,
    DevicePasswordRotationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.site_password_rotation import (
    PasswordRotationResultData,
    SitePasswordRotationInput,
    SitePasswordRotationWorkflow,
)
from nv_config_manager_workflows.activities.dcim import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    GetNetworkDevicesInput,
    GetNetworkDevicesOutput,
)
from nv_config_manager_workflows.activities.deploy import DiffActivityInput
from nv_config_manager_workflows.activities.device_password_rotation import (
    GetPasswordMappingsInput,
    GetPasswordMappingsOutput,
    ValidatePlatformSupportInput,
    ValidatePlatformSupportOutput,
    format_password_rotation_results,
)
from nv_config_manager_workflows.activities.nats import PublishNatsInput

TEST_DEVICE_DATA = {
    "id": "device-1-uuid",
    "name": "rno1-tor-001",
    "role": {"name": "TAN-Leaf"},
    "location": {"location_type": {"name": "Site"}, "name": "rno1"},
    "device_type": {"model": "SN2010"},
    "platform": {"name": "Cumulus Linux"},
    "primary_ip4": {"host": "10.1.1.1"},
    "primary_ip6": None,
    "rack": {"name": "a01"},
    "position": 1,
    "configmanagerdevicestatus": {
        "render_enabled": True,
        "deploy_enabled": True,
        "backup_enabled": True,
        "ztp_enabled": True,
    },
    "config_context": {
        "password_mappings": {
            "cumulus": {
                "password": "root_password",
                "role": "system-admin",
                "rotation": "r1",
            }
        }
    },
}


def _device() -> NetworkDeviceData:
    return NetworkDeviceData(
        id="device-1",
        name="leaf-1",
        rack="rack-1",
        position=1,
        role="leaf",
        site="site-1",
        device_type="switch",
        platform=Platform.CUMULUS_LINUX,
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    _activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(device=_device())


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    _activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    return GetNetworkDevicesOutput(devices=[])


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(
    _device_data: NetworkDeviceData,
) -> tuple[str, str, str]:
    return "intended config", "commit-1", "https://config.example.test/intended"


@activity.defn(name="validate_platform_support")
async def mock_validate_platform_support(
    _activity_input: ValidatePlatformSupportInput,
) -> ValidatePlatformSupportOutput:
    return ValidatePlatformSupportOutput(normalized_platform="cumulus")


@activity.defn(name="get_password_mappings")
async def mock_get_password_mappings(
    activity_input: GetPasswordMappingsInput,
) -> GetPasswordMappingsOutput:
    return GetPasswordMappingsOutput(username=activity_input.username)


@activity.defn(name="perform_candidate_diff")
async def mock_perform_candidate_diff(_activity_input: DiffActivityInput) -> str:
    return ""


@activity.defn(name="get_ui_base_url")
async def mock_get_ui_base_url() -> str:
    return "https://workflow.example.test"


@activity.defn(name="publish_nats")
async def mock_publish_nats(_activity_input: PublishNatsInput) -> None:
    return None


@workflow.defn(name="BackupWorkflow", sandboxed=False)
class MockBackupWorkflow:
    @workflow.run
    async def run(self, _workflow_input: BackupInput) -> None:
        return None


@pytest.mark.asyncio
async def test_device_password_rotation_no_diff_path(env: WorkflowEnvironment) -> None:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[DevicePasswordRotationWorkflow, MockBackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            mock_validate_platform_support,
            mock_get_password_mappings,
            mock_perform_candidate_diff,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(2),
    ):
        handle = await env.client.start_workflow(
            DevicePasswordRotationWorkflow.run,
            DevicePasswordRotationInput(
                device_id="device-1",
                selected_secret="admin",
            ),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            run_timeout=timedelta(minutes=2),
        )

        assert await handle.result() is False


@pytest.mark.asyncio
async def test_site_password_rotation_empty_site_formats_result(env: WorkflowEnvironment) -> None:
    task_queue = str(uuid.uuid4())
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[SitePasswordRotationWorkflow],
        activities=[
            mock_get_network_devices,
            mock_get_ui_base_url,
            format_password_rotation_results,
            mock_publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(2),
    ):
        handle = await env.client.start_workflow(
            SitePasswordRotationWorkflow.run,
            SitePasswordRotationInput(location="site-1", selected_secret="admin"),
            id=str(uuid.uuid4()),
            task_queue=task_queue,
            run_timeout=timedelta(minutes=2),
        )

        result = await handle.result()
        assert result == "**Total devices**: 0\n**Updated**: 0\n**Not Updated**: 0"


class TestSitePasswordRotationInput:
    """Tests for SitePasswordRotationInput validation."""

    def test_location_must_not_be_empty(self) -> None:
        """Reject an empty location before starting the workflow."""
        with pytest.raises(ValidationError):
            SitePasswordRotationInput(location="", selected_secret="device-password")


class TestPasswordRotationResultData:
    """Tests for PasswordRotationResultData model."""

    def test_password_rotation_result_data_success(self) -> None:
        """Test successful password rotation result."""
        device = network_device_from_nautobot_graphql(TEST_DEVICE_DATA)
        result = PasswordRotationResultData(
            device=device,
            success=True,
            child_workflow_id="workflow-123",
        )
        assert result.device is not None
        assert result.device.name == "rno1-tor-001"
        assert result.success is True
        assert result.error is None
        assert result.child_workflow_id == "workflow-123"

    def test_password_rotation_result_data_failure(self) -> None:
        """Test failed password rotation result."""
        device = network_device_from_nautobot_graphql(TEST_DEVICE_DATA)
        result = PasswordRotationResultData(
            device=device,
            success=False,
            error="Password rotation failed",
            child_workflow_id="workflow-456",
        )
        assert result.device is not None
        assert result.device.name == "rno1-tor-001"
        assert result.success is False
        assert result.error == "Password rotation failed"
        assert result.child_workflow_id == "workflow-456"

    def test_password_rotation_result_data_no_device(self) -> None:
        """Test password rotation result with no device data."""
        result = PasswordRotationResultData(
            device=None,
            success=False,
            error="Device not found",
            child_workflow_id="workflow-789",
        )
        assert result.device is None
        assert result.success is False
        assert result.error == "Device not found"
