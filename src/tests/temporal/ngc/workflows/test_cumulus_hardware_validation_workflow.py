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
"""Test Cumulus Hardware Validation Workflow."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from nv_config_manager.temporal.ngc.activities.hardware_validation import (
    CreateConsolidatedExcelInput,
    CreateConsolidatedExcelOutput,
    CreateExcelInput,
    CreateExcelOutput,
    HardwareValidationInput,
    HardwareValidationOutput,
    HardwareValidationResult,
)
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
    GetNetworkDevicesInput,
    GetNetworkDevicesOutput,
)
from nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation import (
    ValidateHardwareInput,
    ValidateHardwareWorkflow,
)
from tests.temporal.ngc.activities.test_hardware_validation_data import (
    PLATFORM_RESPONSE,
    TEST_DEVICE,
)

TEST_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
TEST_TIMEOUT = timedelta(seconds=10)


@activity.defn(name="get_network_devices")
async def mock_get_network_devices(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Mock get_network_devices activity."""
    device = TEST_DEVICE.model_copy()
    device.platform = "cumulus-linux"  # Ensure it's detected as Cumulus
    return GetNetworkDevicesOutput(devices=[device])


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    """Mock get_network_device activity."""
    device = TEST_DEVICE.model_copy()
    device.id = activity_input.device_id
    return GetNetworkDeviceOutput(device=device)


