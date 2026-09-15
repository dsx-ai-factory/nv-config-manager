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
"""Temporal test configuration - INI mocking handled by top-level conftest.py."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from nv_config_manager.temporal.common.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    ISSUE_KEY_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.activities.nats import PublishNatsInput
from nv_config_manager.temporal.ngc.activities.slack import SlackMessageInput

_SEARCH_ATTRIBUTES = {
    USER_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    DEVICE_ID_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    DEVICE_ROLE_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    SITE_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    DEVICE_NAME_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_TEXT,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    READ_ROLES_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
    FAILED_STAGE_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
    ISSUE_KEY_SEARCH_ATTRIBUTE: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
}


async def _register_search_attributes(env: WorkflowEnvironment) -> None:
    """Register custom search attributes for a Temporal test environment."""
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes=_SEARCH_ATTRIBUTES,
        )
    )
    try:
        # Read to force the cache refresh when the test server supports it.
        await env.client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=env.client.namespace)
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.UNIMPLEMENTED:
            raise


@activity.defn(name="publish_nats")
async def mock_publish_nats(activity_input: PublishNatsInput) -> None:
    """No-op mock for the publish_nats activity."""


@activity.defn(name="send_slack_message")
async def mock_send_slack_message(activity_input: SlackMessageInput) -> None:
    """No-op mock for the send_slack_message activity."""


# UNCOMMENT THIS WHEN TROUBLESHOOTING HUNG TESTS
# WILL BREAK ANY TESTS THAT ARE ACTUALLY TESTING RETRY LOGIC
# @pytest.fixture(autouse=True)
# def mock_workflow_wait_condition(mocker):
#     """Mock workflow wait_condition to prevent tests from hanging on retry logic."""
#     # Simple mock that returns immediately to prevent hanging
#     mock_wait = AsyncMock(return_value=None)
#     mocker.patch("nv_config_manager_workflows.stage.mixin.workflow.wait_condition", mock_wait)


@pytest.fixture(autouse=True)
def disable_workflow_lock_io(mocker) -> dict[str, Any]:
    """Keep the per-resource workflow lock out of Redis and out of the way in tests.

    Acquire and release still run as no-ops, so the lock wiring is exercised end to
    end, but the renewal loop is neutralized so time-skipping tests don't spin on
    its timer. Renewal and the Redis backend are covered by the dedicated lock
    tests (``tests/common/test_lock*`` and ``tests/temporal/common/test_workflow_lock``).

    Returns the acquire/renew/release helper mocks so tests that want to assert on
    the lock wiring can request this fixture by name.
    """
    mocks = {
        helper: mocker.patch(
            f"nv_config_manager.temporal.common.activities.lock.{helper}",
            new=mocker.AsyncMock(return_value=True),
        )
        for helper in ("acquire_lock", "renew_lock", "release_lock")
    }

    async def _never_renew(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    mocker.patch(
        "nv_config_manager.temporal.common.decorators.workflow._renew_loop",
        new=_never_renew,
    )
    return mocks


@pytest.fixture(autouse=True)
def bmc_creds(mocker):
    """Mock BMC creds."""

    def mock_creds() -> Any:
        return {
            "C8-4B-D6-7A-E9-E2": {
                "default_user": "USER1",
                "default_password": "PASSWORD1",
                "config_manager_password": "CONFIGMANAGERPASSWORD1",
            },
            "C8-4B-D6-7A-E8-F2": {
                "default_user": "USER2",
                "default_password": "PASSWORD2",
                "config_manager_password": "CONFIGMANAGERPASSWORD2",
            },
            "D0-8E-79-F8-92-44": {
                "default_user": "USER3",
                "default_password": "PASSWORD3",
                "config_manager_password": "CONFIGMANAGERPASSWORD3",
            },
            "38-7C-76-8D-6F-13": {
                "default_user": "USER4",
                "default_password": "PASSWORD4",
                "config_manager_password": "CONFIGMANAGERPASSWORD4",
            },
            "58-A2-E1-84-74-FB": {
                "default_user": "USER5",
                "default_password": "PASSWORD5",
                "config_manager_password": "CONFIGMANAGERPASSWORD5",
            },
            "58-A2-E1-72-DD-C5": {
                "default_user": "USER6",
                "default_password": "PASSWORD6",
                "config_manager_password": "CONFIGMANAGERPASSWORD6",
            },
        }

    mocker.patch("nv_config_manager.temporal.client.redfish.get_bmc_creds", new=mock_creds)


@pytest_asyncio.fixture(scope="session")
async def env() -> AsyncGenerator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(),
        dev_server_extra_args=[
            "--dynamic-config-value",
            "system.forceSearchAttributesCacheRefreshOnRead=true",
        ],
    )
    await _register_search_attributes(env)
    yield env
    await env.shutdown()


@pytest.fixture()
def time_skipping_env() -> Callable[[], AbstractAsyncContextManager[WorkflowEnvironment]]:
    """Return a time-skipping Temporal environment with custom search attributes."""

    @asynccontextmanager
    async def _env() -> AsyncIterator[WorkflowEnvironment]:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=get_data_converter())
        try:
            await _register_search_attributes(env)
            yield env
        finally:
            await env.shutdown()

    return _env
