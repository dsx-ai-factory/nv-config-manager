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
"""Readers can inspect historical workflows without inheriting execution access."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from temporalio.service import RPCError, RPCStatusCode

from nv_config_manager.common import auth
from nv_config_manager.temporal.api import workflow_v1
from nv_config_manager.temporal.api.workflow_authorization import require_workflow_execute_access


@pytest.fixture
def reader_request(monkeypatch: pytest.MonkeyPatch) -> Request:
    monkeypatch.setattr(
        auth,
        "_auth_config",
        auth.AuthConfig(required=False, read_only_groups=("api-reader",)),
    )
    monkeypatch.setattr(auth, "_auth_config_tracks_file", False)
    config = MagicMock()
    roles = {"read_roles": {"api-reader"}, "execute_roles": {"all"}}
    config.get_workflow_roles.side_effect = lambda name: (
        roles if name == "AllowedWorkflow" else None
    )
    config.get_all_workflows.return_value = {
        "AllowedWorkflow": roles,
        "RestrictedWorkflow": {"read_roles": {"other-reader"}, "execute_roles": {"all"}},
    }
    config.get_admin_roles.return_value = {"administrator"}
    monkeypatch.setattr(workflow_v1, "RBACConfig", lambda: config)
    request = Request({"type": "http", "method": "GET", "path": "/v1/workflow", "headers": []})
    request.state.user = "reader"
    request.state.roles = {"all", "api-reader"}
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_roles", [[], ["old-role"]])
async def test_reader_can_inspect_historical_workflow(
    reader_request: Request, stored_roles: list[str]
) -> None:
    handle = MagicMock()
    handle.describe = AsyncMock(
        return_value=MagicMock(
            workflow_type="AllowedWorkflow", search_attributes={"ReadRoles": stored_roles}
        )
    )
    assert await workflow_v1.is_authorized(reader_request, handle, "read")


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_type", ["RestrictedWorkflow", "UnknownWorkflow"])
@pytest.mark.parametrize("other_roles", [set(), {"administrator"}])
async def test_reader_needs_current_workflow_read_grant(
    reader_request: Request, workflow_type: str, other_roles: set[str]
) -> None:
    reader_request.state.roles.update(other_roles)
    handle = MagicMock()
    handle.describe = AsyncMock(
        return_value=MagicMock(
            workflow_type=workflow_type, search_attributes={"ReadRoles": ["api-reader"]}
        )
    )
    assert not await workflow_v1.is_authorized(reader_request, handle, "read")


@pytest.mark.asyncio
async def test_reader_gets_not_found_for_missing_workflow(reader_request: Request) -> None:
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("missing", RPCStatusCode.NOT_FOUND, b""))
    with pytest.raises(HTTPException) as error:
        await workflow_v1.is_authorized(reader_request, handle, "read")
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("other_roles", [set(), {"administrator"}])
async def test_reader_cannot_execute_workflow(
    reader_request: Request, other_roles: set[str]
) -> None:
    reader_request.state.roles.update(other_roles)
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(search_attributes={"ExecuteRoles": ["all"]}))
    assert not await workflow_v1.is_authorized(reader_request, handle, "execute")


def test_reader_cannot_start_public_workflow(reader_request: Request) -> None:
    config = MagicMock()
    config.get_workflow_roles.return_value = {"read_roles": {"all"}, "execute_roles": {"all"}}
    with pytest.raises(HTTPException) as error:
        require_workflow_execute_access(reader_request, "HelloWorld", rbac_config=config)
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "site, expected",
    [
        (None, "(WorkflowType = 'AllowedWorkflow')"),
        ("example", "Site = 'example' and (WorkflowType = 'AllowedWorkflow')"),
    ],
)
async def test_historical_list_keeps_filters_and_requires_current_read_grants(
    reader_request: Request,
    monkeypatch: pytest.MonkeyPatch,
    site: str | None,
    expected: str,
) -> None:
    client = MagicMock()
    client.count_workflows = AsyncMock(return_value=MagicMock(count=1))
    monkeypatch.setattr(workflow_v1, "get_client", AsyncMock(return_value=client))
    summaries = AsyncMock(return_value=([], None))
    monkeypatch.setattr(workflow_v1, "_get_workflow_summaries", summaries)

    await workflow_v1.get_workflows(reader_request, site=site)

    client.count_workflows.assert_awaited_once_with(expected)
    assert summaries.await_args.args[1] == expected


@pytest.mark.asyncio
async def test_reader_without_workflow_grants_gets_empty_list(
    reader_request: Request, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader_request.state.roles = {"all", "different-reader"}
    monkeypatch.setattr(
        auth, "_auth_config", auth.AuthConfig(read_only_groups=("different-reader",))
    )
    get_client = AsyncMock()
    monkeypatch.setattr(workflow_v1, "get_client", get_client)

    response = await workflow_v1.get_workflows(reader_request)

    assert response.workflows == []
    assert response.total_count == 0
    get_client.assert_not_awaited()
