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
"""Tests for dynamic workflow endpoint generation."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.api import dynamic_endpoints
from nv_config_manager.temporal.api.dynamic_endpoints import create_workflow_endpoint
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.ngc.workflows.cable_validation import (
    DeviceCableValidationInput,
    DeviceCableValidationWorkflow,
)


class _Input(BaseModel):
    host: str


class _CanonWorkflow(WorkflowMetadataMixin):
    workflow_name = "Canon"
    workflow_description = "Canonicalizing workflow"
    workflow_input_class = _Input
    workflow_api_endpoint = "/x"

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        cast(_Input, body).host = "canonical"
        return body


@pytest.mark.asyncio
async def test_default_canonicalize_input_is_noop():
    body = _Input(host="ufm01")
    assert await WorkflowMetadataMixin.canonicalize_input(body) is body
    assert body.host == "ufm01"


@pytest.mark.asyncio
async def test_endpoint_canonicalizes_input_before_start(mocker):
    """The generated endpoint runs canonicalize_input before starting the run."""
    captured: dict[str, BaseModel] = {}

    async def _fake_start(request, workflow_class, body):
        captured["body"] = body
        return "wid-1"

    mocker.patch.object(dynamic_endpoints, "start_workflow", new=_fake_start)

    endpoint = create_workflow_endpoint(_CanonWorkflow, _Input, "/x")
    request = MagicMock()
    request.state.user = "user@nvidia.com"

    body = _Input(host="ufm01")
    response = await endpoint(body, request)

    assert response.id == "wid-1"
    assert cast(_Input, captured["body"]).host == "canonical"


@pytest.mark.asyncio
async def test_endpoint_returns_422_for_canonicalization_failure(mocker):
    """Invalid external references fail before workflow submission with a client error."""

    async def _reject_input(cls, body):
        raise ApplicationError("UFM device not found", non_retryable=True)

    mocker.patch.object(_CanonWorkflow, "canonicalize_input", new=classmethod(_reject_input))
    start = mocker.patch.object(dynamic_endpoints, "start_workflow", new=mocker.AsyncMock())
    endpoint = create_workflow_endpoint(_CanonWorkflow, _Input, "/x")
    request = MagicMock()
    request.state.user = "user@nvidia.com"

    with pytest.raises(HTTPException, match="UFM device not found") as exc_info:
        await endpoint(_Input(host="attacker.example.com"), request)

    assert exc_info.value.status_code == 422
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_cable_endpoint_rejects_deferred_status_updates(mocker):
    """The child-only status deferral option cannot be set through the API."""
    start = mocker.patch.object(dynamic_endpoints, "start_workflow", new=mocker.AsyncMock())
    endpoint = create_workflow_endpoint(
        DeviceCableValidationWorkflow,
        DeviceCableValidationInput,
        "/ngc/device_cable_validation",
    )
    request = MagicMock()
    request.state.user = "user@nvidia.com"

    with pytest.raises(HTTPException, match="only valid for site cable validation") as exc_info:
        await endpoint(
            DeviceCableValidationInput(
                device_id="device-1",
                defer_cable_status_updates=True,
            ),
            request,
        )

    assert exc_info.value.status_code == 422
    start.assert_not_awaited()
