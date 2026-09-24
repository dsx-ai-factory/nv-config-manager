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
"""V1 Workflow API Endpoints."""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import uuid4

import brotli
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, computed_field
from temporalio.client import (
    Client,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
    WorkflowQueryFailedError,
)
from temporalio.common import SearchAttributes
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.service import RPCError, RPCStatusCode

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.api.dynamic_endpoints import (
    get_registered_workflows_info,
    register_dynamic_endpoints,
    set_start_workflow_function,
)
from nv_config_manager.temporal.api.links import temporal_ui_workflow_href
from nv_config_manager.temporal.api.workflow_submission import resolve_workflow_references
from nv_config_manager.temporal.client.connection import client_connect_options, temporal_address
from nv_config_manager.temporal.client.redis import RedisClient
from nv_config_manager.temporal.common.mixins.base import BaseMixin
from nv_config_manager.temporal.common.mixins.stage import (
    ReviewSignalInput,
    Stage,
    StageMixin,
    StateEnum,
)
from nv_config_manager.temporal.common.rbac_config import RBACConfig
from nv_config_manager.temporal.common.search_attributes import (
    DEVICE_ID_SEARCH_ATTRIBUTE,
    DEVICE_NAME_SEARCH_ATTRIBUTE,
    DEVICE_PLATFORM_SEARCH_ATTRIBUTE,
    DEVICE_ROLE_SEARCH_ATTRIBUTE,
    EXECUTE_ROLES_SEARCH_ATTRIBUTE,
    FAILED_STAGE_SEARCH_ATTRIBUTE,
    PENDING_APPROVAL_SEARCH_ATTRIBUTE,
    READ_ROLES_SEARCH_ATTRIBUTE,
    SITE_SEARCH_ATTRIBUTE,
    USER_SEARCH_ATTRIBUTE,
)
from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.hello_world.workflows import (
    REGISTERED_WORKFLOWS as HELLO_WORLD_REGISTERED_WORKFLOWS,
)
from nv_config_manager.temporal.ngc.workflows import (
    REGISTERED_WORKFLOWS as NGC_REGISTERED_WORKFLOWS,
)
from nv_config_manager.temporal.telemetry import get_runtime

logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)

router = APIRouter(prefix="/workflow", tags=["workflow"])

_VISIBILITY_SAFE_VALUE = re.compile(r"^[\w.@:/ -]+$")
_WORKFLOW_LIST_QUERY_CONCURRENCY = 25
_WORKFLOW_LIST_QUERY_TIMEOUT_SECONDS = 2
_WORKFLOW_STAGE_QUERY_CACHE_NAMES = ("pending_approval", "compressed_stages")
_PENDING_APPROVAL_STATUS_VALUES = {
    StateEnum.PENDING_APPROVAL.value,
    "PENDING APPROVAL",
    "PENDINGAPPROVAL",
}
_FAILED_STATUS_VALUES = {
    StateEnum.FAILED.value,
    "FAILED",
}
_TEMPORAL_VISIBILITY_STATUS_VALUES = {
    "RUNNING": "Running",
    "COMPLETED": "Completed",
    "FAILED": "Failed",
    "CANCELED": "Canceled",
    "CANCELLED": "Canceled",
    "TERMINATED": "Terminated",
    "CONTINUED_AS_NEW": "ContinuedAsNew",
    "CONTINUED AS NEW": "ContinuedAsNew",
    "CONTINUEDASNEW": "ContinuedAsNew",
    "TIMED_OUT": "TimedOut",
    "TIMED OUT": "TimedOut",
    "TIMEDOUT": "TimedOut",
}


def _sanitize_visibility_value(value: str, param_name: str) -> str:
    """Validate a value is safe to embed in a Temporal Visibility query literal.

    Raises HTTPException(400) if the value contains characters that could
    allow query injection (single quotes, parentheses, SQL operators, etc.).
    """
    if not _VISIBILITY_SAFE_VALUE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid characters in query parameter '{param_name}'",
        )
    return value


