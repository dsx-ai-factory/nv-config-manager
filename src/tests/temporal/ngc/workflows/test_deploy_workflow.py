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
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from unittest.mock import ANY, patch

import pytest
from pydantic import ValidationError
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.worker import Worker

from nv_config_manager.temporal.client.device import ConfigApplyFailureException
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.activities.backup import (
    load_running_configuration,
)
from nv_config_manager.temporal.ngc.activities.deploy import (
    LoadPartialConfigurationActivityInput,
    apply_approved_configuration,
    perform_candidate_diff,
    validate_config_diff,
)
from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.activities.nautobot import (
    GetNetworkDeviceInput,
    GetNetworkDeviceOutput,
)
from nv_config_manager.temporal.ngc.workflows.backup import BackupWorkflow
from nv_config_manager.temporal.ngc.workflows.deploy import (
    INTENDED_CONFIG_COMMIT_ID_DESCRIPTION,
    TENANT_CONFIG_COMMIT_ID_DESCRIPTION,
    DeployInput,
    DeployWorkflow,
    TenantDeployInput,
    TenantDeployWorkflow,
)
from tests.temporal.conftest import mock_send_slack_message


@activity.defn(name="get_network_device")
async def mock_get_network_device(
    activity_input: GetNetworkDeviceInput,
) -> GetNetworkDeviceOutput:
    return GetNetworkDeviceOutput(
        device=NetworkDeviceData(
            id=activity_input.device_id,
            name="mock_device",
            role="mock_role",
            platform=Platform.CUMULUS_LINUX,
            device_type="sn4000",
            site="SITEA",
            primary_ip4="10.0.0.1",
            primary_ip6=None,
            render_enabled=False,
            deploy_enabled=False,
            backup_enabled=False,
            ztp_enabled=False,
            intent=None,
        )
    )


@activity.defn(name="load_intended_configuration")
async def mock_load_intended_configuration(
    _device_data: NetworkDeviceData,
) -> tuple[str, str, str]:
    return (
        "mock intended config",
        "mock_commit_id",
        "https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_commit_id",
    )


# Global state for mock activities
_newer_commit_mock_state = {
    "newer_commit_allowed": True,
    "use_newer_commit": False,
}


@pytest.mark.parametrize("invalid_commit_id", ["abc", "12.5", "-1", ""])
def test_tenant_deploy_input_rejects_non_numeric_commit_ids(invalid_commit_id):
    """Reject invalid snapshot versions before starting the workflow."""
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        TenantDeployInput(
            device="mock_device_uuid",
            tenant_config_commit_id=invalid_commit_id,
            intended_config_commit_id="11",
        )


def test_tenant_deploy_input_schema_requires_both_snapshot_commit_ids_or_neither():
    """Publish the runtime snapshot-pair constraint in the API schema."""
    schema = TenantDeployInput.model_json_schema()

    assert schema["oneOf"] == [
        {
            "required": [
                "tenant_config_commit_id",
                "intended_config_commit_id",
            ],
            "properties": {
                "tenant_config_commit_id": {
                    "description": TENANT_CONFIG_COMMIT_ID_DESCRIPTION,
                    "type": "string",
                    "pattern": r"^\d+$",
                },
                "intended_config_commit_id": {
                    "description": INTENDED_CONFIG_COMMIT_ID_DESCRIPTION,
                    "type": "string",
                    "pattern": r"^\d+$",
                },
            },
        },
        {
            "not": {
                "anyOf": [
                    {"required": ["tenant_config_commit_id"]},
                    {"required": ["intended_config_commit_id"]},
                ]
            }
        },
    ]


@pytest.mark.parametrize(
    "snapshot_fields",
    [
        {"tenant_config_commit_id": None},
        {"intended_config_commit_id": None},
        {
            "tenant_config_commit_id": None,
            "intended_config_commit_id": None,
        },
        {
            "tenant_config_commit_id": None,
            "intended_config_commit_id": "11",
        },
    ],
)
def test_tenant_deploy_input_rejects_explicit_null_snapshot_commit_ids(snapshot_fields):
    """Treat explicit null snapshot fields as invalid rather than omitted."""
    with pytest.raises(ValidationError, match="must both be non-null or both be omitted"):
        TenantDeployInput(device="mock_device_uuid", **snapshot_fields)


def test_tenant_deploy_input_allows_snapshot_commit_ids_to_be_omitted():
    """Allow the internal child workflow to deploy the latest rendered snapshot."""
    deploy_input = TenantDeployInput(device="mock_device_uuid")

    assert deploy_input.tenant_config_commit_id is None
    assert deploy_input.intended_config_commit_id is None


