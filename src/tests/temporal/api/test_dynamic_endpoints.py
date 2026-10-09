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

from typing import Annotated, cast
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.api import dynamic_endpoints
from nv_config_manager.temporal.api.dynamic_endpoints import (
    create_workflow_endpoint,
    register_dynamic_endpoints,
)
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.ngc.workflows.cable_validation import (
    DeviceCableValidationInput,
    DeviceCableValidationWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_add import (
    IBPKeyMemberAddInput,
    IBPKeyMemberAddWorkflow,
)
from nv_config_manager_workflows.metadata import build_workflow_lock_key
from nv_config_manager_workflows.mixins import ib_pkey as ib_pkey_mixins
from nv_config_manager_workflows.ui import ServerOwned


class _Input(BaseModel):
    host: str


class _AttributedInput(BaseModel):
    host: str
    user: Annotated[str | None, ServerOwned()] = None
    user_domain: Annotated[str | None, ServerOwned()] = None


class _CallerOwnedInput(BaseModel):
    host: str
    user: str | None = None
    user_domain: str | None = None


class _UnsupportedOwnedInput(BaseModel):
    host: str
    owner: Annotated[str | None, ServerOwned()] = None


class _CanonWorkflow(WorkflowMetadataMixin):
    workflow_name = "Canon"
    workflow_description = "Canonicalizing workflow"
    workflow_input_class = _Input
    workflow_api_endpoint = "/x"

    @classmethod
    async def canonicalize_input(cls, body: BaseModel) -> BaseModel:
        cast(_Input, body).host = "canonical"
        return body


class _AttributedWorkflow(WorkflowMetadataMixin):
    workflow_name = "Attributed"
    workflow_description = "Workflow with server-owned attribution"
    workflow_input_class = _AttributedInput
    workflow_api_endpoint = "/attributed"


class _UnsupportedOwnedWorkflow(WorkflowMetadataMixin):
    workflow_name = "Unsupported attribution"
    workflow_description = "Workflow whose invalid browser form must not disable execution"
    workflow_input_class = _UnsupportedOwnedInput
    workflow_api_endpoint = "/unsupported-attribution"


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
async def test_endpoint_replaces_submitted_identity_with_authenticated_identity(mocker):
    """HTTP callers cannot spoof input fields owned by the authenticated boundary."""
    captured: dict[str, BaseModel] = {}

    async def _fake_start(request, workflow_class, body):
        captured["body"] = body
        return "wid-1"

    mocker.patch.object(dynamic_endpoints, "start_workflow", new=_fake_start)
    endpoint = create_workflow_endpoint(_AttributedWorkflow, _AttributedInput, "/attributed")
    request = MagicMock()
    request.state.user = "trusted@example.com"

    await endpoint(
        _AttributedInput(
            host="device-1",
            user="spoofed@attacker.example",
            user_domain="attacker.example",
        ),
        request,
    )

    submitted = cast(_AttributedInput, captured["body"])
    assert submitted.user == "trusted@example.com"
    assert submitted.user_domain == "example.com"


@pytest.mark.asyncio
async def test_endpoint_preserves_unmarked_identity_fields(mocker):
    """Non-empty unmarked identity fields remain caller controlled for compatibility."""
    captured: dict[str, BaseModel] = {}

    async def _fake_start(request, workflow_class, body):
        captured["body"] = body
        return "wid-1"

    mocker.patch.object(dynamic_endpoints, "start_workflow", new=_fake_start)
    endpoint = create_workflow_endpoint(_AttributedWorkflow, _CallerOwnedInput, "/attributed")
    request = MagicMock()
    request.state.user = "trusted@example.com"

    await endpoint(
        _CallerOwnedInput(
            host="device-1",
            user="caller@example.net",
            user_domain="example.net",
        ),
        request,
    )

    submitted = cast(_CallerOwnedInput, captured["body"])
    assert submitted.user == "caller@example.net"
    assert submitted.user_domain == "example.net"


@pytest.mark.parametrize("value", [None, ""])
@pytest.mark.asyncio
async def test_endpoint_populates_falsey_unmarked_legacy_identity_fields(mocker, value):
    """Legacy models retain the falsey identity fallback used before form metadata."""
    captured: dict[str, BaseModel] = {}

    async def _fake_start(request, workflow_class, body):
        captured["body"] = body
        return "wid-1"

    mocker.patch.object(dynamic_endpoints, "start_workflow", new=_fake_start)
    endpoint = create_workflow_endpoint(_AttributedWorkflow, _CallerOwnedInput, "/attributed")
    request = MagicMock()
    request.state.user = "trusted@example.com"

    await endpoint(
        _CallerOwnedInput(host="device-1", user=value, user_domain=value),
        request,
    )

    submitted = cast(_CallerOwnedInput, captured["body"])
    assert submitted.user == "trusted@example.com"
    assert submitted.user_domain == "example.com"


def test_invalid_server_owned_form_marker_does_not_remove_execution_endpoint() -> None:
    """Form validation is isolated from otherwise valid dynamic API registration."""
    router = APIRouter(prefix="/workflow")

    register_dynamic_endpoints(router, workflows=[_UnsupportedOwnedWorkflow])

    assert [getattr(route, "path", None) for route in router.routes] == [
        "/workflow/unsupported-attribution"
    ]


@pytest.mark.asyncio
async def test_ib_pkey_endpoint_submits_canonical_lock_input(mocker):
    """UFM canonicalization precedes submission and therefore lock acquisition."""
    events: list[str] = []
    captured: dict[str, BaseModel] = {}

    async def _canonicalize_host(host: str) -> str:
        assert host == "ufm01"
        events.append("canonicalize")
        return "10.0.0.5"

    async def _fake_start(request, workflow_class, body):
        events.append("start")
        captured["body"] = body
        return "wid-1"

    mocker.patch.object(ib_pkey_mixins, "_canonicalize_ufm_host", new=_canonicalize_host)
    mocker.patch.object(dynamic_endpoints, "start_workflow", new=_fake_start)
    endpoint = create_workflow_endpoint(
        IBPKeyMemberAddWorkflow,
        IBPKeyMemberAddInput,
        "/ngc/ib_pkey_member_add",
    )
    request = MagicMock()
    request.state.user = "user@nvidia.com"

    response = await endpoint(
        IBPKeyMemberAddInput(
            host="ufm01",
            pkey="0x100",
            guids=["0002c903000e0b72"],
        ),
        request,
    )

    submitted = cast(IBPKeyMemberAddInput, captured["body"])
    lock_spec = IBPKeyMemberAddWorkflow.get_workflow_lock()
    assert lock_spec is not None
    assert response.id == "wid-1"
    assert events == ["canonicalize", "start"]
    assert submitted.host == "10.0.0.5"
    assert submitted.pkey == "0x0100"
    assert (
        build_workflow_lock_key(
            lock_spec,
            workflow_name=IBPKeyMemberAddWorkflow.get_workflow_name(),
            namespace=IBPKeyMemberAddWorkflow.get_workflow_namespace(),
            workflow_input=submitted,
        )
        == "wf-lock:ngc:host=10.0.0.5:pkey=0x0100"
    )


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
    assert exc_info.value.detail == [
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": "UFM device not found",
        }
    ]
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
    assert exc_info.value.detail == [
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": "defer_cable_status_updates is only valid for site cable validation "
            "child workflows",
        }
    ]
    start.assert_not_awaited()
