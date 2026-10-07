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
"""Dynamic API endpoint generation from workflow metadata."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, computed_field
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.auth import get_sso_user
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.api.links import temporal_ui_workflow_href
from nv_config_manager.temporal.api.workflow_catalog import WORKFLOW_API_CATALOG
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.rbac_config import RBACConfig

logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)


class WorkflowResponse(BaseModel):
    """Workflow Response Model."""

    id: str

    @computed_field
    def href(self) -> str:
        """Calculate URL to Temporal UI Workflow View."""
        return temporal_ui_workflow_href(self.id)


# Import the start_workflow function - this will be available when this module is imported
# from within the API context
start_workflow = None


def set_start_workflow_function(func: Callable[..., Awaitable[str]]) -> None:
    """Set the start_workflow function to avoid circular imports."""
    global start_workflow
    start_workflow = func


def create_workflow_endpoint(
    workflow_class: type, input_class: type[BaseModel], endpoint_path: str
) -> Callable[..., Awaitable[WorkflowResponse]]:
    """Create a FastAPI endpoint function for a workflow."""

    async def workflow_endpoint(
        body: input_class,  # type: ignore
        request: Request,
    ) -> WorkflowResponse:
        """Execute the workflow with provided parameters."""
        if start_workflow is None:
            raise RuntimeError(
                "start_workflow function not set. Call set_start_workflow_function() first."
            )

        # Auto-populate user fields from request auth data if they exist and are None
        user = getattr(request.state, "user", None) or get_sso_user(request)

        # Auto-populate common user fields if they exist in the input model and are None
        if hasattr(body, "user") and not body.user:  # type: ignore[attr-defined]
            body.user = user  # type: ignore[attr-defined]

        if hasattr(body, "user_domain") and not body.user_domain:  # type: ignore[attr-defined]
            # Extract domain from user email or default to nvidia.com
            # TODO: add a default user domain to INI file for external customers
            body.user_domain = user.split("@")[1] if "@" in user else "nvidia.com"  # type: ignore[attr-defined]

        try:
            body = await cast(type[WorkflowMetadataMixin], workflow_class).canonicalize_input(body)
        except ApplicationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        workflow_id = await start_workflow(request, workflow_class, body)
        return WorkflowResponse(id=workflow_id)

    # Set function metadata for FastAPI
    workflow_endpoint.__name__ = f"{workflow_class.__name__.lower()}_endpoint"
    workflow_endpoint.__doc__ = f"Execute the {workflow_class.__name__} workflow."

    # Set the proper type annotations for OpenAPI generation
    workflow_endpoint.__annotations__ = {
        "body": input_class,
        "request": Request,
        "return": WorkflowResponse,
    }

    return workflow_endpoint


def register_dynamic_endpoints(
    router: APIRouter,
    workflows: Sequence[type] | None = None,
) -> None:
    """Register all workflow endpoints dynamically based on metadata."""
    registered_count = 0
    all_workflows = WORKFLOW_API_CATALOG if workflows is None else workflows

    for workflow_class in all_workflows:
        try:
            # Check if workflow uses metadata mixin
            if not issubclass(workflow_class, WorkflowMetadataMixin):
                logger.warning(
                    f"Workflow {workflow_class.__name__} does not use WorkflowMetadataMixin, skipping dynamic registration"
                )
                continue

            metadata_workflow = workflow_class

            # Check if workflow has complete metadata
            if not metadata_workflow.has_complete_metadata():
                logger.warning(
                    f"Workflow {workflow_class.__name__} has incomplete metadata, skipping dynamic registration"
                )
                continue

            # Get metadata
            endpoint_path = metadata_workflow.get_workflow_api_endpoint()
            input_class = metadata_workflow.get_workflow_input_class()
            description = metadata_workflow.get_workflow_description()

            if not endpoint_path or not input_class:
                logger.warning(
                    f"Workflow {workflow_class.__name__} missing endpoint path or input class"
                )
                continue

            # Create the endpoint function
            endpoint_func = create_workflow_endpoint(workflow_class, input_class, endpoint_path)

            # Register the endpoint with the router
            # The parent router supplies the workflow tag; repeating it here creates duplicate
            # OpenAPI tags and duplicate suffixed methods in generated clients.
            router.post(
                endpoint_path,
                response_model=WorkflowResponse,
                summary=f"Execute {workflow_class.__name__}",
                description=description,
            )(endpoint_func)

            registered_count += 1
            logger.info(
                f"Registered dynamic endpoint: {endpoint_path} -> {workflow_class.__name__}"
            )

        except Exception as e:
            logger.error(f"Failed to register endpoint for {workflow_class.__name__}: {e}")
            continue

    logger.info(f"Successfully registered {registered_count} dynamic workflow endpoints")


def get_registered_workflows_info(
    *,
    include_rbac: bool = False,
    workflows: Sequence[type] | None = None,
) -> dict[str, dict[str, Any]]:
    """Get information about all registered workflows with metadata."""
    workflows_info: dict[str, dict[str, Any]] = {}
    rbac_config = RBACConfig() if include_rbac else None
    all_workflows = WORKFLOW_API_CATALOG if workflows is None else workflows

    for workflow_class in all_workflows:
        if issubclass(workflow_class, WorkflowMetadataMixin):
            metadata_workflow = workflow_class

            if metadata_workflow.has_complete_metadata():
                input_class = metadata_workflow.get_workflow_input_class()
                workflow_roles = (
                    rbac_config.get_workflow_roles(workflow_class.__name__)
                    if rbac_config is not None
                    else None
                )
                workflows_info[workflow_class.__name__] = {
                    "name": workflow_class.__name__,
                    "display_name": metadata_workflow.get_workflow_name(),
                    "endpoint": metadata_workflow.get_workflow_api_endpoint(),
                    "input_class": input_class.__name__ if input_class else "Unknown",
                    "description": metadata_workflow.get_workflow_description(),
                    "namespace": metadata_workflow.get_workflow_namespace(),
                    "cli_name": metadata_workflow.get_workflow_cli_name(),
                    "group": metadata_workflow.get_workflow_group(),
                }
                if include_rbac:
                    workflows_info[workflow_class.__name__]["read_roles"] = sorted(
                        workflow_roles["read_roles"] if workflow_roles else []
                    )
                    workflows_info[workflow_class.__name__]["execute_roles"] = sorted(
                        workflow_roles["execute_roles"] if workflow_roles else []
                    )

    return workflows_info