@pytest.mark.asyncio
async def test_tenant_deploy_input_preserves_omitted_snapshot_ids_during_temporal_round_trip():
    """Temporal serialization must not turn omitted snapshot IDs into explicit nulls."""
    converter = get_data_converter()
    deploy_input = TenantDeployInput(device="mock_device_uuid")

    payloads = await converter.encode([deploy_input])
    [decoded_input] = await converter.decode(payloads, [TenantDeployInput])

    assert decoded_input == deploy_input


@activity.defn(name="load_partial_configuration")
async def mock_load_partial_configuration(
    activity_input: LoadPartialConfigurationActivityInput,
) -> tuple[str, str, str]:
    commit_id = activity_input.commit_id or "mock_tenant_commit_id"
    if activity_input.config_file == activity_input.device_data.intended_config_file:
        return (
            "mock intended config",
            commit_id,
            f"https://config-manager.example.com/device/mock_device_uuid/"
            f"startup.yaml?commit={commit_id}",
        )
    # Check if we should return a newer commit for testing
    if _newer_commit_mock_state.get("use_newer_commit", False):
        commit_id = activity_input.commit_id or "7"
        if _newer_commit_mock_state.get("newer_commit_allowed", True):
            return (
                "nv set vrf test-vrf router bgp router-id 172.28.0.2\n"
                "nv set vrf test-vrf router bgp autonomous-system 4266990009\n"
                "nv set interface swp1 ip vrf test-vrf\n"
                "nv set interface swp2 ip vrf test-vrf\n",
                commit_id,
                f"https://config-manager.example.com/device/mock_device/tenant.yaml?commit={commit_id}",
            )
        else:
            return (
                "nv set vrf test-vrf router bgp router-id 172.28.0.2\n"
                "nv set vrf test-vrf router bgp autonomous-system 4266990009\n"
                "nv set interface swp1 ip vrf test-vrf\n"
                "nv set interface swp2 ip vrf test-vrf\n"
                "nv set system hostname disallowed-change\n",  # Disallowed line
                commit_id,
                f"https://config-manager.example.com/device/mock_device/tenant.yaml?commit={commit_id}",
            )
    # Default behavior for other tests
    return (
        "mock tenant config",
        commit_id,
        f"https://config-manager.example.com/device/mock_device_uuid/tenant.yaml?commit={commit_id}",
    )


@activity.defn(name="load_partial_configuration")
async def mock_load_empty_tenant_configuration(
    activity_input: LoadPartialConfigurationActivityInput,
) -> tuple[str, str, str]:
    """Return the empty tenant render and its matching full intended render."""
    commit_id = activity_input.commit_id or "mock_commit_id"
    if activity_input.config_file == activity_input.device_data.intended_config_file:
        return (
            "mock intended config",
            commit_id,
            f"https://config-manager.example.com/device/mock_device_uuid/"
            f"startup.yaml?commit={commit_id}",
        )
    return (
        "- set: {}\n",
        commit_id,
        f"https://config-manager.example.com/device/mock_device_uuid/"
        f"tenant.yaml?commit={commit_id}",
    )


@activity.defn(name="persist_config_backup")
async def mock_persist_config_backup(_activity_input: Any) -> str:
    return "mock_commit_id"


@activity.defn(name="record_backup_config_manager_plugin")
async def mock_record_backup_config_manager_plugin(
    _activity_input: Any,
) -> tuple[bool, str]:
    markdown = """
[Configuration Backup](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=backup)
[Latest Commit](https://config-manager.example.com/commits/mock_commit_id)
"""
    return True, f"Persisted new backup configuration:\n{markdown}"


