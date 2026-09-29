# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporal execution fixtures for package-owned workflow tests."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from unittest.mock import AsyncMock, Mock

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

from nv_config_manager_workflows.activities.nats import PublishNatsInput
from nv_config_manager_workflows.activities.slack import SlackMessageInput
from nv_config_manager_workflows.clients.device.base import NetworkConnection
from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.runtime import (
    NatsRuntime,
    configure_device_connection,
    configure_nats,
)
from nv_config_manager_workflows.search_attributes import (
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
    """Register the package workflows' custom search attributes."""
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes=_SEARCH_ATTRIBUTES,
        )
    )
    try:
        await env.client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=env.client.namespace)
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.UNIMPLEMENTED:
            raise


@activity.defn(name="publish_nats")
async def mock_publish_nats(_activity_input: PublishNatsInput) -> None:
    """Accept an archive activity without external NATS I/O."""


@activity.defn(name="send_slack_message")
async def mock_send_slack_message(_activity_input: SlackMessageInput) -> None:
    """Accept a Slack activity without external I/O."""


@pytest.fixture
def mock_cumulus_connection(configured_workflow_runtime: None) -> Mock:
    """Inject a reusable mock through the package device provider boundary."""
    provider = Mock()
    provider.return_value = Mock(spec=NetworkConnection)
    configure_device_connection(provider)
    return provider


@pytest.fixture
def mock_nats_client(configured_workflow_runtime: None) -> Mock:
    """Inject a recording publisher through the package NATS provider boundary."""
    factory = Mock()
    publisher = factory.return_value
    publisher.server = "nats://test.invalid:4222"
    publisher.publish = AsyncMock()
    configure_nats(
        lambda: NatsRuntime(
            publisher=publisher,
            stream="test-workflow-events",
            subject="test.workflow.result",
        )
    )
    return factory


@pytest_asyncio.fixture(scope="session")
async def env() -> AsyncGenerator[WorkflowEnvironment]:
    """Start one local Temporal environment for workflow execution tests."""
    environment = await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(),
        dev_server_extra_args=[
            "--dynamic-config-value",
            "system.forceSearchAttributesCacheRefreshOnRead=true",
        ],
    )
    await _register_search_attributes(environment)
    yield environment
    await environment.shutdown()


@pytest.fixture
def time_skipping_env() -> Callable[[], AbstractAsyncContextManager[WorkflowEnvironment]]:
    """Return an isolated time-skipping Temporal environment."""

    @asynccontextmanager
    async def _environment() -> AsyncIterator[WorkflowEnvironment]:
        environment = await WorkflowEnvironment.start_time_skipping(
            data_converter=get_data_converter()
        )
        try:
            await _register_search_attributes(environment)
            yield environment
        finally:
            await environment.shutdown()

    return _environment
