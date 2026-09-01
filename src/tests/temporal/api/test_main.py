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
from configparser import ConfigParser
from datetime import datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle

from nv_config_manager.dcim import DCIMSelection, DeviceMetadata
from nv_config_manager.temporal.api.links import temporal_ui_workflow_href
from nv_config_manager.temporal.api.main import app
from nv_config_manager.temporal.api.workflow_v1 import (
    WorkflowDetailResponse,
    WorkflowSummaryResponse,
    cache_workflow_input,
    signal_workflow,
    start_workflow,
)
from nv_config_manager.temporal.common.mixins.stage import ReviewSignalInput, Stage, StageMixin
from nv_config_manager.temporal.common.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.common.workflow_references import LocationReference
from nv_config_manager.temporal.hello_world.workflows.hello_world_workflow import (
    HelloWorld,
    HelloWorldInput,
)
from nv_config_manager.temporal.ngc.workflows.deploy import DeployInput, DeployWorkflow

TEMPORAL_UI_WORKFLOW_BASE = "https://temporal-ui.example.com/namespaces/default/workflows"


class LocationWorkflowInput(BaseModel):
    """Minimal location-based input for workflow start tests."""

    site: LocationReference


def test_healthcheck():
    """Verify healthcheck."""
    client = TestClient(app)
    rsp = client.get("/healthcheck")
    assert rsp.status_code == 200
    assert rsp.json() == "OK"


def test_openapi_operation_tags_are_unique():
    """Verify routes do not duplicate tags inherited from their parent router."""
    schema = app.openapi()

    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in {"delete", "get", "head", "options", "patch", "post", "put", "trace"}:
                continue

            tags = operation.get("tags", [])
            assert len(tags) == len(set(tags)), (
                f"{method.upper()} {path} has duplicate tags: {tags}"
            )


def test_batch_deploy_child_workflow_is_not_exposed_by_api():
    """Parent-generated device connection data must not have an external API path."""
    assert "/v1/workflow/ngc/batch_deploy" not in app.openapi()["paths"]


def test_metrics():
    """Verify /metrics returns Prometheus metrics without auth."""
    client = TestClient(app)
    rsp = client.get("/metrics")
    assert rsp.status_code == 200
    assert "nv_config_manager_temporal_api" in rsp.text


def test_temporal_ui_workflow_href_uses_ini(monkeypatch):
    """Verify Workflow API href generation reads the INI, not TEMPORAL_UI."""
    monkeypatch.setenv("TEMPORAL_UI", "http://localhost:8080")
    config = ConfigParser()
    config.read_dict({"temporal": {"temporal_ui_url": "https://temporal-ui.example.com"}})

    assert (
        temporal_ui_workflow_href("workflow-id", config=config)
        == f"{TEMPORAL_UI_WORKFLOW_BASE}/workflow-id"
    )


def test_temporal_ui_workflow_href_uses_configured_namespace():
    """Verify Workflow API href generation uses the configured Temporal namespace."""
    config = ConfigParser()
    config.read_dict(
        {
            "temporal": {
                "temporal_ui_url": "https://temporal-ui.example.com",
                "namespace": "network automation",
            }
        }
    )

    assert (
        temporal_ui_workflow_href("workflow-id", config=config)
        == "https://temporal-ui.example.com/namespaces/network%20automation/workflows/workflow-id"
    )