@activity.defn(name="get_ui_base_url")
async def mock_get_ui_base_url() -> str:
    return "config-manager.example.com"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow(
    _: Any,
    mock_nats_client: Any,
    mock_cumulus_connection: Any,
    env: Any,
) -> None:
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[DeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = "mock_diff"

        input_data = DeployInput(device_id="mock_device_uuid")

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            DeployWorkflow.run,
            input_data,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        while await handle.query("pending_approval") is False:
            await asyncio.sleep(1)

        expected_pre_approve_stages = [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the latest intended configuration from the Config Store.",
                "execution_time": 0.0,
                "input": {"device_id": "mock_device_uuid"},
                "name": "load_intended_configuration",
                "output": {
                    "commit_id": "mock_commit_id",
                    "display": "Loaded intended configuration from "
                    "[mock_device_uuid/startup.yaml](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_commit_id).",
                    "intended_config": "mock intended config",
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
                "approval_threshold": 1,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["load_intended_configuration"],
                "description": "Retrieve the configuration diff.",
                "execution_time": None,
                "input": {
                    "device_id": "mock_device_uuid",
                    "intended_config": "mock intended config",
                },
                "name": "perform_configuration_diff",
                "output": {
                    "approved": False,
                    "diff": "mock_diff",
                    "display": "Configuration Diff For Approval\n```\nmock_diff\n```",
                },
                "rejecters": [],
                "requires_approval": True,
                "retry_count": 0,
                "retryable": True,
                "state": "PENDING_APPROVAL",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "PENDING_APPROVAL", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["perform_configuration_diff"],
                "description": "Apply the configuration to the device.",
                "execution_time": None,
                "input": None,
                "name": "apply_configuration",
                "output": None,
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "NOT_STARTED",
                "state_history": [{"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["perform_configuration_diff"],
                "description": "Run the backup workflow for the device..",
                "execution_time": None,
                "input": None,
                "name": "perform_backup",
                "output": None,
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "NOT_STARTED",
                "state_history": [{"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}],
                "traceback": None,
            },
        ]

        assert await handle.query("stages") == expected_pre_approve_stages
        await handle.signal("approve", {"stage_name": "perform_configuration_diff", "user": "Test"})

        result = await handle.result()
        assert result

        expected_final_stages = [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the latest intended configuration from the Config Store.",
                "execution_time": 0.0,
                "input": {"device_id": "mock_device_uuid"},
                "name": "load_intended_configuration",
                "output": {
                    "commit_id": "mock_commit_id",
                    "display": "Loaded intended configuration from "
                    "[mock_device_uuid/startup.yaml](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_commit_id).",
                    "intended_config": "mock intended config",
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
                "approval_threshold": 1,
                "approvers": [{"time": "1970-01-01T00:00:00+00:00", "user": "Test"}],
                "child_workflows": [],
                "depends_on": ["load_intended_configuration"],
                "description": "Retrieve the configuration diff.",
                "execution_time": 0.0,
                "input": {
                    "device_id": "mock_device_uuid",
                    "intended_config": "mock intended config",
                },
                "name": "perform_configuration_diff",
                "output": {
                    "approved": True,
                    "diff": "mock_diff",
                    "display": "Configuration Diff Approved by Test:\n```\nmock_diff\n```",
                },
                "rejecters": [],
                "requires_approval": True,
                "retry_count": 0,
                "retryable": True,
                "state": "COMPLETE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "PENDING_APPROVAL", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "APPROVED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "COMPLETE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["perform_configuration_diff"],
                "description": "Apply the configuration to the device.",
                "execution_time": 0.0,
                "input": {
                    "approved_diff": "mock_diff",
                    "commit_confirm": True,
                    "device_id": "mock_device_uuid",
                    "intended_config": "mock intended config",
                },
                "name": "apply_configuration",
                "output": {"display": "Configuration Applied Successfully."},
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
                "child_workflows": [ANY],
                "depends_on": ["perform_configuration_diff"],
                "description": "Run the backup workflow for the device..",
                "execution_time": 0.0,
                "input": {
                    "commit_id": "mock_commit_id",
                    "device_id": "mock_device_uuid",
                },
                "name": "perform_backup",
                "output": {"display": ANY},
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
        stages = await handle.query("stages")
        assert stages == expected_final_stages

        backup_workflow_id = stages[-1]["child_workflows"][0]
        backup_handle = client.get_workflow_handle(backup_workflow_id)
        backup_history = await backup_handle.fetch_history()
        scheduled_activity_types = {
            event.activity_task_scheduled_event_attributes.activity_type.name
            for event in backup_history.events
            if event.HasField("activity_task_scheduled_event_attributes")
        }
        assert "send_slack_message" not in scheduled_activity_types

        expected_backup_stages = [
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
                        "device_type": "sn4000",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "```\nmock running config\n```",
                    "running_config": "mock running config",
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
                    "intended_config_commit_id": "mock_commit_id",
                    "suppress_drift_notification": False,
                },
                "name": "check_drift",
                "output": {
                    "commit_id": "mock_commit_id",
                    "diff": "",
                    "display": "No drift detected between running and intended configuration.",
                    "has_drift": False,
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
                        "device_type": "sn4000",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "intended_config_commit_id": "mock_commit_id",
                    "running_config": "mock running config",
                    "trigger": "WORKFLOW",
                    "user": "nv-config-manager-temporal",
                    "user_domain": None,
                    "workflow_id": workflow_id,
                },
                "name": "persist_backup",
                "output": {
                    "changed": True,
                    "display": "Persisted new backup configuration:\n"
                    "\n"
                    "[Configuration "
                    "Backup](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=backup)\n"
                    "[Latest "
                    "Commit](https://config-manager.example.com/commits/mock_commit_id)\n",
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
        assert await backup_handle.query("stages") == expected_backup_stages

        expected_backup_input = {
            "device_id": "mock_device_uuid",
            "intended_config_commit_id": "mock_commit_id",
            "suppress_drift_notification": False,
            "terminate_on_failure": False,
            "trigger": "WORKFLOW",
            "user": "nv-config-manager-temporal",
            "user_domain": None,
            "workflow_id": handle.id,
        }
        assert await backup_handle.query("input") == expected_backup_input

        # Validate search attributes
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

        # Validate backup search attributes
        desc = await backup_handle.describe()
        search_attrs = desc.search_attributes
        for attr, val in expected_search_attributes.items():
            assert search_attrs[attr] == val
        assert mock_nats_client.return_value.publish.called == 1


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.backup.config_store_client")
@patch("nv_config_manager.temporal.ngc.activities.deploy.config_store_client")
@patch("nv_config_manager.temporal.ngc.activities.backup.create_dcim_client")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow_no_diff(
    _: Any,
    mock_nats_client: Any,
    _mock_nb_client: Any,
    _mock_gitlab_client_deploy: Any,
    _mock_gitlab_client_backup: Any,
    mock_cumulus_connection: Any,
    env: Any,
) -> None:
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[DeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = ""

        input_data = DeployInput(device_id="mock_device_uuid")

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            DeployWorkflow.run,
            input_data,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert not result

        expected_final_stages = [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the latest intended configuration from the Config Store.",
                "execution_time": 0.0,
                "input": {"device_id": "mock_device_uuid"},
                "name": "load_intended_configuration",
                "output": {
                    "commit_id": "mock_commit_id",
                    "display": "Loaded intended configuration from "
                    "[mock_device_uuid/startup.yaml](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_commit_id).",
                    "intended_config": "mock intended config",
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
                "approval_threshold": 1,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["load_intended_configuration"],
                "description": "Retrieve the configuration diff.",
                "execution_time": 0.0,
                "input": {
                    "device_id": "mock_device_uuid",
                    "intended_config": "mock intended config",
                },
                "name": "perform_configuration_diff",
                "output": {
                    "approved": False,
                    "diff": "",
                    "display": "No diff between the latest configuration render and "
                    "the configuration on the switch.",
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
                "depends_on": ["perform_configuration_diff"],
                "description": "Apply the configuration to the device.",
                "execution_time": None,
                "input": None,
                "name": "apply_configuration",
                "output": None,
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "UNREACHABLE",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "UNREACHABLE", "time": "1970-01-01T00:00:00+00:00"},
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [ANY],
                "depends_on": ["perform_configuration_diff"],
                "description": "Run the backup workflow for the device..",
                "execution_time": 0.0,
                "input": {
                    "commit_id": "mock_commit_id",
                    "device_id": "mock_device_uuid",
                },
                "name": "perform_backup",
                "output": {"display": ANY},
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

        stages = await handle.query("stages")
        assert stages == expected_final_stages
        assert mock_nats_client.return_value.publish.called


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.backup.config_store_client")
@patch("nv_config_manager.temporal.ngc.activities.deploy.config_store_client")
@patch("nv_config_manager.temporal.ngc.activities.backup.create_dcim_client")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_workflow_rejected_diff(
    _: Any,
    mock_nats_client: Any,
    _mock_nb_client: Any,
    _mock_gitlab_client_deploy: Any,
    _mock_gitlab_client_backup: Any,
    mock_cumulus_connection: Any,
    env: Any,
) -> None:
    """Test that when a diff is rejected, apply and backup stages are UNREACHABLE."""
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[DeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = (
            "some configuration diff"
        )

        input_data = DeployInput(device_id="mock_device_uuid")

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            DeployWorkflow.run,
            input_data,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        while await handle.query("pending_approval") is False:
            await asyncio.sleep(0.5)

        await handle.signal(
            "reject",
            {"stage_name": "perform_configuration_diff", "user": "TestRejecter"},
        )

        result = await handle.result()
        assert not result

        stages = await handle.query("stages")

        diff_stage = next(s for s in stages if s["name"] == "perform_configuration_diff")
        apply_stage = next(s for s in stages if s["name"] == "apply_configuration")
        backup_stage = next(s for s in stages if s["name"] == "perform_backup")

        assert diff_stage["state"] == "COMPLETE"
        assert diff_stage["output"]["approved"] is False
        assert len(diff_stage["rejecters"]) == 1
        assert diff_stage["rejecters"][0]["user"] == "TestRejecter"
        assert "Rejected" in diff_stage["output"]["display"]
        assert apply_stage["state"] == "UNREACHABLE"
        assert apply_stage["output"] is None
        assert backup_stage["state"] == "UNREACHABLE"
        assert backup_stage["output"] is None

        assert mock_nats_client.return_value.publish.called


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_tenant_deploy_workflow(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[TenantDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_partial_configuration,
            mock_load_intended_configuration,
            perform_candidate_diff,
            validate_config_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            mock_send_slack_message,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        # Mock diff with valid tenant config commands
        mock_tenant_diff = """nv unset interface swp3s0 ip vrf test-ryan-1
nv set nve vxlan enable on
nv set evpn vni 999 rd 172.28.0.2:999
nv set evpn vni 999 route-target export 11414:999
nv set evpn vni 999 route-target import 11414:999
nv set interface swp3s0 ip vrf test-ryan-2
nv set vrf test-ryan-2 evpn enable on
nv set vrf test-ryan-2 evpn vni 999
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast aggregate-route 172.16.128.64/26 summary-only on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast aggregate-route 172.17.128.64/26 summary-only on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast aggregate-route 172.18.128.64/26 summary-only on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast aggregate-route 172.19.128.64/26 summary-only on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast enable on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast redistribute connected enable on
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast redistribute connected route-map SERVER-REDISTRIBUTE-CONNECTED
nv set vrf test-ryan-2 router bgp address-family ipv4-unicast route-export to-evpn enable on
nv set vrf test-ryan-2 router bgp address-family l2vpn-evpn enable on
nv set vrf test-ryan-2 router bgp autonomous-system 4266990009
nv set vrf test-ryan-2 router bgp enable on
nv set vrf test-ryan-2 router bgp path-selection multipath aspath-ignore on
nv set vrf test-ryan-2 router bgp rd 172.28.0.2:999
nv set vrf test-ryan-2 router bgp router-id 172.28.0.2
"""
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = mock_tenant_diff

        input = TenantDeployInput(
            device="mock_device_uuid",
            tenant_config_commit_id="7",
            intended_config_commit_id="11",
        )

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            TenantDeployWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        # TenantDeployWorkflow doesn't require approval, so just wait for result
        result = await handle.result()
        assert result

        expected_final_stages = [
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Load the latest tenant configuration from config store.",
                "execution_time": 0.0,
                "input": {
                    "device": "mock_device_uuid",
                    "intended_config_commit_id": "11",
                    "tenant_config_commit_id": "7",
                    "use_full_intended_config": False,
                },
                "name": "load_tenant_configuration",
                "output": {
                    "commit_id": "7",
                    "device": ANY,
                    "display": "Loaded tenant configuration from "
                    "[mock_device_uuid/tenant.yaml](https://config-manager.example.com/device/mock_device_uuid/tenant.yaml?commit=7).",
                    "intended_config_commit_id": "11",
                    "deployment_config": "mock tenant config",
                    "partial": True,
                    "tenant_config": "mock tenant config",
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
                "depends_on": ["load_tenant_configuration"],
                "description": "Retrieve the configuration diff.",
                "execution_time": 0.0,
                "input": {
                    "device": ANY,
                    "deployment_config": "mock tenant config",
                    "partial": True,
                },
                "name": "perform_configuration_diff",
                "output": {
                    "diff": mock_tenant_diff,
                    "display": f"Configuration Diff\n```\n{mock_tenant_diff}\n```",
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
                "depends_on": ["perform_configuration_diff"],
                "description": "Validate the diff matches the tenant configuration.",
                "execution_time": 0.0,
                "input": {
                    "tenant_config": "mock tenant config",
                    "diff": mock_tenant_diff,
                },
                "name": "validate_configuration_diff",
                "output": ANY,
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
                "depends_on": ["validate_configuration_diff"],
                "description": "Apply the configuration to the device.",
                "execution_time": 0.0,
                "input": {
                    "device": ANY,
                    "deployment_config": "mock tenant config",
                    "diff": mock_tenant_diff,
                    "partial": True,
                },
                "name": "apply_configuration",
                "output": {"display": "Configuration Applied Successfully."},
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
                "child_workflows": [ANY],
                "depends_on": ["perform_configuration_diff"],
                "description": "Run the backup workflow for the device..",
                "execution_time": 0.0,
                "input": {"device_id": "mock_device_uuid"},
                "name": "perform_backup",
                "output": {"display": ANY},
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
        stages = await handle.query("stages")
        assert stages == expected_final_stages

        backup_workflow_id = stages[-1]["child_workflows"][0]
        backup_handle = client.get_workflow_handle(backup_workflow_id)

        expected_backup_stages = [
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
                        "device_type": "sn4000",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "display": "```\nmock running config\n```",
                    "running_config": "mock running config",
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
                    "suppress_drift_notification": True,
                },
                "name": "check_drift",
                "output": {
                    "commit_id": "mock_commit_id",
                    "diff": mock_tenant_diff,
                    "display": "Loaded intended configuration from "
                    "[mock_device_uuid/startup.yaml](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=intended&commit=mock_commit_id).\n"
                    f"Configuration Drift Detected:\n```\n{mock_tenant_diff}\n```",
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
                        "device_type": "sn4000",
                        "host": "10.0.0.1",
                        "id": "mock_device_uuid",
                        "intended_config_path": "mock_device_uuid/startup.yaml",
                        "name": "mock_device",
                        "platform": "cumulus-linux",
                        "position": None,
                        "primary_ip4": "10.0.0.1",
                        "primary_ip6": None,
                        "rack": None,
                        "render_enabled": False,
                        "role": "mock_role",
                        "site": "SITEA",
                        "tenant_config_file": "tenant.yaml",
                        "tenant_config_path": "mock_device_uuid/tenant.yaml",
                        "ztp_enabled": False,
                        "intent": None,
                    },
                    "intended_config_commit_id": None,
                    "running_config": "mock running config",
                    "trigger": "WORKFLOW",
                    "user": "nv-config-manager-temporal",
                    "user_domain": None,
                    "workflow_id": workflow_id,
                },
                "name": "persist_backup",
                "output": {
                    "changed": True,
                    "display": "Persisted new backup configuration:\n"
                    "\n"
                    "[Configuration "
                    "Backup](https://config-manager.example.com/device/mock_device_uuid/startup.yaml?file_type=backup)\n"
                    "[Latest "
                    "Commit](https://config-manager.example.com/commits/mock_commit_id)\n",
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
        assert await backup_handle.query("stages") == expected_backup_stages

        expected_backup_input = {
            "device_id": "mock_device_uuid",
            "intended_config_commit_id": None,
            "suppress_drift_notification": True,
            "terminate_on_failure": False,
            "trigger": "WORKFLOW",
            "user": "nv-config-manager-temporal",
            "user_domain": None,
            "workflow_id": handle.id,
        }
        assert await backup_handle.query("input") == expected_backup_input

        # Validate search attributes
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

        # Validate backup search attributes
        desc = await backup_handle.describe()
        search_attrs = desc.search_attributes
        for attr, val in expected_search_attributes.items():
            assert search_attrs[attr] == val
        assert mock_nats_client.return_value.publish.called == 1


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_tenant_deploy_uses_full_intended_config_for_removals(
    _,
    _mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Use replacement semantics so absent tenant settings are removed."""
    task_queue_name = str(uuid.uuid4())
    mock_diff = """nv unset interface swp1 ip vrf test-vrf
nv unset vrf test-vrf router bgp enable on
"""
    mock_cumulus_connection.return_value.get_running_configuration.return_value = (
        "mock running config"
    )
    mock_cumulus_connection.return_value.perform_candidate_diff.return_value = mock_diff

    async with Worker(
        env.client,
        task_queue=task_queue_name,
        workflows=[TenantDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_empty_tenant_configuration,
            mock_load_intended_configuration,
            perform_candidate_diff,
            validate_config_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            mock_send_slack_message,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        handle = await env.client.start_workflow(
            TenantDeployWorkflow.run,
            TenantDeployInput(
                device="mock_device_uuid",
                tenant_config_commit_id="7",
                intended_config_commit_id="11",
                use_full_intended_config=True,
            ),
            id=str(uuid.uuid4()),
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        assert await handle.result() is True
        stages = {stage["name"]: stage for stage in await handle.query("stages")}

    load_stage = stages["load_tenant_configuration"]
    assert load_stage["output"]["tenant_config"] == "- set: {}\n"
    assert load_stage["output"]["deployment_config"] == "mock intended config"
    assert load_stage["output"]["partial"] is False
    assert stages["perform_configuration_diff"]["input"]["partial"] is False
    assert stages["apply_configuration"]["input"]["partial"] is False
    mock_cumulus_connection.return_value.commit_candidate_config.assert_called_once_with(
        "mock intended config",
        mock_diff,
        commit_confirm=True,
        partial=False,
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_apply_config_with_ignore_fail_and_retry(
    _: Any,
    _mock_nats_client: Any,
    mock_cumulus_connection: Any,
    env: Any,
) -> None:
    """Test that ConfigApplyFailureException displays error message and fails workflow."""
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client

    revision_id = "test-revision-abc123"

    error_message = (
        "Failure during apply. Ignore?\n\n"
        "[ERROR] systemctl: Unable to reload-or-restart services (frr):\n"
        "  Job for frr.service failed.\n\n"
        "MANUAL INTERVENTION REQUIRED:\n"
        f"You can try manually applying the configuration on "
        f"the switch:\n\n"
        f"  nv config apply {revision_id}\n\n"
        f"If successful, this workflow stage will "
        f"automatically retry and complete. If the manual "
        f"apply also fails, further investigation of the "
        f"configuration or device state is required."
    )

    def mock_commit_candidate_config(
        _new_config: str,
        _approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Raise ConfigApplyFailureException with detailed error message."""
        raise ConfigApplyFailureException(error_message, non_retryable=True)

    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[DeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_intended_configuration,
            perform_candidate_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = "mock_diff"
        mock_cumulus_connection.return_value.commit_candidate_config.side_effect = (
            mock_commit_candidate_config
        )

        input_data = DeployInput(device_id="mock_device_uuid")

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            DeployWorkflow.run,
            input_data,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=30),
        )

        while await handle.query("pending_approval") is False:
            await asyncio.sleep(0.5)

        await handle.signal(
            "approve",
            {"stage_name": "perform_configuration_diff", "user": "Test"},
        )

        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

        assert exc_info.value is not None
        workflow_desc = await handle.describe()
        assert workflow_desc.status.name == "FAILED"

        stages = await handle.query("stages")
        apply_stage = next(s for s in stages if s["name"] == "apply_configuration")

        assert apply_stage["state"] == "FAILED"
        assert apply_stage["output"]["display"] == error_message


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_tenant_deploy_workflow_invalid_config(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[TenantDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_partial_configuration,
            mock_load_intended_configuration,
            perform_candidate_diff,
            validate_config_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        mock_invalid_diff = """nv set vrf default router bgp enable on
nv set interface swp1 ip address 10.0.0.1/24
"""
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = mock_invalid_diff

        input = TenantDeployInput(
            device="mock_device_uuid",
            tenant_config_commit_id="7",
            intended_config_commit_id="11",
        )

        workflow_id = str(uuid.uuid4())

        handle: WorkflowHandle = await env.client.start_workflow(
            TenantDeployWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=3),
        )

        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

        assert "timed out" in str(exc_info.value.cause).lower()
        stages = await handle.query("stages")
        assert stages[2]["state"] == "FAILED"
        error = (
            "Invalid diff: Validation failed: 2 lines not allowed: "
            "['nv set vrf default router bgp enable on', "
            "'nv set interface swp1 ip address 10.0.0.1/24']"
        )
        assert error in stages[2]["traceback"]


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_tenant_deploy_workflow_newer_commit_allowed(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test tenant deploy when commit is newer but all lines are allowed."""
    _newer_commit_mock_state["use_newer_commit"] = True
    _newer_commit_mock_state["newer_commit_allowed"] = True

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[TenantDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_partial_configuration,
            mock_load_intended_configuration,
            perform_candidate_diff,
            validate_config_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            mock_send_slack_message,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        # Mock diff with valid tenant config commands
        mock_tenant_diff = """nv set vrf test-vrf router bgp router-id 172.28.0.2
nv set vrf test-vrf router bgp autonomous-system 4266990009
nv set interface swp1 ip vrf test-vrf
nv set interface swp2 ip vrf test-vrf
"""
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = mock_tenant_diff

        input = TenantDeployInput(
            device="mock_device_uuid",
            tenant_config_commit_id="7",
            intended_config_commit_id="11",
        )

        workflow_id = str(uuid.uuid4())
        handle: WorkflowHandle = await env.client.start_workflow(
            TenantDeployWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(minutes=10),
        )

        result = await handle.result()
        assert result

        stages = await handle.query("stages")
        assert stages[-1]["state"] == "COMPLETE"
        # Verify it loaded the newer commit (7)
        load_stage = next((s for s in stages if s["name"] == "load_tenant_configuration"), None)
        assert load_stage is not None
        assert load_stage["output"]["commit_id"] == "7"
        assert load_stage["output"]["intended_config_commit_id"] == "11"
        assert stages[-1]["input"] == {"device_id": "mock_device_uuid"}

        backup_workflow_id = stages[-1]["child_workflows"][0]
        backup_handle = client.get_workflow_handle(backup_workflow_id)
        backup_input = await backup_handle.query("input")
        assert backup_input["intended_config_commit_id"] is None
        assert backup_input["suppress_drift_notification"] is True

    # Reset state for other tests
    _newer_commit_mock_state["use_newer_commit"] = False
    _newer_commit_mock_state["newer_commit_allowed"] = True


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.client.device.CumulusConnection")
@patch("nv_config_manager.temporal.ngc.activities.nats.NatsProducer", autospec=True)
@patch("nv_config_manager_workflows.stage.mixin.workflow.time", return_value=float(0))
async def test_execute_tenant_deploy_workflow_newer_commit_disallowed(
    _,
    mock_nats_client,
    mock_cumulus_connection,
    env,
):
    """Test tenant deploy when commit is newer but has disallowed lines."""
    _newer_commit_mock_state["use_newer_commit"] = True
    _newer_commit_mock_state["newer_commit_allowed"] = False

    task_queue_name = str(uuid.uuid4())
    client: Client = env.client
    async with Worker(
        client,
        task_queue=task_queue_name,
        workflows=[TenantDeployWorkflow, BackupWorkflow],
        activities=[
            mock_get_network_device,
            mock_load_partial_configuration,
            mock_load_intended_configuration,
            perform_candidate_diff,
            validate_config_diff,
            apply_approved_configuration,
            load_running_configuration,
            mock_persist_config_backup,
            mock_record_backup_config_manager_plugin,
            mock_get_ui_base_url,
            publish_nats,
        ],
        activity_executor=ThreadPoolExecutor(5),
    ):
        # Setup mocking
        mock_cumulus_connection.return_value.get_running_configuration.return_value = (
            "mock running config"
        )
        # Mock diff with disallowed line
        mock_tenant_diff = """nv set vrf test-vrf router bgp router-id 172.28.0.2
nv set vrf test-vrf router bgp autonomous-system 4266990009
nv set interface swp1 ip vrf test-vrf
nv set interface swp2 ip vrf test-vrf
nv set system hostname disallowed-change
"""
        mock_cumulus_connection.return_value.perform_candidate_diff.return_value = mock_tenant_diff

        input = TenantDeployInput(
            device="mock_device_uuid",
            tenant_config_commit_id="7",
            intended_config_commit_id="11",
        )

        workflow_id = str(uuid.uuid4())

        handle: WorkflowHandle = await env.client.start_workflow(
            TenantDeployWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue_name,
            run_timeout=timedelta(seconds=10),
        )

        # Wait a bit for workflow to progress, then check stages
        await asyncio.sleep(2)
        stages = await handle.query("stages")
        load_stage = next((s for s in stages if s["name"] == "load_tenant_configuration"), None)
        assert load_stage is not None
        assert load_stage["output"]["commit_id"] == "7"
        assert load_stage["output"]["intended_config_commit_id"] == "11"
        validate_stage = next(
            (s for s in stages if s["name"] == "validate_configuration_diff"), None
        )

        # If validation stage exists and failed, that's what we expect
        if validate_stage and validate_stage["state"] == "FAILED":
            assert validate_stage["state"] == "FAILED"
            # Check traceback for validation error
            if validate_stage.get("traceback"):
                assert (
                    "Invalid diff" in validate_stage["traceback"]
                    or "Validation failed" in validate_stage["traceback"]
                    or "disallowed" in validate_stage["traceback"].lower()
                )
        else:
            # Try to get result - it should fail
            try:
                await handle.result()
                assert False, "Workflow should have failed"
            except WorkflowFailureError as exc:
                error_msg = str(exc.cause) if hasattr(exc, "cause") else str(exc)
                # Check error message
                assert (
                    "Invalid diff" in error_msg
                    or "Validation failed" in error_msg
                    or "disallowed" in error_msg.lower()
                )

    # Reset state for other tests
    _newer_commit_mock_state["use_newer_commit"] = False
    _newer_commit_mock_state["newer_commit_allowed"] = True
