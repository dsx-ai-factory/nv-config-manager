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
"""Typed API routes for workflow-owned form option providers."""

import asyncio
import copy
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.api.workflow_authorization import (
    require_workflow_execute_access,
)
from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.form_catalog import (
    FormOptionProviderBinding,
    WorkflowFormCatalog,
)
from nv_config_manager_workflows.registration.form_provider_validation import (
    ResolvedFormOptionProvider,
    resolve_form_option_provider,
)
from nv_config_manager_workflows.ui import OptionSourceResponse, WorkflowFormContractError

logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)

_PUBLIC_WORKFLOW_PREFIX = "/v1/workflow"
_PROVIDER_CONCURRENCY = 25
_PROVIDER_TIMEOUT_SECONDS = 10.0
_PROVIDER_MAX_ITEMS = 1000
_provider_limiter = asyncio.Semaphore(_PROVIDER_CONCURRENCY)


class ApiErrorResponse(BaseModel):
    """A sanitized API error response."""

    detail: str


@dataclass(frozen=True, slots=True)
class WorkflowFormSurface:
    """An atomically finalized form catalog and its successfully built routes."""

    catalog: WorkflowFormCatalog
    router: APIRouter


def build_workflow_form_surface(candidate: WorkflowFormCatalog) -> WorkflowFormSurface:
    """Resolve referenced providers and publish only forms whose routes all compile."""
    catalog = copy.copy(candidate)
    catalog.forms = dict(candidate.forms)
    catalog.form_ids = dict(candidate.form_ids)
    catalog.diagnostics = dict(candidate.diagnostics)
    catalog.providers = tuple(candidate.providers)

    router = APIRouter()
    by_workflow: dict[type[WorkflowMetadataMixin], list[FormOptionProviderBinding]] = defaultdict(
        list
    )
    for binding in catalog.providers:
        by_workflow[binding.workflow].append(binding)

    registered_paths: set[str] = set()
    operation_ids: set[str] = set()
    for workflow, bindings in by_workflow.items():
        workflow_router = APIRouter()
        candidate_paths: set[str] = set()
        candidate_operation_ids: set[str] = set()
        try:
            for binding in bindings:
                resolved = resolve_form_option_provider(binding)
                relative_path = _relative_provider_path(binding)
                operation_id = _provider_operation_id(binding)
                if relative_path in registered_paths | candidate_paths:
                    raise WorkflowFormContractError(
                        f"duplicate form option provider path {binding.endpoint!r}"
                    )
                if operation_id in operation_ids | candidate_operation_ids:
                    raise WorkflowFormContractError(
                        f"duplicate form option provider operation ID {operation_id!r}"
                    )
                endpoint = create_form_option_endpoint(binding, resolved)
                workflow_router.get(
                    relative_path,
                    response_model=OptionSourceResponse,
                    response_model_exclude_none=True,
                    operation_id=operation_id,
                    summary=(f"Get {binding.source} options for {binding.workflow.__name__}"),
                    responses={
                        403: {"model": ApiErrorResponse, "description": "Forbidden"},
                        502: {
                            "model": ApiErrorResponse,
                            "description": "The option provider failed or returned invalid data.",
                        },
                        504: {
                            "model": ApiErrorResponse,
                            "description": "The option provider timed out.",
                        },
                    },
                )(endpoint)
                candidate_paths.add(relative_path)
                candidate_operation_ids.add(operation_id)
        except Exception as error:
            logger.warning(
                "Form option routes unavailable for workflow %s from plugin %s (%s)",
                workflow.__name__,
                bindings[0].plugin,
                type(error).__name__,
                extra={
                    "event_type": "workflow_form_option_routes_unavailable",
                    "plugin": bindings[0].plugin,
                    "workflow": workflow.__name__,
                    "error_type": type(error).__name__,
                },
            )
            catalog.unavailable(workflow, error)
            continue

        router.include_router(workflow_router)
        registered_paths.update(candidate_paths)
        operation_ids.update(candidate_operation_ids)

    return WorkflowFormSurface(catalog=catalog, router=router)