def _format_visibility_time(value: datetime) -> str:
    """Return a UTC timestamp literal for Temporal visibility queries."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _format_visibility_bool(value: bool) -> str:
    """Return a Temporal visibility boolean literal."""
    return "true" if value else "false"


def _format_visibility_status(value: str) -> str:
    """Return a Temporal visibility status literal from an API status value."""
    normalized = value.upper()
    temporal_status = _TEMPORAL_VISIBILITY_STATUS_VALUES.get(normalized)
    if temporal_status is None:
        raise HTTPException(status_code=400, detail=f"Invalid workflow status '{value}'")
    return temporal_status


class WorkflowListResponse(BaseModel):
    """Workflow List Response Model."""

    workflows: list[WorkflowSummaryResponse]
    next_page_token: str | None
    total_count: int
    page_count: int


class WorkflowMetadata(BaseModel):
    """Workflow metadata."""

    name: str
    display_name: str
    description: str
    endpoint: str
    namespace: str | None
    cli_name: str
    input_class: str
    read_roles: list[str]
    execute_roles: list[str]


class WorkflowMetadataResponse(BaseModel):
    """Workflow metadata response."""

    workflows: list[WorkflowMetadata]


class WorkflowResponse(BaseModel):
    """Workflow Response Model."""

    id: str

    @computed_field
    def href(self) -> str:
        """Calculate URL to Temporal UI Workflow View."""
        return temporal_ui_workflow_href(self.id)


class WorkflowSummaryResponse(WorkflowResponse):
    """Workflow Summary Response for use in List API."""

    workflow_type: str
    workflow_input: Any
    started_by: str
    start_time: datetime
    close_time: datetime | None
    status: str
    pending_approval: bool
    failed_stage: bool
    search_attributes: SearchAttributes

    _NON_TERMINAL_STATES: ClassVar[set[StateEnum]] = {
        StateEnum.IN_PROGRESS,
        StateEnum.PENDING_APPROVAL,
        StateEnum.APPROVED,
        StateEnum.REJECTED,
    }
    _ACTIVE_WORKFLOW_STATUSES: ClassVar[set[WorkflowExecutionStatus]] = {
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.CONTINUED_AS_NEW,
    }
    _ACTIVE_DURABLE_CACHEABLE_QUERIES: ClassVar[set[str]] = {"input", "pending_approval"}

    @staticmethod
    def _temporal_status_name(status: WorkflowExecutionStatus | None) -> str:
        """Return the raw Temporal workflow status value."""
        return status.name if status else "UNKNOWN"

    @staticmethod
    def _bool_from_search_attributes(
        search_attributes: SearchAttributes, search_attribute: str
    ) -> bool | None:
        """Return an indexed boolean search attribute when present."""
        value = search_attributes.get(search_attribute)
        if not value:
            return None
        indexed_bool = value[0]
        return indexed_bool if isinstance(indexed_bool, bool) else None

    @staticmethod
    def _pending_approval_from_search_attributes(
        search_attributes: SearchAttributes,
    ) -> bool | None:
        """Return indexed pending-approval state when present."""
        return WorkflowSummaryResponse._bool_from_search_attributes(
            search_attributes, PENDING_APPROVAL_SEARCH_ATTRIBUTE
        )

    @staticmethod
    def _failed_stage_from_search_attributes(
        search_attributes: SearchAttributes,
    ) -> bool:
        """Return indexed failed-stage state when present."""
        return bool(
            WorkflowSummaryResponse._bool_from_search_attributes(
                search_attributes, FAILED_STAGE_SEARCH_ATTRIBUTE
            )
        )

    @staticmethod
    def _should_read_cache(query: str, is_active: bool) -> bool:
        """Return whether the query should read the durable workflow query cache."""
        return not is_active or query in WorkflowSummaryResponse._ACTIVE_DURABLE_CACHEABLE_QUERIES

    @staticmethod
    def _should_cache(query: str, data: Any, is_active: bool) -> bool:
        """Return whether the query should write the durable workflow query cache."""
        if is_active:
            if query == "input":
                return True
            if query == "pending_approval":
                return bool(data)
            return False
        if query != "compressed_stages" or data is None:
            return True
        try:
            stages = StageMixin.decompress_stages(data)
            return not any(s.state in WorkflowSummaryResponse._NON_TERMINAL_STATES for s in stages)
        except Exception:
            return False

    @staticmethod
    async def _execute_queries(
        handle: WorkflowHandle,
        description: WorkflowExecutionDescription,
        queries: list[str],
        query_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        is_active = description.status in WorkflowSummaryResponse._ACTIVE_WORKFLOW_STATUSES
        cache = RedisClient.from_config(load_config())
        results = {}

        for query in queries:
            try:
                if WorkflowSummaryResponse._should_read_cache(query, is_active):
                    data = await cache.get_cached_query(handle.id, query)
                    if data is not None:
                        results[query] = data
                        continue

                if query_timeout_seconds is None:
                    data = await handle.query(query)
                else:
                    try:
                        async with asyncio.timeout(query_timeout_seconds):
                            data = await handle.query(query)
                    except TimeoutError:
                        logger.warning(
                            "Workflow %s of type %s timed out after %s seconds running the %s "
                            "query while building a list response.",
                            handle.id,
                            description.workflow_type,
                            query_timeout_seconds,
                            query,
                        )
                        results[query] = None
                        continue

                if WorkflowSummaryResponse._should_cache(query, data, is_active):
                    await cache.cache_query(handle.id, query, data)

                results[query] = data
            except WorkflowQueryFailedError:
                logger.exception(
                    "Workflow %s of type %s failed the %s query.",
                    handle.id,
                    description.workflow_type,
                    query,
                )
                results[query] = None
        return results

    @staticmethod
    async def from_handle(handle: WorkflowHandle) -> WorkflowSummaryResponse:
        """Generate a summary response from a WorkflowHandle object."""
        try:
            description: WorkflowExecutionDescription = await handle.describe()
        except RPCError as e:
            # If workflow doesn't exist, user is not authorized to access it
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(
                    status_code=404, detail=f"Workflow with ID '{handle.id}' not found"
                ) from e
            else:
                raise
        try:
            pending_approval = WorkflowSummaryResponse._pending_approval_from_search_attributes(
                description.search_attributes
            )
            failed_stage = WorkflowSummaryResponse._failed_stage_from_search_attributes(
                description.search_attributes
            )
            queries = ["input"] if pending_approval is not None else ["pending_approval", "input"]
            query_results = await WorkflowSummaryResponse._execute_queries(
                handle,
                description,
                queries,
                query_timeout_seconds=_WORKFLOW_LIST_QUERY_TIMEOUT_SECONDS,
            )
            workflow_input = query_results.get("input")
            if pending_approval is None:
                pending_approval = bool(query_results.get("pending_approval"))
        except RPCError:
            # Should not occur in prod, but there was a point in time in
            # test in which signal functions would throw Exceptions when
            # unexpected input was supplied. Temporal does not like this
            # and the workflow ends up wedged.
            logger.exception(
                "Workflow %s of type %s is in a bad state and cannot accept queries.",
                handle.id,
                description.workflow_type,
            )
            pending_approval = False
            failed_stage = False
            workflow_input = None

        # Stage-state search attributes record the last state observed by the
        # workflow. Temporal termination does not give workflow code a chance
        # to clear them, so they must not describe a closed execution as
        # currently awaiting approval.
        pending_approval = description.status == WorkflowExecutionStatus.RUNNING and bool(
            pending_approval
        )

        try:
            user = cast(str, description.search_attributes[USER_SEARCH_ATTRIBUTE][0])
        except (KeyError, IndexError):
            user = "unknown"

        return WorkflowSummaryResponse(
            id=handle.id,
            started_by=user,
            workflow_input=workflow_input,
            status=WorkflowSummaryResponse._temporal_status_name(description.status),
            start_time=description.start_time,
            close_time=description.close_time,
            workflow_type=description.workflow_type,
            pending_approval=pending_approval,
            failed_stage=failed_stage,
            search_attributes=description.search_attributes,
        )

    @staticmethod
    async def from_handle_with_semaphore(
        handle: WorkflowHandle, semaphore: asyncio.Semaphore
    ) -> WorkflowSummaryResponse:
        """Generate a summary response from a WorkflowHandle object."""
        async with semaphore:
            return await WorkflowSummaryResponse.from_handle(handle)


class WorkflowDetailResponse(WorkflowSummaryResponse):
    """Workflow Detail Response."""

    stages: list[Stage]
    result: Any

    @staticmethod
    async def from_handle(handle: WorkflowHandle) -> WorkflowDetailResponse:
        """Generate a detailed response from a WorkflowHandle object."""
        try:
            description: WorkflowExecutionDescription = await handle.describe()
        except RPCError as e:
            # If workflow doesn't exist, user is not authorized to access it
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(
                    status_code=404, detail=f"Workflow with ID '{handle.id}' not found"
                ) from e
            else:
                raise
        # Not every workflow is guaranteed to implement every query
        try:
            pending_approval = WorkflowSummaryResponse._pending_approval_from_search_attributes(
                description.search_attributes
            )
            failed_stage = WorkflowSummaryResponse._failed_stage_from_search_attributes(
                description.search_attributes
            )
            queries = (
                ["input", "compressed_stages"]
                if pending_approval is not None
                else ["pending_approval", "input", "compressed_stages"]
            )
            query_results = await WorkflowSummaryResponse._execute_queries(
                handle, description, queries
            )
            workflow_input = query_results.get("input")
            if pending_approval is None:
                pending_approval = bool(query_results.get("pending_approval"))

            # Decompress and parse stages
            compressed_stages = query_results.get("compressed_stages")
            stages = []
            if compressed_stages is not None:
                stages = StageMixin.decompress_stages(compressed_stages)
                failed_stage = any(stage.state == StateEnum.FAILED for stage in stages)

        except RPCError:
            # Should not occur in prod, but there was a point in time in
            # test in which signal functions would throw Exceptions when
            # unexpected input was supplied. Temporal does not like this
            # and the workflow ends up wedged.
            logger.exception(
                "Workflow %s of type %s is in a bad state and cannot accept queries.",
                handle.id,
                description.workflow_type,
            )
            pending_approval = False
            failed_stage = False
            workflow_input = None
            stages = []

        # A closed execution cannot still be waiting for an approval, even if
        # its final indexed stage state was PENDING_APPROVAL.
        pending_approval = description.status == WorkflowExecutionStatus.RUNNING and bool(
            pending_approval
        )

        result = None
        if description.status == WorkflowExecutionStatus.COMPLETED:
            cache = RedisClient.from_config(load_config())
            result = await cache.get_cached_result(handle.id)
            if result is None:
                result = await handle.result()
                await cache.cache_result(handle.id, result)

        try:
            user = cast(str, description.search_attributes[USER_SEARCH_ATTRIBUTE][0])
        except (KeyError, IndexError):
            user = "unknown"

        return WorkflowDetailResponse(
            id=handle.id,
            started_by=user,
            workflow_input=workflow_input,
            status=WorkflowSummaryResponse._temporal_status_name(description.status),
            start_time=description.start_time,
            close_time=description.close_time,
            workflow_type=description.workflow_type,
            pending_approval=pending_approval,
            failed_stage=failed_stage,
            stages=stages,
            result=result,
            search_attributes=description.search_attributes,
        )


async def get_client() -> Client:
    """Create a temporal client."""
    return await Client.connect(
        temporal_address(),
        **client_connect_options(),
        data_converter=get_data_converter(),
        interceptors=[TracingInterceptor(always_create_workflow_spans=True)],
        runtime=get_runtime(),
    )


def get_user_info(request: Request) -> tuple[str, set[str]]:
    """Extract the user data from request data."""
    return request.state.user, request.state.roles


async def cache_workflow_input(workflow_id: str, body: BaseModel) -> None:
    """Cache workflow input immediately after workflow creation."""
    cache = RedisClient.from_config(load_config())
    await cache.cache_query(workflow_id, "input", body.model_dump(mode="json"))


async def start_workflow(
    request: Request,
    workflow_class: type[BaseMixin],
    body: BaseModel,
    search_attributes: dict[str, Any] | None = None,
) -> str:
    """Start a workflow with the given input."""

    request.state.audit_workflow_type = workflow_class.__name__
    user, roles = get_user_info(request)

    # Check if the user is authorized to execute the workflow
    rbac_config = RBACConfig()
    workflow_roles = rbac_config.get_workflow_roles(workflow_class.__name__)

    if workflow_roles:
        # Use the roles from the config
        execute_roles = workflow_roles["execute_roles"]
        read_roles = workflow_roles["read_roles"]

        # Check if the user is authorized
        if not (execute_roles.intersection(roles) or "all" in execute_roles):
            logger.error(
                "User %s with roles %s is not authorized to execute workflow %s",
                user,
                roles,
                workflow_class.__name__,
            )
            raise HTTPException(status_code=403, detail="Not authorized to execute this workflow")
    else:
        # No RBAC config for this workflow, deny access
        logger.error(
            "No RBAC configuration found for workflow %s",
            workflow_class.__name__,
        )
        raise HTTPException(status_code=403, detail="No RBAC configuration found for this workflow")

    # Prepare search attributes
    if search_attributes is None:
        search_attributes = {}
    search_attributes[USER_SEARCH_ATTRIBUTE] = [user]
    search_attributes[READ_ROLES_SEARCH_ATTRIBUTE] = sorted(read_roles)
    search_attributes[EXECUTE_ROLES_SEARCH_ATTRIBUTE] = sorted(execute_roles)
    search_attributes.setdefault(PENDING_APPROVAL_SEARCH_ATTRIBUTE, [False])
    search_attributes.setdefault(FAILED_STAGE_SEARCH_ATTRIBUTE, [False])

    search_attributes.update(await resolve_workflow_references(body))

    client = await get_client()
    handle: WorkflowHandle = await client.start_workflow(
        workflow_class.run,
        body,
        id=str(uuid4()),
        task_queue="default-task-queue",
        search_attributes=search_attributes,
    )
    request.state.audit_workflow_id = handle.id
    try:
        await cache_workflow_input(handle.id, body)
    except Exception:
        logger.exception("Failed to proactively cache workflow input for %s", handle.id)
    return handle.id


async def is_authorized(request: Request, handle: WorkflowHandle, action: str) -> bool:
    """Check if the user is authorized to perform the action on the workflow."""
    user, roles = get_user_info(request)
    rbac_config = RBACConfig()
    # Admin roles are always permitted
    # This is to handle workflows that predate any RBAC setup
    if roles.intersection(rbac_config.get_admin_roles()):
        return True

    try:
        description: WorkflowExecutionDescription = await handle.describe()
    except RPCError as e:
        # If workflow doesn't exist, user is not authorized to access it
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(
                status_code=404, detail=f"Workflow with ID '{handle.id}' not found"
            ) from e
        else:
            # Re-raise if it's a different type of error
            raise

    # convert roles to string set for comparison
    attr = EXECUTE_ROLES_SEARCH_ATTRIBUTE if action == "execute" else READ_ROLES_SEARCH_ATTRIBUTE
    permitted_roles = description.search_attributes.get(attr, [])
    if not permitted_roles:
        logger.error(
            "Workflow %s of type %s does not have %s search attributes",
            handle.id,
            description.workflow_type,
            attr,
        )
        return False
    if not roles.intersection(permitted_roles):
        logger.error(
            "User %s with roles %s does not have permission to %s workflow %s of type %s",
            user,
            roles,
            action,
            handle.id,
            description.workflow_type,
        )
        return False
    return True


async def signal_workflow(request: Request, workflow_id: str, signal_name: str, *args: Any) -> None:
    """Send signal to workflow."""
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id)

    if await is_authorized(request, handle, "execute"):
        await handle.signal(signal_name, *args)
        await invalidate_workflow_stage_query_cache(workflow_id)
    else:
        raise HTTPException(status_code=403, detail="Forbidden")


async def invalidate_workflow_stage_query_cache(workflow_id: str) -> None:
    """Invalidate cached workflow query data that changes when stages change."""
    cache = RedisClient.from_config(load_config())
    try:
        await asyncio.gather(
            *(
                cache.delete_cached_query(workflow_id, query)
                for query in _WORKFLOW_STAGE_QUERY_CACHE_NAMES
            )
        )
    except Exception:
        logger.exception("Failed to invalidate workflow stage query cache for %s", workflow_id)


async def _get_workflow_summary_page(
    client: Client,
    query: str | None,
    limit: int,
    next_page_token: bytes | None,
    semaphore: asyncio.Semaphore,
) -> tuple[list[WorkflowSummaryResponse], bytes | None]:
    """Fetch and summarize one Temporal workflow visibility page."""
    tasks = []
    aiter = client.list_workflows(
        query, limit=limit, page_size=limit, next_page_token=next_page_token
    )

    async for workflow in aiter:
        handle = client.get_workflow_handle(workflow.id)
        tasks.append(
            asyncio.create_task(
                WorkflowSummaryResponse.from_handle_with_semaphore(handle, semaphore)
            )
        )

    workflows = await asyncio.gather(*tasks)
    return workflows, aiter.next_page_token


async def _get_workflow_summaries(
    client: Client,
    query: str | None,
    limit: int,
    next_page_token: bytes | None,
) -> tuple[list[WorkflowSummaryResponse], bytes | None]:
    """Fetch workflow summaries for one Temporal visibility page."""
    semaphore = asyncio.Semaphore(_WORKFLOW_LIST_QUERY_CONCURRENCY)
    return await _get_workflow_summary_page(client, query, limit, next_page_token, semaphore)


@router.get("/")
async def get_workflows(  # pylint: disable=R0913,R0914
    request: Request,
    user: str | None = None,
    workflow_type: str | None = None,
    workflow_id: str | None = None,
    device_id: str | None = None,
    device_name: str | None = None,
    device_role: str | None = None,
    device_platform: str | None = None,
    site: str | None = None,
    status: str | None = None,
    pending_approval: bool | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    hide_completed: bool = False,
    limit: int = 100,
    next_page_token: str | None = None,
) -> WorkflowListResponse:
    """Return a filtered list of workflows."""
    _, user_roles = get_user_info(request)
    filters = []
    pending_approval_filter = pending_approval
    if user:
        filters.append(f"{USER_SEARCH_ATTRIBUTE} = '{_sanitize_visibility_value(user, 'user')}'")
    if workflow_type:
        filters.append(
            f"WorkflowType = '{_sanitize_visibility_value(workflow_type, 'workflow_type')}'"
        )
    if workflow_id:
        filters.append(f"WorkflowId = '{_sanitize_visibility_value(workflow_id, 'workflow_id')}'")
    if device_id:
        filters.append(
            f"{DEVICE_ID_SEARCH_ATTRIBUTE} = '{_sanitize_visibility_value(device_id, 'device_id')}'"
        )
    if device_name:
        filters.append(
            f"{DEVICE_NAME_SEARCH_ATTRIBUTE} = "
            f"'{_sanitize_visibility_value(device_name, 'device_name')}'"
        )
    if device_role:
        filters.append(
            f"{DEVICE_ROLE_SEARCH_ATTRIBUTE} = "
            f"'{_sanitize_visibility_value(device_role, 'device_role')}'"
        )
    if device_platform:
        filters.append(
            f"{DEVICE_PLATFORM_SEARCH_ATTRIBUTE} = "
            f"'{_sanitize_visibility_value(device_platform, 'device_platform')}'"
        )
    if site:
        filters.append(f"{SITE_SEARCH_ATTRIBUTE} = '{_sanitize_visibility_value(site, 'site')}'")
    if status:
        sanitized_status = _sanitize_visibility_value(status, "status")
        if sanitized_status.upper() in _PENDING_APPROVAL_STATUS_VALUES:
            filters.append("ExecutionStatus = 'Running'")
            pending_approval_filter = True
        elif sanitized_status.upper() in _FAILED_STATUS_VALUES:
            filters.append(
                f"(ExecutionStatus = 'Failed' or {FAILED_STAGE_SEARCH_ATTRIBUTE} = true)"
            )
        else:
            filters.append(f"ExecutionStatus = '{_format_visibility_status(sanitized_status)}'")
    elif pending_approval is True:
        filters.append("ExecutionStatus = 'Running'")
    if pending_approval_filter is not None:
        filters.append(
            f"{PENDING_APPROVAL_SEARCH_ATTRIBUTE} = "
            f"{_format_visibility_bool(pending_approval_filter)}"
        )
    if start_time:
        filters.append(f"StartTime >= '{_format_visibility_time(start_time)}'")
    if end_time:
        filters.append(f"CloseTime <= '{_format_visibility_time(end_time)}'")
    if hide_completed:
        filters.append("ExecutionStatus != 'Completed'")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0")
    # Add role filters
    # Permit admin roles to view workflows that are missing
    # RBAC search attributes
    admin_roles = RBACConfig().get_admin_roles()
    if not admin_roles.intersection(user_roles):
        role_filters = [
            f"{READ_ROLES_SEARCH_ATTRIBUTE} = '{_sanitize_visibility_value(role, 'role')}'"
            for role in sorted(user_roles)
        ]
        role_filter = " or ".join(role_filters)
        filters.append(f"({role_filter})")
    query = " and ".join(filters) if filters else None

    client = await get_client()

    next_page_token = (
        brotli.decompress(base64.urlsafe_b64decode(next_page_token)) if next_page_token else None
    )
    (workflows, new_token), workflow_count = await asyncio.gather(
        _get_workflow_summaries(client, query, limit, next_page_token),
        client.count_workflows(query),
    )
    encoded_token = (
        base64.urlsafe_b64encode(brotli.compress(new_token)).decode() if new_token else None
    )
    page_count = 0 if workflow_count.count == 0 else (workflow_count.count + limit - 1) // limit

    return WorkflowListResponse(
        workflows=workflows,
        next_page_token=encoded_token,
        total_count=workflow_count.count,
        page_count=page_count,
    )


@router.get("/types")
async def get_workflow_types() -> list[str]:
    """Return registered workflow type names."""
    return sorted(
        [wf.__name__ for wf in NGC_REGISTERED_WORKFLOWS + HELLO_WORLD_REGISTERED_WORKFLOWS]
    )


@router.get("/metadata")
async def get_workflow_metadata() -> WorkflowMetadataResponse:
    """Return registered workflow metadata and RBAC roles."""
    workflow_types = sorted(
        [wf.__name__ for wf in NGC_REGISTERED_WORKFLOWS + HELLO_WORLD_REGISTERED_WORKFLOWS]
    )

    workflows_info = get_registered_workflows_info(include_rbac=True)
    workflows = [
        WorkflowMetadata.model_validate(workflows_info[name])
        for name in workflow_types
        if name in workflows_info
    ]
    return WorkflowMetadataResponse(workflows=workflows)


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request) -> WorkflowDetailResponse:
    """Get workflow execution details."""
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id)
    if await is_authorized(request, handle, "read"):
        return await WorkflowDetailResponse.from_handle(handle)
    else:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/{workflow_id}/approve/{stage_name}")
async def approve(workflow_id: str, stage_name: str, request: Request) -> WorkflowResponse:
    """Send the approve signal to a workflow."""
    user, _ = get_user_info(request)
    await signal_workflow(
        request,
        workflow_id,
        "approve",
        ReviewSignalInput(user=user, stage_name=stage_name),
    )
    return WorkflowResponse(id=workflow_id)


@router.post("/{workflow_id}/reject/{stage_name}")
async def reject(workflow_id: str, stage_name: str, request: Request) -> WorkflowResponse:
    """Send the reject signal to a workflow."""
    user, _ = get_user_info(request)
    await signal_workflow(
        request,
        workflow_id,
        "reject",
        ReviewSignalInput(user=user, stage_name=stage_name),
    )
    return WorkflowResponse(id=workflow_id)


@router.post("/{workflow_id}/retry/{stage_name}")
async def retry(workflow_id: str, stage_name: str, request: Request) -> WorkflowResponse:
    """Send the retry signal for the given stage to a workflow."""
    await signal_workflow(request, workflow_id, "retry", stage_name)
    return WorkflowResponse(id=workflow_id)


@router.get("/{workflow_id}/tech-support/{device_name}")
async def download_tech_support(workflow_id: str, device_name: str, request: Request) -> Response:
    """Download a tech-support bundle stored in Redis by the diagnostics workflow."""
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id)
    if not await is_authorized(request, handle, "read"):
        raise HTTPException(status_code=403, detail="Forbidden")

    redis_key = f"tech_support:{workflow_id}:{device_name}"
    cache = RedisClient.from_config(load_config())
    content: bytes | None = await cache.get(redis_key, deserialize=False)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tech-support bundle for '{device_name}' not found (key={redis_key}). It may have expired.",
        )
    safe_name = re.sub(r'[\x00-\x1f\x7f"\\]', "", device_name)
    return Response(
        content=content,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="tech-support_{safe_name}.tar.gz"'},
    )


@router.post("/{workflow_id}/terminate")
async def terminate(workflow_id: str, request: Request) -> WorkflowResponse:
    """Terminate a running workflow."""
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id=workflow_id)

    if await is_authorized(request, handle, "execute"):
        try:
            description = await handle.describe()
            if description.status != WorkflowExecutionStatus.RUNNING:
                status_name = description.status.name if description.status else "UNKNOWN"
                raise HTTPException(
                    status_code=400,
                    detail=f"Workflow is not running (status: {status_name})",
                )
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(
                    status_code=404, detail=f"Workflow with ID '{workflow_id}' not found"
                ) from e
            raise
        await handle.terminate()
        await invalidate_workflow_stage_query_cache(workflow_id)
    else:
        raise HTTPException(status_code=403, detail="Forbidden")

    return WorkflowResponse(id=workflow_id)


# Register dynamic endpoints for workflows with metadata

# Set the start_workflow function to avoid circular imports
set_start_workflow_function(start_workflow)

# Register dynamic endpoints
register_dynamic_endpoints(router)
