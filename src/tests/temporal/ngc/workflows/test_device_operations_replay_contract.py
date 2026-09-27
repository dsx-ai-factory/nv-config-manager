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
"""Replay contracts for device validation, password, and Redfish workflows."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.bmc import RedfishProvisioningWorkflow
from nv_config_manager.temporal.ngc.workflows.cable_validation import (
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.cumulus_hardware_validation import (
    ValidateHardwareWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_DEVICE_OPERATION_WORKFLOWS = [
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
    ValidateHardwareWorkflow,
    DevicePasswordRotationWorkflow,
    SitePasswordRotationWorkflow,
    RedfishProvisioningWorkflow,
]
_EXPECTED_ACTIVITY_NAMES = {
    "device_cable_validation.json": [
        "validate_hostname",
        "get_device_intended_neighbors",
        "get_device_actual_neighbors",
        "get_device_mac_table",
        "get_device_arp_table",
        "validate_device_neighbors",
        "decorate_result",
        "format_device_validation_result",
        "update_cable_statuses",
        "publish_nats",
    ],
    "site_cable_validation.json": [
        "get_network_devices",
        "get_ui_base_url",
        "format_results",
        "publish_nats",
    ],
    "hardware_validation.json": [
        "get_network_devices",
        "get_platform",
        "get_platform_environment_fan",
        "get_platform_environment_led",
        "get_platform_environment_psu",
        "get_platform_environment_voltage",
        "get_platform_inventory",
        "create_consolidated_excel_export",
        "publish_nats",
    ],
    "device_password_rotation.json": [
        "get_network_device",
        "load_intended_configuration",
        "get_network_device",
        "validate_platform_support",
        "get_password_mappings",
        "perform_candidate_diff",
        "publish_nats",
    ],
    "site_password_rotation.json": [
        "get_network_devices",
        "get_ui_base_url",
        "format_password_rotation_results",
        "publish_nats",
    ],
    "redfish_provisioning.json": [
        "get_network_devices",
        "discover_redfish_hosts",
        "get_device_arp_table",
        "get_device_arp_table",
        "populate_redfish_macs",
        "set_redfish_password",
        "power_on_host",
        "power_on_host",
        "discover_redfish_hosts",
        "get_device_arp_table",
        "get_device_arp_table",
        "populate_redfish_macs",
        "set_redfish_password",
        "set_redfish_password",
        "set_redfish_password",
        "power_on_host",
        "power_on_host",
        "power_on_host",
        "get_server_details",
        "get_server_details",
        "get_dpu_details",
        "get_dpu_details",
        "get_dpu_details",
        "update_dpu_data",
        "update_dpu_data",
        "factory_reset_bmc",
        "factory_reset_bmc",
        "factory_reset_bmc",
        "factory_reset_bmc",
    ],
}


def _history(filename: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(
        filename.removesuffix(".json"),
        (_FIXTURE_ROOT / filename).read_text(),
    )


def _scheduled_activities(
    history: WorkflowHistory,
) -> list[ActivityTaskScheduledEventAttributes]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def _scheduled_inputs(filename: str) -> list[tuple[str, dict[str, Any]]]:
    converter = get_data_converter()
    decoded = []
    for item in _scheduled_activities(_history(filename)):
        payloads = await converter.decode(item.input.payloads)
        activity_input = payloads[0] if payloads else {}
        decoded.append((item.activity_type.name, activity_input))
    return decoded


def _inputs_for(
    scheduled: list[tuple[str, dict[str, Any]]],
    activity_name: str,
) -> list[dict[str, Any]]:
    return [activity_input for name, activity_input in scheduled if name == activity_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_device_operation_histories_replay(history_filename: str) -> None:
    """Package extraction remains deterministic against captured device histories."""
    replayer = Replayer(
        workflows=_DEVICE_OPERATION_WORKFLOWS,
        data_converter=get_data_converter(),
    )

    if history_filename == "redfish_provisioning.json":
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await replayer.replay_workflow(_history(history_filename))
    else:
        await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _EXPECTED_ACTIVITY_NAMES)
async def test_device_operation_histories_preserve_activity_order(history_filename: str) -> None:
    """Captured histories independently freeze every scheduled activity type name."""
    scheduled = await _scheduled_inputs(history_filename)

    assert [name for name, _ in scheduled] == _EXPECTED_ACTIVITY_NAMES[history_filename]


@pytest.mark.asyncio
async def test_cable_histories_preserve_validation_and_report_arguments() -> None:
    device = await _scheduled_inputs("device_cable_validation.json")
    site = await _scheduled_inputs("site_cable_validation.json")

    [validation] = _inputs_for(device, "validate_device_neighbors")
    [status_update] = _inputs_for(device, "update_cable_statuses")
    [site_report] = _inputs_for(site, "format_results")

    assert validation["device"]["id"] == status_update["device_id"]
    assert validation["ignore_no_neighbor"] is False
    assert status_update["cable_statuses"] == {
        "swp0": "Connected",
        "swp1": "Connected",
        "swp2": "Connected",
        "swp3": "Connected",
    }
    assert site_report["ignore_no_neighbor"] is False
    assert list(site_report["devices"]) == ["mock_device1", "mock_device2", "mock_device3"]


@pytest.mark.asyncio
async def test_hardware_history_preserves_collection_and_workbook_arguments() -> None:
    scheduled = await _scheduled_inputs("hardware_validation.json")

    [platform] = _inputs_for(scheduled, "get_platform")
    [workbook] = _inputs_for(scheduled, "create_consolidated_excel_export")

    assert platform["device_data"]["id"] == "c8f7a95e-4b2a-4e8c-9d5f-1a2b3c4d5e6f"
    assert list(workbook["stage_data"]) == [
        "fan",
        "inventory",
        "led",
        "platform",
        "psu",
        "voltage",
    ]


@pytest.mark.asyncio
async def test_password_histories_preserve_mapping_diff_and_formatter_arguments() -> None:
    device = await _scheduled_inputs("device_password_rotation.json")
    site = await _scheduled_inputs("site_password_rotation.json")

    [mapping] = _inputs_for(device, "get_password_mappings")
    [candidate] = _inputs_for(device, "perform_candidate_diff")
    [report] = _inputs_for(site, "format_password_rotation_results")

    assert mapping["device"]["id"] == "device-1"
    assert mapping["username"] == "admin"
    assert candidate["configuration"] == "intended config"
    assert candidate["partial"] is False
    assert report == {
        "failed_devices": {},
        "successful_devices": {},
        "total_devices": 0,
        "ui_base_url": "https://workflow.example.test",
    }


@pytest.mark.asyncio
async def test_redfish_history_preserves_authenticated_and_dpu_arguments() -> None:
    scheduled = await _scheduled_inputs("redfish_provisioning.json")

    password_hosts = _inputs_for(scheduled, "set_redfish_password")
    dpu_hosts = _inputs_for(scheduled, "get_dpu_details")
    updates = _inputs_for(scheduled, "update_dpu_data")

    assert [item["host"]["address"] for item in password_hosts] == [
        "127.0.0.2",
        "127.0.0.3",
        "127.0.0.4",
        "127.0.0.5",
    ]
    assert [item["host"]["vendor"] for item in dpu_hosts] == ["Nvidia"] * 3
    assert [item["server"]["address"] for item in updates] == ["127.0.0.1", "127.0.0.2"]