def test_temporal_ui_workflow_href_returns_empty_without_ini_url():
    """Verify missing Temporal UI URL does not raise after workflow start."""
    config = ConfigParser()
    config.read_dict({"temporal": {}})

    assert temporal_ui_workflow_href("workflow-id", config=config) == ""


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.cache_workflow_input")
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.uuid4", return_value="mockuuid")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_start_workflow(mock_rbac_config, mock_uuid, mock_connect, mock_cache_input):
    """Verify Start Workflow."""
    handle = MagicMock()
    handle.id = "mockuuid"

    mock_connect.return_value.start_workflow.return_value = handle

    # Mock RBAC config for HelloWorld workflow
    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_workflow_roles.return_value = {
        "read_roles": {"all"},
        "execute_roles": {"all"},
    }
    mock_rbac_config.return_value = mock_rbac_instance

    request = MagicMock()
    request.state.user = "testuser"
    request.state.roles = {"all"}
    body = HelloWorldInput(name="test")
    result = await start_workflow(request, HelloWorld, body)
    assert result == "mockuuid"

    mock_connect.return_value.start_workflow.assert_called_with(
        HelloWorld.run,
        body,
        id="mockuuid",
        task_queue="default-task-queue",
        search_attributes={
            "ExecuteRoles": ["all"],
            FAILED_STAGE_SEARCH_ATTRIBUTE: [False],
            PENDING_APPROVAL_SEARCH_ATTRIBUTE: [False],
            "ReadRoles": ["all"],
            "User": ["testuser"],
        },
    )
    mock_cache_input.assert_awaited_once_with("mockuuid", body)

    # Location UUIDs remain unchanged while the search attribute uses the canonical name.
    mock_connect.reset_mock()
    mock_cache_input.reset_mock()
    mock_rbac_instance.get_workflow_roles.return_value = {
        "read_roles": {"all"},
        "execute_roles": {"all"},
    }
    request.state.roles = {"all"}
    location_id = "b6f4972a-c6ab-4be1-96ac-72f4efc4f328"
    location_body = LocationWorkflowInput(site=location_id)
    location_client = MagicMock()
    location_client.__aenter__.return_value = location_client
    location_client.is_valid_device_id.return_value = True
    location_client.is_valid_location_id.return_value = True
    location_client.get_location_metadata = AsyncMock(
        return_value=DCIMSelection(id=location_id, name="SJC01")
    )
    with patch(
        "nv_config_manager.temporal.api.workflow_submission.create_dcim_client",
        return_value=location_client,
    ):
        result = await start_workflow(request, HelloWorld, location_body)

    assert result == "mockuuid"
    mock_connect.return_value.start_workflow.assert_called_with(
        HelloWorld.run,
        location_body,
        id="mockuuid",
        task_queue="default-task-queue",
        search_attributes={
            "ExecuteRoles": ["all"],
            FAILED_STAGE_SEARCH_ATTRIBUTE: [False],
            PENDING_APPROVAL_SEARCH_ATTRIBUTE: [False],
            "ReadRoles": ["all"],
            SITE_SEARCH_ATTRIBUTE: ["SJC01"],
            "User": ["testuser"],
        },
    )
    mock_cache_input.assert_awaited_once_with("mockuuid", location_body)
    assert location_body.site == location_id
    location_client.get_location_metadata.assert_awaited_once_with(location_id)

    # Invalid references fail before a Temporal workflow is created.
    mock_connect.reset_mock()
    location_client.get_location_metadata.reset_mock()
    location_client.get_location_metadata.return_value = None
    missing_location_id = str(uuid4())
    location_body = LocationWorkflowInput(site=missing_location_id)
    with (
        patch(
            "nv_config_manager.temporal.api.workflow_submission.create_dcim_client",
            return_value=location_client,
        ),
        pytest.raises(HTTPException, match="Unknown location") as exc_info,
    ):
        await start_workflow(request, HelloWorld, location_body)

    assert exc_info.value.status_code == 422
    mock_connect.return_value.start_workflow.assert_not_called()
    location_client.get_location_metadata.assert_awaited_once_with(missing_location_id)

    # Test that more strict workflow permissions are respected
    mock_connect.reset_mock()
    mock_cache_input.reset_mock()
    # Mock RBAC config for DeployWorkflow
    mock_rbac_instance.get_workflow_roles.return_value = {
        "read_roles": {"ngc-cfa", "ngc-gni"},
        "execute_roles": {"ngc-cfa", "ngc-gni"},
    }
    device_id = "910b85f8-e83c-48ad-9bbd-12b15e97a2d4"
    body = DeployInput(device_id=device_id)
    with pytest.raises(HTTPException) as e:
        result = await start_workflow(request, DeployWorkflow, body)
        assert e.value.status_code == 403
        mock_connect.return_value.start_workflow.assert_not_called()

    # Now modify headers to include an allowed role and try again
    request.state.roles = {"ngc-gni"}
    device_client = MagicMock()
    device_client.__aenter__.return_value = device_client
    device_client.is_valid_device_id.return_value = True
    device_client.get_device_metadata = AsyncMock(
        return_value=DeviceMetadata(
            device_id=device_id,
            name="LEAF01",
            role="Leaf Switch",
            platform="Cumulus Linux",
            site="SJC01",
        )
    )
    with patch(
        "nv_config_manager.temporal.api.workflow_submission.create_dcim_client",
        return_value=device_client,
    ):
        result = await start_workflow(request, DeployWorkflow, body)
    assert result == "mockuuid"
    mock_connect.return_value.start_workflow.assert_called_with(
        DeployWorkflow.run,
        body,
        id="mockuuid",
        task_queue="default-task-queue",
        search_attributes={
            "ExecuteRoles": ["ngc-cfa", "ngc-gni"],
            DEVICE_ID_SEARCH_ATTRIBUTE: [device_id],
            DEVICE_NAME_SEARCH_ATTRIBUTE: ["LEAF01"],
            DEVICE_PLATFORM_SEARCH_ATTRIBUTE: ["cumulus-linux"],
            DEVICE_ROLE_SEARCH_ATTRIBUTE: ["leaf-switch"],
            FAILED_STAGE_SEARCH_ATTRIBUTE: [False],
            PENDING_APPROVAL_SEARCH_ATTRIBUTE: [False],
            "ReadRoles": ["ngc-cfa", "ngc-gni"],
            SITE_SEARCH_ATTRIBUTE: ["SJC01"],
            "User": ["testuser"],
        },
    )
    mock_cache_input.assert_awaited_once_with("mockuuid", body)
    device_client.get_device_metadata.assert_awaited_once_with(device_id)

    # Well-formed UUIDs must also resolve to an existing Nautobot device.
    mock_connect.reset_mock()
    device_client.get_device_metadata.reset_mock()
    device_client.get_device_metadata.return_value = None
    with (
        patch(
            "nv_config_manager.temporal.api.workflow_submission.create_dcim_client",
            return_value=device_client,
        ),
        pytest.raises(HTTPException, match="Unknown device") as exc_info,
    ):
        await start_workflow(request, DeployWorkflow, body)
    assert exc_info.value.status_code == 422
    mock_connect.return_value.start_workflow.assert_not_called()

    # Device identifier shape is validated by the selected DCIM provider.
    mock_connect.reset_mock()
    device_client.is_valid_device_id.return_value = False
    with (
        patch(
            "nv_config_manager.temporal.api.workflow_submission.create_dcim_client",
            return_value=device_client,
        ),
        pytest.raises(HTTPException, match="Invalid device identifier") as exc_info,
    ):
        await start_workflow(request, DeployWorkflow, DeployInput(device_id="LEAF01"))
    assert exc_info.value.status_code == 422
    mock_connect.return_value.start_workflow.assert_not_called()


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_cache_workflow_input(mock_redis, mock_load_config):
    """Verify workflow input is cached in list-safe JSON form."""
    cache = mock_redis.from_config.return_value
    cache.cache_query = AsyncMock()
    body = HelloWorldInput(name="test")

    await cache_workflow_input("workflow-id", body)

    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)
    cache.cache_query.assert_awaited_once_with("workflow-id", "input", {"name": "test"})


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.uuid4", return_value="mockuuid")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_hello_world_workflow(mock_rbac_config, mock_uuid, mock_get_client, mocker):
    """Verify HelloWorld Workflow API."""
    audit_logger = mocker.patch("nv_config_manager.temporal.api.audit.logger")
    # Mock Temporal client
    handle = MagicMock()
    handle.id = "mockuuid"
    mock_get_client.return_value.start_workflow.return_value = handle

    # Mock RBAC config for HelloWorld workflow
    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_workflow_roles.return_value = {
        "read_roles": {"all"},
        "execute_roles": {"all"},
    }
    mock_rbac_config.return_value = mock_rbac_instance

    client = TestClient(app)
    rsp = client.post("/v1/workflow/hello_world", json={"name": "test"})
    assert rsp.json() == {
        "id": "mockuuid",
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mockuuid",
    }
    audit_fields = audit_logger.info.call_args.kwargs["extra"]
    assert audit_fields["action"] == "hello_world"
    assert audit_fields["outcome"] == "success"
    assert audit_fields["workflow_id"] == "mockuuid"
    assert audit_fields["workflow_type"] == "HelloWorld"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.uuid4", return_value="mockuuid")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_hello_world_approval_workflow(mock_rbac_config, mock_uuid, mock_get_client):
    """Verify HelloWorld Workflow API."""
    # Mock Temporal client
    handle = MagicMock()
    handle.id = "mockuuid"
    mock_get_client.return_value.start_workflow.return_value = handle

    # Mock RBAC config for HelloWorldApproval workflow
    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_workflow_roles.return_value = {
        "read_roles": {"all"},
        "execute_roles": {"all"},
    }
    mock_rbac_config.return_value = mock_rbac_instance

    client = TestClient(app)
    rsp = client.post("/v1/workflow/hello_world_approval", json={"name": "test"})
    assert rsp.json() == {
        "id": "mockuuid",
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mockuuid",
    }


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.signal_workflow")
async def test_approve(mock_signal):
    """Verify HelloWorld Workflow API."""
    workflow_id = str(uuid4())

    client = TestClient(app)
    rsp = client.post(f"/v1/workflow/{workflow_id}/approve/prompt")
    assert rsp.json() == {
        "id": workflow_id,
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/{workflow_id}",
    }

    mock_signal.assert_called_with(
        ANY,
        workflow_id,
        "approve",
        ReviewSignalInput(stage_name="prompt", user="anonymous"),
    )