def create_form_option_endpoint(
    binding: FormOptionProviderBinding,
    resolved: ResolvedFormOptionProvider,
) -> Callable[..., Awaitable[OptionSourceResponse]]:
    """Create a typed FastAPI endpoint with centralized provider policy."""

    async def form_option_endpoint(query: Any, request: Request) -> OptionSourceResponse:
        require_workflow_execute_access(request, binding.workflow.__name__)
        try:
            async with asyncio.timeout(_PROVIDER_TIMEOUT_SECONDS):
                async with _provider_limiter:
                    raw_response = await resolved.resolver(query)
        except TimeoutError as error:
            logger.warning(
                "Form option provider timed out for workflow %s source %s",
                binding.workflow.__name__,
                binding.source,
                extra={
                    "event_type": "workflow_form_option_provider_timeout",
                    "workflow": binding.workflow.__name__,
                    "source": binding.source,
                },
            )
            raise HTTPException(status_code=504, detail="Option provider timed out") from error
        except Exception as error:
            logger.error(
                "Form option provider failed for workflow %s source %s (%s)",
                binding.workflow.__name__,
                binding.source,
                type(error).__name__,
                extra={
                    "event_type": "workflow_form_option_provider_failed",
                    "workflow": binding.workflow.__name__,
                    "source": binding.source,
                    "error_type": type(error).__name__,
                },
            )
            raise HTTPException(status_code=502, detail="Option provider failed") from error

        try:
            response = OptionSourceResponse.model_validate(raw_response)
        except ValidationError as error:
            logger.error(
                "Form option provider returned invalid data for workflow %s source %s",
                binding.workflow.__name__,
                binding.source,
                extra={
                    "event_type": "workflow_form_option_provider_invalid_response",
                    "workflow": binding.workflow.__name__,
                    "source": binding.source,
                },
            )
            raise HTTPException(
                status_code=502,
                detail="Option provider returned invalid data",
            ) from error

        if len(response.items) > _PROVIDER_MAX_ITEMS:
            logger.error(
                "Form option provider exceeded the item limit for workflow %s source %s",
                binding.workflow.__name__,
                binding.source,
                extra={
                    "event_type": "workflow_form_option_provider_item_limit",
                    "workflow": binding.workflow.__name__,
                    "source": binding.source,
                },
            )
            raise HTTPException(
                status_code=502,
                detail="Option provider returned too many items",
            )
        return response

    form_option_endpoint.__name__ = _provider_operation_id(binding)
    form_option_endpoint.__doc__ = (
        f"Return {binding.source} form options for {binding.workflow.__name__}."
    )
    annotated = cast(Any, Annotated)
    form_option_endpoint.__annotations__ = {
        "query": annotated[resolved.query_model, Query()],
        "request": Request,
        "return": OptionSourceResponse,
    }
    return form_option_endpoint


def _relative_provider_path(binding: FormOptionProviderBinding) -> str:
    if not binding.endpoint.startswith(f"{_PUBLIC_WORKFLOW_PREFIX}/"):
        raise WorkflowFormContractError(
            f"form option provider endpoint {binding.endpoint!r} is outside "
            f"{_PUBLIC_WORKFLOW_PREFIX!r}"
        )
    return binding.endpoint.removeprefix(_PUBLIC_WORKFLOW_PREFIX)


def _provider_operation_id(binding: FormOptionProviderBinding) -> str:
    """Return the stable OpenAPI operation ID for a provider route."""
    form_id = binding.workflow_form_id.replace("-", "_")
    source = binding.source.replace("-", "_")
    return f"get_workflow_form_options_{form_id}__{source}"


__all__ = [
    "ApiErrorResponse",
    "WorkflowFormSurface",
    "build_workflow_form_surface",
    "create_form_option_endpoint",
]
