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
import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from nv_config_manager.common.client.render import FileCommit
from nv_config_manager.render.api.main import app


def test_healthcheck():
    """Verify healthcheck."""
    client = TestClient(app)
    rsp = client.get("/healthcheck")
    assert rsp.status_code == 200
    assert rsp.json() == "OK"


@patch("nv_config_manager.render.api.render_v1.execute_render", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render(mock_execute_render):
    """Verify render API."""
    commit_1 = str(uuid4())
    commit_2 = str(uuid4())
    file_commits = [
        FileCommit(filename="startup.yaml", commit=commit_1),
        FileCommit(filename="tenant.yaml", commit=commit_2),
    ]
    mock_execute_render.return_value = file_commits
    client = TestClient(app)

    # Test with default commit message (no body)
    rsp = client.post("/v1/render/test/render")
    assert rsp.status_code == 201
    assert rsp.json() == {
        "updated_files": [
            {"filename": "startup.yaml", "commit": commit_1},
            {"filename": "tenant.yaml", "commit": commit_2},
        ]
    }
    mock_execute_render.assert_awaited_with(
        "test", "Manual render initiated by anonymous", "anonymous"
    )

    # Test with custom commit message
    custom_message = "Custom commit message"
    rsp = client.post("/v1/render/test/render", json={"commit_message": custom_message})
    mock_execute_render.assert_awaited_with("test", custom_message, "anonymous")
    assert rsp.status_code == 201

    # Test with no changes (empty list)
    mock_execute_render.return_value = []
    rsp = client.post("/v1/render/test/render")
    assert rsp.status_code == 200
    assert rsp.json() == {"updated_files": []}


@patch("nv_config_manager.render.api.render_v1.queue_render_batch", new_callable=AsyncMock)
@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_success(mock_get_devices, mock_queue_render_batch):
    """Test render all endpoint with successful queueing."""
    # Mock device list
    device_uuids = ["device-1", "device-2", "device-3"]
    mock_get_devices.return_value = device_uuids

    # Mock successful queue_render_batch calls
    mock_queue_render_batch.return_value = (3, [])  # (queued_count, failed_devices)

    client = TestClient(app)

    # Test with default commit message (no body)
    rsp = client.post("/v1/render/all")
    assert rsp.status_code == 202

    response_data = rsp.json()
    assert response_data["message"] == "Queued renders for 3 devices"
    assert response_data["queued_count"] == 3
    assert response_data["total_devices"] == 3
    assert "failed_devices" not in response_data

    # Verify queue_render_batch was called once with all devices
    assert mock_queue_render_batch.call_count == 1
    call = mock_queue_render_batch.call_args_list[0]
    assert call[0][0] == device_uuids  # device_uuids list
    assert call[0][1] == "Bulk render initiated by anonymous"  # commit_message
    assert call[0][2] == "anonymous"  # user
    # call[0][3] is timestamp - just verify it's a string
    assert isinstance(call[0][3], str)


@patch("nv_config_manager.render.api.render_v1.queue_render_batch", new_callable=AsyncMock)
@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_custom_commit_message(mock_get_devices, mock_queue_render_batch):
    """Test render all endpoint with custom commit message."""
    device_uuids = ["device-1", "device-2"]
    mock_get_devices.return_value = device_uuids
    mock_queue_render_batch.return_value = (2, [])  # (queued_count, failed_devices)

    client = TestClient(app)
    custom_message = "Model update - re-render all devices"

    rsp = client.post("/v1/render/all", json={"commit_message": custom_message})
    assert rsp.status_code == 202

    # Verify custom commit message was used
    call = mock_queue_render_batch.call_args_list[0]
    assert call[0][1] == custom_message


@patch("nv_config_manager.render.api.render_v1.queue_render_batch", new_callable=AsyncMock)
@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_no_devices(mock_get_devices, mock_queue_render_batch):
    """Test render all endpoint when no devices are found."""
    mock_get_devices.return_value = []

    client = TestClient(app)
    rsp = client.post("/v1/render/all")

    assert rsp.status_code == 200
    response_data = rsp.json()
    assert response_data["message"] == "No render-enabled devices found"
    assert response_data["queued_count"] == 0

    # queue_render_batch should not be called
    mock_queue_render_batch.assert_not_called()


@patch("nv_config_manager.render.api.render_v1.queue_render_batch", new_callable=AsyncMock)
@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_partial_failures(mock_get_devices, mock_queue_render_batch):
    """Test render all endpoint with some devices failing to queue."""
    device_uuids = ["device-1", "device-2", "device-3"]
    mock_get_devices.return_value = device_uuids

    # Mock queue_render_batch to return partial failures
    failed_devices = [{"device_uuid": "device-2", "error": "Device not enabled for renders"}]
    mock_queue_render_batch.return_value = (2, failed_devices)  # (queued_count, failed_devices)

    client = TestClient(app)
    rsp = client.post("/v1/render/all")

    assert rsp.status_code == 202
    response_data = rsp.json()
    assert response_data["message"] == "Queued renders for 2 devices"
    assert response_data["queued_count"] == 2
    assert response_data["total_devices"] == 3

    # Check failed devices
    assert "failed_devices" in response_data
    returned_failed_devices = response_data["failed_devices"]
    assert len(returned_failed_devices) == 1
    assert returned_failed_devices[0]["device_uuid"] == "device-2"
    assert "Device not enabled for renders" in returned_failed_devices[0]["error"]


@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_graphql_error(mock_get_devices):
    """Test render all endpoint when GraphQL query fails."""
    from fastapi import HTTPException

    mock_get_devices.side_effect = HTTPException(
        status_code=500, detail="Failed to query render-enabled devices: GraphQL connection failed"
    )

    client = TestClient(app)
    rsp = client.post("/v1/render/all")

    assert rsp.status_code == 500
    assert "Failed to query render-enabled devices" in rsp.json()["detail"]


@patch("nv_config_manager.render.api.render_v1.queue_render_batch", new_callable=AsyncMock)
@patch("nv_config_manager.render.api.render_v1.get_render_enabled_devices", new_callable=AsyncMock)
@patch.dict(os.environ, {"LOCAL_VENV": "1"})
def test_render_all_user_extraction(mock_get_devices, mock_queue_render_batch):
    """Test render all endpoint with custom user header."""
    device_uuids = ["device-1"]
    mock_get_devices.return_value = device_uuids
    mock_queue_render_batch.return_value = (1, [])  # (queued_count, failed_devices)

    client = TestClient(app)
    rsp = client.post("/v1/render/all", headers={"X-Auth-Request-Email": "test.user@nvidia.com"})

    assert rsp.status_code == 202

    # Verify user was extracted correctly (without @nvidia.com)
    call = mock_queue_render_batch.call_args_list[0]
    assert call[0][1] == "Bulk render initiated by test.user"  # commit_message
    assert call[0][2] == "test.user"  # user
