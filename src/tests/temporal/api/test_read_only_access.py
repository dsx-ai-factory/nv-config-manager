# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Readers can inspect historical workflows without inheriting execution access."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from temporalio.service import RPCError, RPCStatusCode

from nv_config_manager.common import auth
from nv_config_manager.temporal.api import workflow_v1
from nv_config_manager.temporal.api.workflow_authorization import require_workflow_execute_access


@pytest.fixture
def reader_request(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_auth_config",
        auth.AuthConfig(required=False, read_only_groups=("api-reader",)),
    )
    monkeypatch.setattr(auth, "_auth_config_tracks_file", False)
    request = Request({"type": "http", "method": "GET", "path": "/v1/workflow", "headers": []})
    request.state.user = "reader"
    request.state.roles = {"all", "api-reader"}
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_roles", [[], ["old-role"]])
async def test_reader_can_inspect_historical_workflow(reader_request, stored_roles):
    handle = MagicMock()
    handle.describe = AsyncMock(
        return_value=MagicMock(search_attributes={"ReadRoles": stored_roles})
    )
    assert await workflow_v1.is_authorized(reader_request, handle, "read")


@pytest.mark.asyncio
async def test_reader_gets_not_found_for_missing_workflow(reader_request):
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("missing", RPCStatusCode.NOT_FOUND, b""))
    with pytest.raises(HTTPException) as error:
        await workflow_v1.is_authorized(reader_request, handle, "read")
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("other_roles", [set(), {"administrator"}])
async def test_reader_cannot_execute_workflow(reader_request, other_roles):
    reader_request.state.roles.update(other_roles)
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(search_attributes={"ExecuteRoles": ["all"]}))
    assert not await workflow_v1.is_authorized(reader_request, handle, "execute")


def test_reader_cannot_start_public_workflow(reader_request):
    config = MagicMock()
    config.get_workflow_roles.return_value = {"read_roles": {"all"}, "execute_roles": {"all"}}
    with pytest.raises(HTTPException) as error:
        require_workflow_execute_access(reader_request, "HelloWorld", rbac_config=config)
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("site, expected", [(None, None), ("example", "Site = 'example'")])
async def test_historical_list_keeps_filters_without_reader_role_filter(
    reader_request,
    monkeypatch,
    site,
    expected,
):
    client = MagicMock()
    client.count_workflows = AsyncMock(return_value=MagicMock(count=1))
    monkeypatch.setattr(workflow_v1, "get_client", AsyncMock(return_value=client))
    summaries = AsyncMock(return_value=([], None))
    monkeypatch.setattr(workflow_v1, "_get_workflow_summaries", summaries)

    await workflow_v1.get_workflows(reader_request, site=site)

    client.count_workflows.assert_awaited_once_with(expected)
    assert summaries.await_args.args[1] == expected