@activity.defn(name="get_platform")
def mock_get_platform(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform activity with real switch data."""
    return HardwareValidationOutput(info=PLATFORM_RESPONSE)


@activity.defn(name="publish_nats")
async def mock_publish_nats(_activity_input: PublishNatsInput) -> None:
    """Mock publish nats activity."""
    return None


@activity.defn(name="get_platform_environment_fan")
def mock_get_platform_environment_fan(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform_environment_fan activity."""
    return HardwareValidationOutput(info={"mock": "fan_data"})


@activity.defn(name="get_platform_environment_led")
def mock_get_platform_environment_led(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform_environment_led activity."""
    return HardwareValidationOutput(info={"mock": "led_data"})


@activity.defn(name="get_platform_environment_psu")
def mock_get_platform_environment_psu(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform_environment_psu activity."""
    return HardwareValidationOutput(info={"mock": "psu_data"})


@activity.defn(name="get_platform_environment_voltage")
def mock_get_platform_environment_voltage(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform_environment_voltage activity."""
    return HardwareValidationOutput(info={"mock": "voltage_data"})


@activity.defn(name="get_platform_inventory")
def mock_get_platform_inventory(
    _activity_input: HardwareValidationInput,
) -> HardwareValidationOutput:
    """Mock get_platform_inventory activity."""
    return HardwareValidationOutput(info={"mock": "inventory_data"})


@activity.defn(name="create_excel_export")
def mock_create_excel_export(
    _activity_input: CreateExcelInput,
) -> CreateExcelOutput:
    """Mock create_excel_export activity."""
    # Create a minimal Excel file in memory for testing
    import base64
    from io import BytesIO

    import pandas as pd

    # Create a simple DataFrame for the mock
    df = pd.DataFrame(
        [
            {
                "device_name": "test-device",
                "rack_name": "test-rack",
                "rack_position": "1",
                "item_name": "test-item",
                "status": "ok",
            }
        ]
    )

    excel_buffer = BytesIO()
    df.to_excel(excel_buffer, index=False, engine="openpyxl")
    excel_buffer.seek(0)
    mock_excel_data = excel_buffer.getvalue()

    # Base64 encode for Temporal serialization
    excel_data_b64 = base64.b64encode(mock_excel_data).decode("utf-8")

    return CreateExcelOutput(excel_data=excel_data_b64, row_count=len(df))


@activity.defn(name="create_consolidated_excel_export")
async def mock_create_consolidated_excel_export(
    activity_input: CreateConsolidatedExcelInput,
) -> CreateConsolidatedExcelOutput:
    """Mock create_consolidated_excel_export activity."""
    import base64
    from io import BytesIO

    import pandas as pd

    excel_buffer = BytesIO()
    worksheet_counts = {}
    total_row_count = 0

    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        # Create a simple worksheet for each stage
        for stage_name in activity_input.stage_data.keys():
            df = pd.DataFrame(
                [
                    {
                        "device_name": "test-device",
                        "rack_name": "test-rack",
                        "rack_position": "42",
                        "item_name": f"test-{stage_name}",
                        "status": "ok",
                    }
                ]
            )
            sheet_name = stage_name.capitalize()
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet_counts[sheet_name] = 1
            total_row_count += 1

    excel_buffer.seek(0)
    mock_excel_data = excel_buffer.getvalue()
    excel_data_b64 = base64.b64encode(mock_excel_data).decode("utf-8")

    return CreateConsolidatedExcelOutput(
        excel_data=excel_data_b64,
        total_row_count=total_row_count,
        worksheet_counts=worksheet_counts,
    )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.timedelta",
    return_value=TEST_TIMEOUT,
)
async def test_cumulus_hardware_validation_workflow(
    _mock_timedelta,
    _mock_retry_policy,
    _mock_time,
    env,
):
    """Test hardware validation workflow with real switch API data."""
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[ValidateHardwareWorkflow],
        activities=[
            mock_get_network_devices,
            mock_get_network_device,
            mock_get_platform,
            mock_publish_nats,
            mock_get_platform_environment_fan,
            mock_get_platform_environment_led,
            mock_get_platform_environment_psu,
            mock_get_platform_environment_voltage,
            mock_get_platform_inventory,
            mock_create_excel_export,
            mock_create_consolidated_excel_export,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = ValidateHardwareInput(
            site="test-site",
            roles=["tor-switch"],
            status=["active"],
            tenant="test-tenant",
            device_type_ids=[],
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())

        handle: WorkflowHandle = await client.start_workflow(
            ValidateHardwareWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=2),
        )

        result = await handle.result()
        assert isinstance(result, HardwareValidationResult)
        assert result.success is True

        stages = await handle.query("stages")
        assert len(stages) == 9

        completed_stages = [s for s in stages if s["state"] == "COMPLETE"]
        assert len(completed_stages) == 9

        stage_names = {s["name"] for s in stages}
        expected_stages = {
            "get_devices_to_validate",
            "get_device_info",
            "get_platform",
            "get_environment_fan",
            "get_environment_led",
            "get_environment_psu",
            "get_environment_voltage",
            "get_inventory",
            "generate_consolidated_report",
        }
        assert stage_names == expected_stages

        for stage in stages:
            stage_name = stage["name"]
            stage_output = stage.get("output", {})

            if stage_name == "get_device_info":
                assert "devices_data" in stage_output
                assert len(stage_output["devices_data"]) > 0

            elif stage_name == "get_platform":
                assert "devices_info" in stage_output
                display = stage_output.get("display", "")
                assert "validation completed" in display

            elif stage_name in [
                "get_environment_fan",
                "get_environment_led",
                "get_environment_psu",
                "get_environment_voltage",
                "get_inventory",
            ]:
                assert "devices_info" in stage_output
                display = stage_output.get("display", "")
                assert "Export to Excel" not in display
                assert "validation completed" in display

            elif stage_name == "generate_consolidated_report":
                display = stage_output.get("display", "")
                assert "Download Excel Summary" in display
                assert "total_row_count" in stage_output
                assert stage_output["total_row_count"] > 0


@activity.defn(name="get_network_devices")
async def mock_get_network_devices_empty(
    activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Mock get_network_devices activity that returns no devices."""
    return GetNetworkDevicesOutput(devices=[])


@activity.defn(name="get_network_devices")
async def mock_get_network_devices_invalid_filter(
    _activity_input: GetNetworkDevicesInput,
) -> GetNetworkDevicesOutput:
    """Mock get_network_devices activity with a Nautobot GraphQL filter error."""
    raise ApplicationError(
        "GraphQL error: {'tenant': "
        "['Select a valid choice. invalid-tenant is not one of the available choices.']}",
        non_retryable=True,
    )


@activity.defn(name="create_consolidated_excel_export")
async def mock_create_consolidated_excel_export_empty(
    activity_input: CreateConsolidatedExcelInput,
) -> CreateConsolidatedExcelOutput:
    """Mock create_consolidated_excel_export activity for empty result."""
    import base64
    from io import BytesIO

    import pandas as pd

    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df = pd.DataFrame(columns=["message"])
        df.loc[0] = ["No devices matched the specified criteria"]
        df.to_excel(writer, sheet_name="Summary", index=False)

    excel_buffer.seek(0)
    mock_excel_data = excel_buffer.getvalue()
    excel_data_b64 = base64.b64encode(mock_excel_data).decode("utf-8")

    return CreateConsolidatedExcelOutput(
        excel_data=excel_data_b64, total_row_count=0, worksheet_counts={"Summary": 1}
    )


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.timedelta",
    return_value=TEST_TIMEOUT,
)
async def test_cumulus_hardware_validation_workflow_no_devices(
    _mock_timedelta,
    _mock_retry_policy,
    _mock_time,
    env,
):
    """Test hardware validation workflow when no devices match the filter criteria."""
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[ValidateHardwareWorkflow],
        activities=[
            mock_get_network_devices_empty,
            mock_get_network_device,
            mock_get_platform,
            mock_publish_nats,
            mock_get_platform_environment_fan,
            mock_get_platform_environment_led,
            mock_get_platform_environment_psu,
            mock_get_platform_environment_voltage,
            mock_get_platform_inventory,
            mock_create_excel_export,
            mock_create_consolidated_excel_export_empty,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        workflow_input = ValidateHardwareInput(
            site="test-site",
            roles=["non-existent-role"],
            status=["active"],
            tenant="test-tenant",
            device_type_ids=[],
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())

        handle: WorkflowHandle = await client.start_workflow(
            ValidateHardwareWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=2),
        )

        result = await handle.result()
        assert isinstance(result, HardwareValidationResult)
        assert result.success is False
        assert result.devices_validated == 0
        assert result.total_entries == 0

        stages = await handle.query("stages")
        completed_stages = [s for s in stages if s["state"] == "COMPLETE"]
        unreachable_stages = [s for s in stages if s["state"] == "UNREACHABLE"]
        assert [stage["name"] for stage in completed_stages] == ["get_devices_to_validate"]
        assert len(unreachable_stages) == len(stages) - 1

        discovery_stage = next(s for s in stages if s["name"] == "get_devices_to_validate")
        assert "No devices matched the specified filters" in discovery_stage["output"]["display"]
        assert result.message == discovery_stage["output"]["display"]


@pytest.mark.asyncio
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.DEFAULT_ACTIVITY_RETRY_POLICY",
    return_value=TEST_RETRY_POLICY,
)
@patch(
    "nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation.timedelta",
    return_value=TEST_TIMEOUT,
)
async def test_cumulus_hardware_validation_workflow_invalid_filter(
    _mock_timedelta,
    _mock_retry_policy,
    _mock_time,
    env,
):
    """Test hardware validation returns a clean result for invalid Nautobot filters."""
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[ValidateHardwareWorkflow],
        activities=[
            mock_get_network_devices_invalid_filter,
            mock_publish_nats,
        ],
    ):
        workflow_input = ValidateHardwareInput(
            site="test-site",
            roles=["tor-switch"],
            status=["active"],
            tenant="invalid-tenant",
            device_type_ids=[],
            raise_for_invalid=False,
        )
        workflow_id = str(uuid.uuid4())

        handle: WorkflowHandle = await client.start_workflow(
            ValidateHardwareWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=2),
        )

        result = await handle.result()
        assert isinstance(result, HardwareValidationResult)
        assert result.success is False
        assert result.devices_validated == 0
        assert result.total_entries == 0
        assert "Invalid hardware validation device filter" in result.message
        assert "tenant: Select a valid choice" in result.message
        assert "invalid-tenant is not one of the available choices" in result.message

        stages = await handle.query("stages")
        completed_stages = [s for s in stages if s["state"] == "COMPLETE"]
        unreachable_stages = [s for s in stages if s["state"] == "UNREACHABLE"]
        assert [stage["name"] for stage in completed_stages] == ["get_devices_to_validate"]
        assert len(unreachable_stages) == len(stages) - 1

        discovery_stage = next(s for s in stages if s["name"] == "get_devices_to_validate")
        assert discovery_stage["output"]["invalid_filter"] is True
        assert "tenant: Select a valid choice" in discovery_stage["output"]["display"]
