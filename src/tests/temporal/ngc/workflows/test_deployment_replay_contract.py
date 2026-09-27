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
"""Replay contracts for configuration deployment and firmware workflows."""

from pathlib import Path
from typing import Any

import pytest
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.config_diff import ConfigDiffWorkflow
from nv_config_manager.temporal.ngc.workflows.deploy import DeployWorkflow
from nv_config_manager.temporal.ngc.workflows.infiniband_mlnx_os_upgrade import (
    InfinibandMlnxOSUpgradeWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.nvlinkswitch_firmware_upgrade import (
    NVLinkSwitchFirmwareUpgradeWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.os_upgrade import SwitchOSUpgradeWorkflow

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"
_DEPLOYMENT_WORKFLOWS = [
    BackupWorkflow,
    ConfigDiffWorkflow,
    DeployWorkflow,
    InfinibandMlnxOSUpgradeWorkflow,
    NVLinkSwitchFirmwareUpgradeWorkflow,
    SwitchOSUpgradeWorkflow,
]


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
    scheduled = _scheduled_activities(_history(filename))
    decoded = []
    for item in scheduled:
        [activity_input] = await converter.decode(item.input.payloads)
        decoded.append((item.activity_type.name, activity_input))
    return decoded


def _inputs_for(
    scheduled: list[tuple[str, dict[str, Any]]],
    activity_name: str,
) -> list[dict[str, Any]]:
    return [activity_input for name, activity_input in scheduled if name == activity_name]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history_filename",
    [
        "backup.json",
        "config_diff.json",
        "deploy.json",
        "infiniband_mlnx_os_upgrade.json",
        "nvlinkswitch_firmware_upgrade.json",
        "switch_os_upgrade.json",
    ],
)
async def test_deployment_histories_replay(history_filename: str) -> None:
    """Package extraction must remain deterministic against successful histories."""
    replayer = Replayer(workflows=_DEPLOYMENT_WORKFLOWS, data_converter=get_data_converter())

    await replayer.replay_workflow(_history(history_filename))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("history_filename", "expected_activity_names"),
    [
        (
            "backup.json",
            [
                "get_network_device",
                "get_network_device",
                "load_running_configuration",
                "load_intended_configuration",
                "perform_candidate_diff",
                "send_slack_message",
                "persist_config_backup",
                "record_backup_config_manager_plugin",
                "publish_nats",
            ],
        ),
        (
            "config_diff.json",
            [
                "get_network_device",
                "load_intended_configuration",
                "perform_candidate_diff",
                "publish_nats",
            ],
        ),
        (
            "deploy.json",
            [
                "get_network_device",
                "load_intended_configuration",
                "get_network_device",
                "perform_candidate_diff",
                "get_network_device",
                "apply_approved_configuration",
                "publish_nats",
            ],
        ),
        (
            "infiniband_mlnx_os_upgrade.json",
            [
                "get_os_image_versions",
                "get_network_device",
                "get_mlnx_os_version",
                "download_mlnx_os",
                "install_mlnx_os",
                "reload_mlnx_os",
                "install_mlnx_os",
                "reload_mlnx_os",
                "get_mlnx_os_version",
                "cleanup_mlnx_os",
            ],
        ),
        (
            "nvlinkswitch_firmware_upgrade.json",
            [
                "get_network_device",
                "get_current_os",
                "get_running_firmware",
                "compare_running_desired",
                "check_recorded_config_drift",
                "update_device_context",
                "validate_render_targets",
                "execute_ztp",
                "poll_ztp_status",
                "get_current_os",
                "get_running_firmware",
                "compare_running_desired",
                "reboot_device",
                "wait_reboot",
                "get_current_os",
                "get_running_firmware",
                "compare_running_desired",
                "publish_nats",
            ],
        ),
        (
            "switch_os_upgrade.json",
            [
                "get_network_device",
                "get_os_image_versions",
                "check_recorded_config_drift",
                "update_intended_os_image",
                "validate_rendered_image_change",
                "execute_ztp",
                "poll_image",
                "poll_ztp_status",
                "publish_nats",
            ],
        ),
    ],
)
async def test_deployment_histories_preserve_activity_order(
    history_filename: str,
    expected_activity_names: list[str],
) -> None:
    """Replay fixtures separately freeze every scheduled activity type name."""
    scheduled = await _scheduled_inputs(history_filename)

    assert [name for name, _ in scheduled] == expected_activity_names


@pytest.mark.asyncio
async def test_backup_history_preserves_configuration_arguments() -> None:
    scheduled = await _scheduled_inputs("backup.json")

    [running] = _inputs_for(scheduled, "load_running_configuration")
    [intended] = _inputs_for(scheduled, "load_intended_configuration")
    [candidate] = _inputs_for(scheduled, "perform_candidate_diff")
    [persist] = _inputs_for(scheduled, "persist_config_backup")
    [record] = _inputs_for(scheduled, "record_backup_config_manager_plugin")

    assert running["id"] == intended["id"] == "mock_device_uuid"
    assert candidate == {
        "configuration": "mock intended config",
        "device_data": running,
        "partial": False,
    }
    assert persist == {
        "commit_message": "Backup trigger: API User: test_user",
        "device_data": running,
        "device_running_config": "mock config",
        "user": "test_user",
        "user_domain": "nvidia.com",
    }
    assert record["commit_id"] == "mock_commit_id"
    assert record["deployed_commit_id"] is None
    assert record["device_id"] == "mock_device_uuid"
    assert record["path"] == "mock_device_uuid/startup.yaml"
    assert record["user"] == "test_user"


