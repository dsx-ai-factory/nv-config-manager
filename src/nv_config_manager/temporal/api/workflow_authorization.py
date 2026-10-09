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
"""Shared authorization for workflow execution and workflow-owned API resources."""

from dataclasses import dataclass

from fastapi import HTTPException, Request

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.common.rbac_config import RBACConfig

logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)


@dataclass(frozen=True, slots=True)
class WorkflowExecuteAccess:
    """Authenticated identity and configured roles for one workflow."""

    user: str
    read_roles: frozenset[str]
    execute_roles: frozenset[str]


def require_workflow_execute_access(
    request: Request,
    workflow_name: str,
    *,
    rbac_config: RBACConfig | None = None,
) -> WorkflowExecuteAccess:
    """Require the request identity to have an execute role for ``workflow_name``."""
    user: str = request.state.user
    roles: set[str] = request.state.roles
    config = rbac_config if rbac_config is not None else RBACConfig()
    configured_roles = config.get_workflow_roles(workflow_name)

    if configured_roles is None:
        logger.error("No RBAC configuration found for workflow %s", workflow_name)
        raise HTTPException(
            status_code=403,
            detail="No RBAC configuration found for this workflow",
        )

    execute_roles = frozenset(configured_roles["execute_roles"])
    if not (execute_roles.intersection(roles) or "all" in execute_roles):
        logger.error(
            "User %s with roles %s is not authorized to execute workflow %s",
            user,
            roles,
            workflow_name,
        )
        raise HTTPException(status_code=403, detail="Not authorized to execute this workflow")

    return WorkflowExecuteAccess(
        user=user,
        read_roles=frozenset(configured_roles["read_roles"]),
        execute_roles=execute_roles,
    )


__all__ = ["WorkflowExecuteAccess", "require_workflow_execute_access"]