@pytest.mark.asyncio
async def test_approve_emits_identity_aware_audit_log(mocker):
    """Identity-aware audit fields guard the required middleware ordering."""
    mocker.patch("nv_config_manager.temporal.api.workflow_v1.signal_workflow")
    audit_logger = mocker.patch("nv_config_manager.temporal.api.audit.logger")
    workflow_id = str(uuid4())

    rsp = TestClient(app).post(
        f"/v1/workflow/{workflow_id}/approve/prompt",
        headers={
            "X-Auth-Request-Email": "operator@example.com",
            "X-Auth-Request-User": "operator",
            "X-Auth-Request-Groups": "nvcm-network",
        },
    )

    assert rsp.status_code == 200
    audit_fields = audit_logger.info.call_args.kwargs["extra"]
    assert audit_fields["action"] == "approve"
    assert audit_fields["actor"] == "operator"
    assert audit_fields["roles"] == ["all", "nvcm-network"]
    assert audit_fields["source"] == "sso"
    assert audit_fields["workflow_id"] == workflow_id
    assert audit_fields["stage_name"] == "prompt"


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.signal_workflow")
async def test_reject(mock_signal):
    """Verify HelloWorld Workflow API."""
    workflow_id = str(uuid4())

    client = TestClient(app)
    rsp = client.post(f"/v1/workflow/{workflow_id}/reject/prompt")
    assert rsp.json() == {
        "id": workflow_id,
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/{workflow_id}",
    }

    mock_signal.assert_called_with(
        ANY,
        workflow_id,
        "reject",
        ReviewSignalInput(stage_name="prompt", user="anonymous"),
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.signal_workflow")
async def test_retry(mock_signal):
    """Test the retry signal API."""
    workflow_id = str(uuid4())

    client = TestClient(app)
    rsp = client.post(f"/v1/workflow/{workflow_id}/retry/prompt")
    assert rsp.json() == {
        "id": workflow_id,
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/{workflow_id}",
    }

    mock_signal.assert_called_with(ANY, workflow_id, "retry", "prompt")


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_terminate_success(mock_client, mock_redis):
    """Test the terminate workflow API when workflow is running and user is authorized."""
    mock_redis.from_config.return_value.delete_cached_query = AsyncMock()
    workflow_id = str(uuid4())
    mock_handle = MagicMock()

    async def mock_describe():
        mock_description = MagicMock()
        mock_description.status = WorkflowExecutionStatus.RUNNING
        mock_description.search_attributes = {
            "User": ["test"],
            "ReadRoles": ["ngc-cfa"],
            "ExecuteRoles": ["ngc-cfa"],
        }
        return mock_description

    mock_handle.describe = mock_describe
    mock_handle.terminate = AsyncMock(return_value=None)
    mock_handle.id = workflow_id

    mock_client_instance = MagicMock()
    mock_client_instance.get_workflow_handle.return_value = mock_handle
    mock_client.return_value = mock_client_instance

    client = TestClient(app)
    rsp = client.post(
        f"/v1/workflow/{workflow_id}/terminate",
        headers={"X-Auth-Request-Email": "test@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    assert rsp.status_code == 200
    assert rsp.json() == {
        "id": workflow_id,
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/{workflow_id}",
    }
    mock_handle.terminate.assert_called_once()
    mock_redis.from_config.return_value.delete_cached_query.assert_any_await(
        workflow_id, "pending_approval"
    )
    mock_redis.from_config.return_value.delete_cached_query.assert_any_await(
        workflow_id, "compressed_stages"
    )


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_terminate_workflow_not_found(mock_client):
    """Test terminate returns 404 when workflow does not exist (via is_authorized)."""
    from temporalio.service import RPCError, RPCStatusCode

    workflow_id = "nonexistent-workflow-id"
    mock_handle = MagicMock()

    async def mock_describe():
        raise RPCError("sql: no rows in result set", RPCStatusCode.NOT_FOUND, b"")

    mock_handle.describe = mock_describe
    mock_handle.id = workflow_id

    mock_client_instance = MagicMock()
    mock_client_instance.get_workflow_handle.return_value = mock_handle
    mock_client.return_value = mock_client_instance

    client = TestClient(app)
    rsp = client.post(
        f"/v1/workflow/{workflow_id}/terminate",
        headers={"X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow with ID '{workflow_id}' not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_in_detail",
    [
        (WorkflowExecutionStatus.COMPLETED, "COMPLETED"),
        (None, "UNKNOWN"),
    ],
)
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_terminate_workflow_not_running(mock_client, status, expected_in_detail):
    """Test terminate returns 400 when workflow is not running."""
    workflow_id = str(uuid4())
    mock_handle = MagicMock()

    async def mock_describe():
        mock_description = MagicMock()
        mock_description.status = status
        mock_description.search_attributes = {
            "User": ["test"],
            "ReadRoles": ["ngc-cfa"],
            "ExecuteRoles": ["ngc-cfa"],
        }
        return mock_description

    mock_handle.describe = mock_describe
    mock_handle.id = workflow_id

    mock_client_instance = MagicMock()
    mock_client_instance.get_workflow_handle.return_value = mock_handle
    mock_client.return_value = mock_client_instance

    client = TestClient(app)
    rsp = client.post(
        f"/v1/workflow/{workflow_id}/terminate",
        headers={"X-Auth-Request-Email": "test@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    assert rsp.status_code == 400
    assert "Workflow is not running" in rsp.json()["detail"]
    assert expected_in_detail in rsp.json()["detail"]
    mock_handle.terminate.assert_not_called()


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_terminate_forbidden(mock_client):
    """Test terminate returns 403 when user is not authorized."""
    workflow_id = str(uuid4())
    mock_handle = MagicMock()

    async def mock_describe():
        mock_description = MagicMock()
        mock_description.status = WorkflowExecutionStatus.RUNNING
        mock_description.search_attributes = {
            "User": ["other-user"],
            "ReadRoles": ["ngc-cfa"],
            "ExecuteRoles": ["ngc-cfa"],
        }
        return mock_description

    mock_handle.describe = mock_describe
    mock_handle.id = workflow_id

    mock_client_instance = MagicMock()
    mock_client_instance.get_workflow_handle.return_value = mock_handle
    mock_client.return_value = mock_client_instance

    client = TestClient(app)
    rsp = client.post(
        f"/v1/workflow/{workflow_id}/terminate",
        headers={"X-Auth-Request-Email": "user@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-gni"},
    )
    assert rsp.status_code == 403
    assert rsp.json() == {"detail": "Forbidden"}
    mock_handle.terminate.assert_not_called()


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_workflow_detail(mock_redis, mock_client):
    """Verify HelloWorld Workflow API."""
    # Mock to always cache miss
    mock_redis.return_value.get_cached_result.return_value = None
    mock_redis.from_config.return_value.get_cached_query = AsyncMock(return_value=None)
    mock_redis.from_config.return_value.cache_query = AsyncMock()

    class MockHandle(WorkflowHandle):
        _id = "mockid"

        def __init__(self, *args, **kwargs):
            pass

        async def describe(self):
            mock_description = MagicMock()
            mock_description.search_attributes = {
                "User": ["test"],
                "ReadRoles": ["ngc-cfa"],
                "ExecuteRoles": ["ngc-cfa"],
            }
            mock_description.status = WorkflowExecutionStatus.RUNNING
            mock_description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
            mock_description.close_time = None
            mock_description.workflow_type = "HelloWorldApproval"
            return mock_description

        async def query(self, name: str):
            if name == "pending_approval":
                return True
            if name == "input":
                return {"user": "test"}
            if name == "compressed_stages":
                stages = [
                    {
                        "approval_threshold": 1,
                        "approvers": [],
                        "child_workflows": [],
                        "depends_on": [],
                        "description": "Ask the user if they want to be greeted",
                        "execution_time": None,
                        "input": None,
                        "name": "prompt",
                        "output": {
                            "approved": False,
                            "display": "Would you like to be greeted?",
                        },
                        "rejecters": [],
                        "requires_approval": True,
                        "retry_count": 0,
                        "retryable": True,
                        "state": "PENDING_APPROVAL",
                        "state_history": [
                            {
                                "state": "NOT_STARTED",
                                "time": "1970-01-01T00:00:00+00:00",
                            },
                            {
                                "state": "IN_PROGRESS",
                                "time": "1970-01-01T00:00:00+00:00",
                            },
                            {
                                "state": "PENDING_APPROVAL",
                                "time": "1970-01-01T00:00:00+00:00",
                            },
                        ],
                        "traceback": None,
                    },
                    {
                        "approval_threshold": 0,
                        "approvers": [],
                        "child_workflows": [],
                        "depends_on": ["prompt"],
                        "description": "Greet the user.",
                        "execution_time": None,
                        "input": None,
                        "name": "greet",
                        "output": None,
                        "rejecters": [],
                        "requires_approval": False,
                        "retry_count": 0,
                        "retryable": True,
                        "state": "NOT_STARTED",
                        "state_history": [
                            {
                                "state": "NOT_STARTED",
                                "time": "1970-01-01T00:00:00+00:00",
                            }
                        ],
                        "traceback": None,
                    },
                    {
                        "approval_threshold": 0,
                        "approvers": [],
                        "child_workflows": [],
                        "depends_on": ["prompt"],
                        "description": "Say goodbye to the user",
                        "execution_time": None,
                        "input": None,
                        "name": "goodbye",
                        "output": None,
                        "rejecters": [],
                        "requires_approval": False,
                        "retry_count": 0,
                        "retryable": True,
                        "state": "NOT_STARTED",
                        "state_history": [
                            {
                                "state": "NOT_STARTED",
                                "time": "1970-01-01T00:00:00+00:00",
                            }
                        ],
                        "traceback": None,
                    },
                ]
                stages = [Stage(**stage) for stage in stages]
                return StageMixin.compress_stages(stages)

    mock_client.return_value.get_workflow_handle = MockHandle
    workflow_id = str(uuid4())

    client = TestClient(app)
    rsp = client.get(f"/v1/workflow/{workflow_id}")
    assert rsp.status_code == 403
    assert rsp.json() == {"detail": "Forbidden"}

    rsp = client.get(
        f"/v1/workflow/{workflow_id}",
        headers={"X-Auth-Request-Email": "test@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    assert rsp.status_code == 200
    assert rsp.json() == {
        "id": "mockid",
        "workflow_type": "HelloWorldApproval",
        "workflow_input": {"user": "test"},
        "started_by": "test",
        "start_time": "1970-01-01T00:00:00Z",
        "close_time": None,
        "status": "RUNNING",
        "pending_approval": True,
        "failed_stage": False,
        "stages": [
            {
                "approval_threshold": 1,
                "approvers": [],
                "child_workflows": [],
                "depends_on": [],
                "description": "Ask the user if they want to be greeted",
                "execution_time": None,
                "input": None,
                "name": "prompt",
                "output": {
                    "approved": False,
                    "display": "Would you like to be greeted?",
                },
                "rejecters": [],
                "requires_approval": True,
                "retry_count": 0,
                "retryable": True,
                "state": "PENDING_APPROVAL",
                "state_history": [
                    {"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"},
                    {"state": "IN_PROGRESS", "time": "1970-01-01T00:00:00+00:00"},
                    {
                        "state": "PENDING_APPROVAL",
                        "time": "1970-01-01T00:00:00+00:00",
                    },
                ],
                "traceback": None,
            },
            {
                "approval_threshold": 0,
                "approvers": [],
                "child_workflows": [],
                "depends_on": ["prompt"],
                "description": "Greet the user.",
                "execution_time": None,
                "input": None,
                "name": "greet",
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
                "depends_on": ["prompt"],
                "description": "Say goodbye to the user",
                "execution_time": None,
                "input": None,
                "name": "goodbye",
                "output": None,
                "rejecters": [],
                "requires_approval": False,
                "retry_count": 0,
                "retryable": True,
                "state": "NOT_STARTED",
                "state_history": [{"state": "NOT_STARTED", "time": "1970-01-01T00:00:00+00:00"}],
                "traceback": None,
            },
        ],
        "result": None,
        "search_attributes": {
            "User": ["test"],
            "ReadRoles": ["ngc-cfa"],
            "ExecuteRoles": ["ngc-cfa"],
        },
        "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mockid",
    }


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_active_workflow_queries_use_durable_cache(mock_redis, mock_load_config):
    """Verify active workflows read list-safe query data from the durable cache."""
    cache = mock_redis.from_config.return_value
    cache.get_cached_query = AsyncMock(side_effect=[True, {"user": "cached"}])
    cache.cache_query = AsyncMock()

    handle = MagicMock()
    handle.id = "active-workflow"
    handle.query = AsyncMock()

    description = MagicMock()
    description.status = WorkflowExecutionStatus.RUNNING

    result = await WorkflowSummaryResponse._execute_queries(
        handle, description, ["pending_approval", "input"]
    )

    assert result == {"pending_approval": True, "input": {"user": "cached"}}
    handle.query.assert_not_awaited()
    cache.cache_query.assert_not_awaited()
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_active_workflow_query_miss_uses_durable_cache(mock_redis, mock_load_config):
    """Verify active workflow cache misses populate durable query cache selectively."""
    cache = mock_redis.from_config.return_value
    cache.get_cached_query = AsyncMock(return_value=None)
    cache.cache_query = AsyncMock()

    handle = MagicMock()
    handle.id = "active-workflow"
    handle.query = AsyncMock(side_effect=[True, {"user": "live"}])

    description = MagicMock()
    description.status = WorkflowExecutionStatus.RUNNING

    result = await WorkflowSummaryResponse._execute_queries(
        handle, description, ["pending_approval", "input"]
    )

    assert result == {"pending_approval": True, "input": {"user": "live"}}
    cache.cache_query.assert_any_await("active-workflow", "pending_approval", True)
    cache.cache_query.assert_any_await("active-workflow", "input", {"user": "live"})
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch(
    "nv_config_manager.temporal.api.workflow_v1._WORKFLOW_LIST_QUERY_TIMEOUT_SECONDS",
    0.01,
)
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_active_workflow_query_timeout_does_not_block_summary(mock_redis, mock_load_config):
    """A workerless workflow cannot block list enrichment or cached query data."""
    cache = mock_redis.from_config.return_value
    cache.get_cached_query = AsyncMock(side_effect=[None, {"user": "cached"}])
    cache.cache_query = AsyncMock()

    async def workerless_query(_query: str):
        await asyncio.Event().wait()

    handle = MagicMock()
    handle.id = "workerless-workflow"
    handle.query = AsyncMock(side_effect=workerless_query)

    description = MagicMock()
    description.status = WorkflowExecutionStatus.RUNNING
    description.workflow_type = "UnavailableWorkflow"
    description.search_attributes = {"User": ["test"]}
    description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    description.close_time = None
    handle.describe = AsyncMock(return_value=description)

    result = await asyncio.wait_for(WorkflowSummaryResponse.from_handle(handle), timeout=0.2)

    assert result.id == "workerless-workflow"
    assert result.pending_approval is False
    assert result.workflow_input == {"user": "cached"}
    handle.query.assert_awaited_once_with("pending_approval")
    cache.cache_query.assert_not_awaited()
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_active_workflow_pending_false_is_not_cached(mock_redis, mock_load_config):
    """Verify active pending_approval=False is not cached durably."""
    cache = mock_redis.from_config.return_value
    cache.get_cached_query = AsyncMock(return_value=None)
    cache.cache_query = AsyncMock()

    handle = MagicMock()
    handle.id = "active-workflow"
    handle.query = AsyncMock(side_effect=[False, {"user": "live"}])

    description = MagicMock()
    description.status = WorkflowExecutionStatus.RUNNING

    result = await WorkflowSummaryResponse._execute_queries(
        handle, description, ["pending_approval", "input"]
    )

    assert result == {"pending_approval": False, "input": {"user": "live"}}
    cache.cache_query.assert_awaited_once_with("active-workflow", "input", {"user": "live"})
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_running_workflow_with_failed_stage_exposes_failed_stage_flag(
    mock_redis, mock_load_config
):
    """Verify failed-stage workflows expose the failed-stage flag."""
    cache = mock_redis.from_config.return_value
    cache.get_cached_query = AsyncMock(return_value={"user": "cached"})
    cache.cache_query = AsyncMock()

    handle = MagicMock()
    handle.id = "failed-stage-workflow"
    handle.query = AsyncMock()

    description = MagicMock()
    description.search_attributes = {
        FAILED_STAGE_SEARCH_ATTRIBUTE: [True],
        PENDING_APPROVAL_SEARCH_ATTRIBUTE: [False],
        "User": ["test"],
    }
    description.status = WorkflowExecutionStatus.RUNNING
    description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    description.close_time = None
    description.workflow_type = "HelloWorldApproval"
    handle.describe = AsyncMock(return_value=description)

    result = await WorkflowSummaryResponse.from_handle(handle)

    assert result.status == "RUNNING"
    assert result.pending_approval is False
    assert result.failed_stage is True
    assert result.workflow_input == {"user": "cached"}
    handle.query.assert_not_awaited()
    cache.cache_query.assert_not_awaited()
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@pytest.mark.parametrize("response_type", [WorkflowSummaryResponse, WorkflowDetailResponse])
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
async def test_terminated_workflow_is_not_pending_approval(
    mock_redis, mock_load_config, response_type
):
    """A stale search attribute must not override a terminated execution status."""
    cache = mock_redis.from_config.return_value

    async def get_cached_query(_workflow_id, query):
        if query == "input":
            return {"user": "cached"}
        if query == "compressed_stages":
            return StageMixin.compress_stages([])
        return None

    cache.get_cached_query = AsyncMock(side_effect=get_cached_query)
    cache.cache_query = AsyncMock()

    handle = MagicMock()
    handle.id = "terminated-pending-workflow"
    handle.query = AsyncMock()

    description = MagicMock()
    description.search_attributes = {
        PENDING_APPROVAL_SEARCH_ATTRIBUTE: [True],
        "User": ["test"],
    }
    description.status = WorkflowExecutionStatus.TERMINATED
    description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    description.close_time = datetime.fromisoformat("1970-01-01T00:01:00+00:00")
    description.workflow_type = "HelloWorldApproval"
    handle.describe = AsyncMock(return_value=description)

    result = await response_type.from_handle(handle)

    assert result.status == "TERMINATED"
    assert result.pending_approval is False
    handle.query.assert_not_awaited()
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_workflow_detail_not_found(mock_client):
    """Verify workflow detail returns 404 when workflow doesn't exist."""
    from temporalio.service import RPCError, RPCStatusCode

    class MockHandle(WorkflowHandle):
        _id = "nonexistent-workflow-id"

        def __init__(self, *args, **kwargs):
            pass

        async def describe(self):
            # Simulate the error that Temporal throws for non-existent workflows
            raise RPCError("sql: no rows in result set", RPCStatusCode.NOT_FOUND, b"")

    mock_client.return_value.get_workflow_handle = MockHandle
    workflow_id = "nonexistent-workflow-id"

    client = TestClient(app)
    rsp = client.get(f"/v1/workflow/{workflow_id}")
    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow with ID '{workflow_id}' not found"}


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.load_config")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_signal_workflow_invalidates_stage_query_cache(
    mock_client, mock_redis, mock_rbac_config, mock_load_config
):
    """Verify stage-changing signals invalidate cached stage query data."""
    workflow_id = str(uuid4())
    request = MagicMock()
    request.state.user = "test"
    request.state.roles = {"ngc-cfa"}

    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_admin_roles.return_value = set()
    mock_rbac_config.return_value = mock_rbac_instance

    mock_description = MagicMock()
    mock_description.status = WorkflowExecutionStatus.RUNNING
    mock_description.search_attributes = {
        "User": ["test"],
        "ReadRoles": ["ngc-cfa"],
        "ExecuteRoles": ["ngc-cfa"],
    }
    mock_description.workflow_type = "HelloWorldApproval"

    mock_handle = MagicMock()
    mock_handle.id = workflow_id
    mock_handle.describe = AsyncMock(return_value=mock_description)
    mock_handle.signal = AsyncMock()
    mock_client_instance = MagicMock()
    mock_client_instance.get_workflow_handle.return_value = mock_handle
    mock_client.return_value = mock_client_instance

    cache = mock_redis.from_config.return_value
    cache.delete_cached_query = AsyncMock()

    signal_input = ReviewSignalInput(stage_name="prompt", user="test")
    await signal_workflow(request, workflow_id, "approve", signal_input)

    mock_handle.signal.assert_awaited_once_with("approve", signal_input)
    cache.delete_cached_query.assert_any_await(workflow_id, "pending_approval")
    cache.delete_cached_query.assert_any_await(workflow_id, "compressed_stages")
    mock_redis.from_config.assert_called_once_with(mock_load_config.return_value)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
async def test_approve_workflow_not_found(mock_client):
    """Verify workflow approval returns 404 when workflow doesn't exist."""
    from temporalio.service import RPCError, RPCStatusCode

    class MockHandle(WorkflowHandle):
        _id = "nonexistent-workflow-id"

        def __init__(self, *args, **kwargs):
            pass

        async def describe(self):
            # Simulate the error that Temporal throws for non-existent workflows
            raise RPCError("sql: no rows in result set", RPCStatusCode.NOT_FOUND, b"")

    mock_client.return_value.get_workflow_handle = MockHandle
    workflow_id = "nonexistent-workflow-id"

    client = TestClient(app)
    rsp = client.post(
        f"/v1/workflow/{workflow_id}/approve/test-stage",
        headers={"X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    assert rsp.status_code == 404
    assert rsp.json() == {"detail": f"Workflow with ID '{workflow_id}' not found"}


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_workflows(mock_rbac_config, mock_redis, mock_client):
    """Verify HelloWorld Workflow API."""
    # Mock to always cache miss
    mock_redis.return_value.get_cached_result.return_value = None
    mock_redis.from_config.return_value.get_cached_query = AsyncMock(return_value=None)
    mock_redis.from_config.return_value.cache_query = AsyncMock()

    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_admin_roles.return_value = {"ngc-cfa"}
    mock_rbac_config.return_value = mock_rbac_instance

    class MockHandle(WorkflowHandle):
        def __init__(self, *args, **kwargs):
            pass

        async def describe(self):
            mock_description = MagicMock()
            mock_description.search_attributes = {"User": ["test"]}
            mock_description.status = WorkflowExecutionStatus.RUNNING
            mock_description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
            mock_description.close_time = None
            mock_description.workflow_type = "HelloWorldApproval"
            return mock_description

        async def query(self, name: str):
            if name == "pending_approval":
                return True
            if name == "input":
                return {"user": "test"}

    class MockHandle1(MockHandle):
        _id = "mock_uuid1"

    class MockHandle2(MockHandle):
        _id = "mock_uuid2"

    class MockHandle3(MockHandle):
        _id = "mock_uuid3"

    class MockWorkflowExecutionAsyncIterator:
        items = [
            MagicMock(id="mock_uuid1"),
            MagicMock(id="mock_uuid2"),
            MagicMock(id="mock_uuid3"),
        ]

        async def __aiter__(self):
            for item in self.items:
                yield item

        @property
        def next_page_token(self) -> bytes | None:
            """Token for the next page request if any."""
            return None

    def mock_get_workflow_handle(workflow_id):
        if workflow_id == "mock_uuid1":
            return MockHandle1()
        if workflow_id == "mock_uuid2":
            return MockHandle2()
        if workflow_id == "mock_uuid3":
            return MockHandle3()

    def mock_list_queries(query, **kwargs):
        return MockWorkflowExecutionAsyncIterator()

    mock_client.return_value.get_workflow_handle = mock_get_workflow_handle
    mock_client.return_value.list_workflows = MagicMock(side_effect=mock_list_queries)
    mock_client.return_value.count_workflows = AsyncMock(return_value=MagicMock(count=3))

    client = TestClient(app)
    rsp = client.get("/v1/workflow")
    assert rsp.status_code == 200
    assert rsp.json() == {
        "workflows": [
            {
                "id": "mock_uuid1",
                "workflow_type": "HelloWorldApproval",
                "workflow_input": {"user": "test"},
                "started_by": "test",
                "start_time": "1970-01-01T00:00:00Z",
                "close_time": None,
                "status": "RUNNING",
                "pending_approval": True,
                "failed_stage": False,
                "search_attributes": {"User": ["test"]},
                "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mock_uuid1",
            },
            {
                "id": "mock_uuid2",
                "workflow_type": "HelloWorldApproval",
                "workflow_input": {"user": "test"},
                "started_by": "test",
                "start_time": "1970-01-01T00:00:00Z",
                "close_time": None,
                "status": "RUNNING",
                "pending_approval": True,
                "failed_stage": False,
                "search_attributes": {"User": ["test"]},
                "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mock_uuid2",
            },
            {
                "id": "mock_uuid3",
                "workflow_type": "HelloWorldApproval",
                "workflow_input": {"user": "test"},
                "started_by": "test",
                "start_time": "1970-01-01T00:00:00Z",
                "close_time": None,
                "status": "RUNNING",
                "pending_approval": True,
                "failed_stage": False,
                "search_attributes": {"User": ["test"]},
                "href": f"{TEMPORAL_UI_WORKFLOW_BASE}/mock_uuid3",
            },
        ],
        "next_page_token": None,
        "total_count": 3,
        "page_count": 1,
    }
    mock_client.return_value.count_workflows.assert_called_with("(ReadRoles = 'all')")

    rsp = client.get("/v1/workflow", params={"status": "PENDING_APPROVAL"})
    assert rsp.status_code == 200
    assert all(workflow["status"] == "RUNNING" for workflow in rsp.json()["workflows"])
    assert all(workflow["pending_approval"] for workflow in rsp.json()["workflows"])
    mock_client.return_value.list_workflows.assert_called_with(
        "ExecutionStatus = 'Running' and PendingApproval = true and (ReadRoles = 'all')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )
    mock_client.return_value.count_workflows.assert_called_with(
        "ExecutionStatus = 'Running' and PendingApproval = true and (ReadRoles = 'all')"
    )

    rsp = client.get("/v1/workflow", params={"hide_completed": "true"})
    assert rsp.status_code == 200
    mock_client.return_value.list_workflows.assert_called_with(
        "ExecutionStatus != 'Completed' and (ReadRoles = 'all')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )
    mock_client.return_value.count_workflows.assert_called_with(
        "ExecutionStatus != 'Completed' and (ReadRoles = 'all')"
    )

    rsp = client.get(
        "/v1/workflow",
        params={"status": "RUNNING", "pending_approval": "true"},
    )
    assert rsp.status_code == 200
    assert all(workflow["status"] == "RUNNING" for workflow in rsp.json()["workflows"])
    assert all(workflow["pending_approval"] for workflow in rsp.json()["workflows"])
    mock_client.return_value.list_workflows.assert_called_with(
        "ExecutionStatus = 'Running' and PendingApproval = true and (ReadRoles = 'all')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )

    rsp = client.get("/v1/workflow", params={"status": "FAILED"})
    assert rsp.status_code == 200
    mock_client.return_value.list_workflows.assert_called_with(
        "(ExecutionStatus = 'Failed' or FailedStage = true) and (ReadRoles = 'all')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )

    # Test filter query construction
    rsp = client.get(
        "/v1/workflow",
        params={
            "user": "test",
            "workflow_type": "test",
            "workflow_id": "test-id",
            "device_id": "test",
            "device_name": "test",
            "device_role": "test",
            "device_platform": "test",
            "site": "test",
            "status": "RUNNING",
            "start_time": "2025-03-04T02:32:00Z",
            "end_time": "2025-03-04T02:35:00Z",
        },
    )
    mock_client.return_value.list_workflows.assert_called_with(
        "User = 'test' and "
        "WorkflowType = 'test' and "
        "WorkflowId = 'test-id' and "
        "DeviceID = 'test' and "
        "DeviceName = 'test' and "
        "DeviceRole = 'test' and "
        "DevicePlatform = 'test' and "
        "Site = 'test' and "
        "ExecutionStatus = 'Running' and "
        "StartTime >= '2025-03-04T02:32:00Z' and "
        "CloseTime <= '2025-03-04T02:35:00Z' and "
        "(ReadRoles = 'all')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )
    filter_query = mock_client.return_value.list_workflows.call_args.args[0]
    mock_client.return_value.count_workflows.assert_awaited_with(filter_query)

    rsp = client.get(
        "/v1/workflow",
        headers={
            "X-Auth-Request-Email": "user@nvidia.com",
            "X-AUTH-REQUEST-GROUPS": "ngc-gni, bad'role",
        },
    )
    assert rsp.status_code == 400
    assert rsp.json() == {"detail": "Invalid characters in query parameter 'role'"}

    # Modify the X-AUTH-REQUEST-GROUPS header and test filter change
    # admin roles can see all workflows, so ReadRoles is not added to the filter
    rsp = client.get(
        "/v1/workflow",
        params={
            "user": "test",
            "workflow_type": "test",
            "workflow_id": "test-id",
            "device_id": "test",
            "device_name": "test",
            "device_role": "test",
            "device_platform": "test",
            "site": "test",
            "status": "RUNNING",
            "start_time": "2025-03-04T02:32:00Z",
            "end_time": "2025-03-04T02:35:00Z",
        },
        headers={"X-Auth-Request-Email": "admin@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-cfa"},
    )
    mock_client.return_value.list_workflows.assert_called_with(
        "User = 'test' and "
        "WorkflowType = 'test' and "
        "WorkflowId = 'test-id' and "
        "DeviceID = 'test' and "
        "DeviceName = 'test' and "
        "DeviceRole = 'test' and "
        "DevicePlatform = 'test' and "
        "Site = 'test' and "
        "ExecutionStatus = 'Running' and "
        "StartTime >= '2025-03-04T02:32:00Z' and "
        "CloseTime <= '2025-03-04T02:35:00Z'",
        limit=100,
        page_size=100,
        next_page_token=None,
    )

    # GNI is less permissive, so ReadRoles is added to the filter
    rsp = client.get(
        "/v1/workflow",
        params={
            "user": "test",
            "workflow_type": "test",
            "workflow_id": "test-id",
            "device_id": "test",
            "device_name": "test",
            "device_role": "test",
            "device_platform": "test",
            "site": "test",
            "status": "RUNNING",
            "start_time": "2025-03-04T02:32:00Z",
            "end_time": "2025-03-04T02:35:00Z",
        },
        headers={"X-Auth-Request-Email": "user@nvidia.com", "X-AUTH-REQUEST-GROUPS": "ngc-gni"},
    )
    mock_client.return_value.list_workflows.assert_called_with(
        "User = 'test' and "
        "WorkflowType = 'test' and "
        "WorkflowId = 'test-id' and "
        "DeviceID = 'test' and "
        "DeviceName = 'test' and "
        "DeviceRole = 'test' and "
        "DevicePlatform = 'test' and "
        "Site = 'test' and "
        "ExecutionStatus = 'Running' and "
        "StartTime >= '2025-03-04T02:32:00Z' and "
        "CloseTime <= '2025-03-04T02:35:00Z' and "
        "(ReadRoles = 'all' or ReadRoles = 'ngc-gni')",
        limit=100,
        page_size=100,
        next_page_token=None,
    )


def test_workflows_invalid_status_returns_400():
    """Verify unknown workflow status filters are rejected before querying Temporal."""
    client = TestClient(app)
    rsp = client.get("/v1/workflow", params={"status": "NOT_A_STATUS"})

    assert rsp.status_code == 400
    assert rsp.json() == {"detail": "Invalid workflow status 'NOT_A_STATUS'"}


def test_workflows_invalid_limit_returns_400():
    """Verify workflow list limit must be positive."""
    client = TestClient(app)
    rsp = client.get("/v1/workflow", params={"limit": "0"})

    assert rsp.status_code == 400
    assert rsp.json() == {"detail": "limit must be greater than 0"}


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_workflows_zero_count_has_zero_pages(mock_rbac_config, mock_client):
    """Verify workflow count metadata for empty result sets."""
    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_admin_roles.return_value = {"all"}
    mock_rbac_config.return_value = mock_rbac_instance

    class MockWorkflowExecutionAsyncIterator:
        async def __aiter__(self):
            return
            yield  # pragma: no cover

        @property
        def next_page_token(self) -> bytes | None:
            """Token for the next page request if any."""
            return None

    mock_client.return_value.list_workflows = MagicMock(
        return_value=MockWorkflowExecutionAsyncIterator()
    )
    mock_client.return_value.count_workflows = AsyncMock(return_value=MagicMock(count=0))

    client = TestClient(app)
    rsp = client.get("/v1/workflow", params={"limit": "50"})

    assert rsp.status_code == 200
    assert rsp.json() == {
        "workflows": [],
        "next_page_token": None,
        "total_count": 0,
        "page_count": 0,
    }
    mock_client.return_value.list_workflows.assert_called_once_with(
        None,
        limit=50,
        page_size=50,
        next_page_token=None,
    )
    mock_client.return_value.count_workflows.assert_awaited_once_with(None)


@pytest.mark.asyncio
@patch("nv_config_manager.temporal.api.workflow_v1.get_client")
@patch("nv_config_manager.temporal.api.workflow_v1.RedisClient")
@patch("nv_config_manager.temporal.api.workflow_v1.RBACConfig")
async def test_workflows_pending_approval_filter_uses_search_attribute(
    mock_rbac_config, mock_redis, mock_client
):
    """Verify pending approval filtering is pushed into Temporal visibility."""
    mock_redis.from_config.return_value.get_cached_query = AsyncMock(return_value=None)
    mock_redis.from_config.return_value.cache_query = AsyncMock()

    mock_rbac_instance = MagicMock()
    mock_rbac_instance.get_admin_roles.return_value = {"ngc-cfa"}
    mock_rbac_config.return_value = mock_rbac_instance

    def mock_get_workflow_handle(workflow_id):
        mock_description = MagicMock()
        mock_description.search_attributes = {
            PENDING_APPROVAL_SEARCH_ATTRIBUTE: [True],
            "User": ["test"],
        }
        mock_description.status = WorkflowExecutionStatus.RUNNING
        mock_description.start_time = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
        mock_description.close_time = None
        mock_description.workflow_type = "HelloWorldApproval"

        async def mock_query(name: str):
            if name == "pending_approval":
                return True
            if name == "input":
                return {"user": "test"}

        mock_handle = MagicMock()
        mock_handle.id = workflow_id
        mock_handle.describe = AsyncMock(return_value=mock_description)
        mock_handle.query = mock_query
        return mock_handle

    class MockWorkflowExecutionAsyncIterator:
        def __init__(self, items, next_page_token):
            self.items = items
            self._next_page_token = next_page_token

        async def __aiter__(self):
            for item in self.items:
                yield item

        @property
        def next_page_token(self) -> bytes | None:
            """Token for the next page request if any."""
            return self._next_page_token

    def mock_list_queries(query, **kwargs):
        return MockWorkflowExecutionAsyncIterator(
            [MagicMock(id="pending_uuid1"), MagicMock(id="pending_uuid2")],
            b"token-1",
        )

    mock_client.return_value.get_workflow_handle = mock_get_workflow_handle
    mock_client.return_value.list_workflows = MagicMock(side_effect=mock_list_queries)
    mock_client.return_value.count_workflows = AsyncMock(return_value=MagicMock(count=5))

    client = TestClient(app)
    rsp = client.get(
        "/v1/workflow",
        params={"status": "RUNNING", "pending_approval": "true", "limit": "2"},
    )

    assert rsp.status_code == 200
    assert [workflow["id"] for workflow in rsp.json()["workflows"]] == [
        "pending_uuid1",
        "pending_uuid2",
    ]
    assert rsp.json()["next_page_token"] is not None
    assert rsp.json()["total_count"] == 5
    assert rsp.json()["page_count"] == 3
    mock_client.return_value.list_workflows.assert_called_once_with(
        "ExecutionStatus = 'Running' and PendingApproval = true and (ReadRoles = 'all')",
        limit=2,
        page_size=2,
        next_page_token=None,
    )
    mock_client.return_value.count_workflows.assert_awaited_once_with(
        "ExecutionStatus = 'Running' and PendingApproval = true and (ReadRoles = 'all')"
    )


def test_workflow_types():
    """Verify Workflow Types."""
    client = TestClient(app)
    rsp = client.get("/v1/workflow/types")
    assert rsp.status_code == 200
    workflow_types = set(rsp.json())
    # Assert some of our workflows are returned
    # dont want to have to update this test on every
    # workflow creation
    assert {"BackupWorkflow", "DeployWorkflow"}.issubset(workflow_types)
    assert "HelloWorldRunning" not in workflow_types
    assert "TenantDeployWorkflow" in workflow_types


@patch("nv_config_manager.temporal.api.dynamic_endpoints.RBACConfig")
def test_workflow_metadata(mock_dynamic_rbac_config):
    """Verify workflow metadata includes RBAC roles."""
    dynamic_rbac = MagicMock()
    dynamic_rbac.get_workflow_roles.side_effect = lambda workflow_name: {
        "read_roles": {"reader", workflow_name},
        "execute_roles": {"executor", workflow_name},
    }
    mock_dynamic_rbac_config.return_value = dynamic_rbac

    client = TestClient(app)
    rsp = client.get("/v1/workflow/metadata")
    assert rsp.status_code == 200

    response = rsp.json()
    assert "admin_roles" not in response

    workflows_by_name = {workflow["name"]: workflow for workflow in response["workflows"]}
    assert "HelloWorldRunning" not in workflows_by_name
    assert "TenantDeployWorkflow" not in workflows_by_name
    backup_workflow = workflows_by_name["BackupWorkflow"]
    assert backup_workflow["display_name"] == "Configuration Backup"
    assert backup_workflow["description"]
    assert backup_workflow["endpoint"] == "/ngc/backup"
    assert backup_workflow["namespace"] == "ngc"
    assert backup_workflow["cli_name"] == "backup"
    assert backup_workflow["input_class"] == "BackupInput"
    assert backup_workflow["read_roles"] == ["BackupWorkflow", "reader"]
    assert backup_workflow["execute_roles"] == ["BackupWorkflow", "executor"]


def test_tenant_deploy_endpoint_is_not_registered():
    """Do not expose the internal Tenant Deploy child workflow through REST."""
    route_paths = {path for route in app.routes if (path := getattr(route, "path", None))}
    assert "/v1/workflow/ngc/tenant-deploy" not in route_paths


@patch("nv_config_manager.common.auth.x509.load_pem_x509_certificate")
def test_middleware(mock_load_cert):
    """Verify the auth middleware with different certificate types."""
    from unittest.mock import Mock

    from cryptography import x509

    client = TestClient(app)

    # Test 1: Legacy certificate (org=nv-config-manager only)
    # Should use org for both user and role
    mock_cert = Mock()
    mock_cert.subject.get_attributes_for_oid.side_effect = lambda oid: {
        x509.NameOID.ORGANIZATION_NAME: [Mock(value="nv-config-manager")],
        x509.NameOID.ORGANIZATIONAL_UNIT_NAME: [],
        x509.NameOID.COMMON_NAME: [],
    }.get(oid, [])
    mock_load_cert.return_value = mock_cert

    rsp = client.get("/whoami", headers={"ssl-client-cert": "dummy_cert"})
    assert rsp.json() == {
        "user": "nv-config-manager",
        "roles": ["all", "nv-config-manager"],
    }

    # Test 2: Service-to-service certificate (org=nv-config-manager, ou=nv-config-manager)
    # Should use ou for role, ou for user (since no CN)
    mock_cert.subject.get_attributes_for_oid.side_effect = lambda oid: {
        x509.NameOID.ORGANIZATION_NAME: [Mock(value="nv-config-manager")],
        x509.NameOID.ORGANIZATIONAL_UNIT_NAME: [Mock(value="nv-config-manager")],
        x509.NameOID.COMMON_NAME: [],
    }.get(oid, [])
    mock_load_cert.return_value = mock_cert

    rsp = client.get("/whoami", headers={"ssl-client-cert": "dummy_cert"})
    assert rsp.json() == {
        "user": "nv-config-manager",
        "roles": ["all", "nv-config-manager"],
    }

    # Test 3: User certificate (org=nv-config-manager, ou=ngc-gni, cn=testuser@nvidia.com)
    # Should use ou for role, cn for user
    mock_cert.subject.get_attributes_for_oid.side_effect = lambda oid: {
        x509.NameOID.ORGANIZATION_NAME: [Mock(value="nv-config-manager")],
        x509.NameOID.ORGANIZATIONAL_UNIT_NAME: [Mock(value="ngc-gni")],
        x509.NameOID.COMMON_NAME: [Mock(value="testuser@nvidia.com")],
    }.get(oid, [])
    mock_load_cert.return_value = mock_cert

    rsp = client.get("/whoami", headers={"ssl-client-cert": "dummy_cert"})
    assert rsp.json() == {
        "user": "testuser@nvidia.com",
        "roles": ["all", "ngc-gni"],
    }

    rsp = client.get(
        "/whoami",
        headers={
            "X-AUTH-REQUEST-EMAIL": "ngc-cfa@nvidia.com",
            "X-AUTH-REQUEST-GROUPS": "nv-config-manager",
        },
    )
    assert rsp.json() == {
        "user": "ngc-cfa",
        "roles": ["all", "nv-config-manager"],
    }


def test_cors_middleware_configured(custom_ini):
    """Test that CORS middleware is configured when temporal.api section exists."""
    custom_ini(
        """
        [temporal.api]
        cors_origins = https://example.com,https://test.com

        [nautobot]
        server = https://nautobot.example.com
        token = test

        [temporal]
        grpc_service = temporal:7233
        api_service = http://temporal-api:9000
        api_url = https://temporal-api.example.com
        temporal_ui_url = https://temporal-ui.example.com
        ui_url = https://temporal-ui.example.com
        use_internal_endpoint = true
        """
    )

    # Need to reimport app to pick up new config
    from importlib import reload

    from nv_config_manager.temporal.api import main as temporal_main

    reload(temporal_main)

    client = TestClient(temporal_main.app)

    # Test that CORS headers are present
    response = client.options(
        "/healthcheck",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    # FastAPI/Starlette CORS middleware should add these headers
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


def test_cors_middleware_not_configured_when_section_missing(custom_ini):
    """Test that CORS middleware is not configured when temporal.api section is missing."""
    custom_ini(
        """
        [nautobot]
        server = https://nautobot.example.com
        token = test

        [temporal]
        grpc_service = temporal:7233
        api_service = http://temporal-api:9000
        api_url = https://temporal-api.example.com
        temporal_ui_url = https://temporal-ui.example.com
        ui_url = https://temporal-ui.example.com
        use_internal_endpoint = true
        """
    )

    # Need to reimport app to pick up new config
    from importlib import reload

    from nv_config_manager.temporal.api import main as temporal_main

    reload(temporal_main)

    client = TestClient(temporal_main.app)

    # Without CORS middleware, OPTIONS requests should still work
    # but won't have CORS headers
    response = client.get("/healthcheck")
    assert response.status_code == 200