@pytest.mark.asyncio
async def test_deploy_histories_preserve_diff_and_apply_arguments() -> None:
    config_diff = await _scheduled_inputs("config_diff.json")
    deploy = await _scheduled_inputs("deploy.json")

    [read_only_diff] = _inputs_for(config_diff, "perform_candidate_diff")
    [approved_diff] = _inputs_for(deploy, "perform_candidate_diff")
    [apply] = _inputs_for(deploy, "apply_approved_configuration")

    assert read_only_diff["configuration"] == "mock intended config"
    assert read_only_diff["partial"] is False
    assert approved_diff["configuration"] == "mock intended config"
    assert approved_diff["partial"] is False
    assert apply == {
        "approved_diff": "mock_diff",
        "commit_confirm": True,
        "configuration": "mock intended config",
        "device_data": approved_diff["device_data"],
        "partial": False,
    }


@pytest.mark.asyncio
async def test_switch_os_history_preserves_render_and_ztp_arguments() -> None:
    scheduled = await _scheduled_inputs("switch_os_upgrade.json")

    [update] = _inputs_for(scheduled, "update_intended_os_image")
    [validate] = _inputs_for(scheduled, "validate_rendered_image_change")
    [execute] = _inputs_for(scheduled, "execute_ztp")
    [poll_image] = _inputs_for(scheduled, "poll_image")
    [poll_ztp] = _inputs_for(scheduled, "poll_ztp_status")

    assert update == {"desired_firmware": "5.1.0", "device_id": "mock_device_uuid"}
    assert validate["desired_image"] == "5.1.0"
    assert validate["device_data"]["id"] == "mock_device_uuid"
    assert execute["device_data"] == validate["device_data"]
    assert poll_image == {
        "device_data": validate["device_data"],
        "expected_image": "5.1.0",
    }
    assert poll_ztp == {
        "device_data": validate["device_data"],
        "timeout_minutes": 30,
        "ztp_execution_timestamp": None,
    }


@pytest.mark.asyncio
async def test_mlnx_os_history_preserves_command_activity_arguments() -> None:
    scheduled = await _scheduled_inputs("infiniband_mlnx_os_upgrade.json")

    [download] = _inputs_for(scheduled, "download_mlnx_os")
    installs = _inputs_for(scheduled, "install_mlnx_os")
    reloads = _inputs_for(scheduled, "reload_mlnx_os")
    [cleanup] = _inputs_for(scheduled, "cleanup_mlnx_os")

    assert download["device_data"]["id"] == "test-device-id"
    assert download["intended_version"] == "3.10.4000"
    assert download["ztp_ipv4_address"] == "192.168.1.100"
    assert installs == [
        {
            "device_data": download["device_data"],
            "image_name": "image-X86_64-3.10.4000.img",
        },
        {
            "device_data": download["device_data"],
            "image_name": "image-X86_64-3.10.4000.img",
        },
    ]
    assert reloads == [
        {"device_data": download["device_data"]},
        {"device_data": download["device_data"]},
    ]
    assert cleanup == {
        "device_data": download["device_data"],
        "image_name": "image-X86_64-3.10.4000.img",
    }


@pytest.mark.asyncio
async def test_nvlink_history_preserves_validation_and_reboot_arguments() -> None:
    scheduled = await _scheduled_inputs("nvlinkswitch_firmware_upgrade.json")

    [update] = _inputs_for(scheduled, "update_device_context")
    [validate] = _inputs_for(scheduled, "validate_render_targets")
    [poll_ztp] = _inputs_for(scheduled, "poll_ztp_status")
    [reboot] = _inputs_for(scheduled, "reboot_device")
    [wait_reboot] = _inputs_for(scheduled, "wait_reboot")
    comparisons = _inputs_for(scheduled, "compare_running_desired")

    assert update["bundle_version"] == "1.2.2"
    assert validate == {
        "desired_firmware": {
            "asic": "35.2014.1750",
            "bios": "0ACTV_00.01.017",
            "bmc": "88.0002.1140",
            "cpld": "CPLD000370_REV0600",
        },
        "device_data": update["device_data"],
    }
    assert poll_ztp == {
        "device_data": update["device_data"],
        "timeout_minutes": 110,
        "ztp_execution_timestamp": "2024-01-01T12:00:00",
    }
    assert reboot == {"device_data": update["device_data"]}
    assert wait_reboot == {
        "device_data": update["device_data"],
        "timeout": 10,
        "ztp_execution_timestamp": "2024-01-01T12:00:00",
    }
    assert len(comparisons) == 3
    assert all(item["bundle_version"] == "1.2.2" for item in comparisons)
    assert all(item["device_data"] == update["device_data"] for item in comparisons)
