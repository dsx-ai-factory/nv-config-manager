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
"""NVIDIA Config Manager Workflow API."""

import os
from typing import Literal

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as instrumentator_metrics
from pydantic import BaseModel

from nv_config_manager.common.auth import install_identity_probe
from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, configure_logging, get_logger
from nv_config_manager.common.telemetry import (
    group_fastapi_status_codes,
    instrument_fastapi_app,
)
from nv_config_manager.temporal.api import codec_server, parameter_v1, workflow_v1
from nv_config_manager.temporal.api.audit import install_workflow_audit_logging
from nv_config_manager.temporal.common.rbac_config import RBACConfig
from nv_config_manager.temporal.telemetry import setup_telemetry

configure_logging(service="temporal-api")
setup_telemetry("nv-config-manager-temporal-api")
logger = get_logger(__name__, category=LogCategory.TEMPORAL_API)

rbac_config = RBACConfig()
logger.info(
    "RBAC configuration loaded: %s workflows found",
    len(rbac_config.get_all_workflows()),
)

app = FastAPI()
instrument_fastapi_app(app)

# Configure CORS for cross-origin requests from the UI
# CORS origins are configured in nv-config-manager.ini [temporal.api] section
config = load_config()
if config.has_section("temporal.api"):
    cors_origins_str = config.get("temporal.api", "cors_origins", fallback="")
    cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,  # type: ignore[arg-type]
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )

app.include_router(parameter_v1.router, prefix="/v1")
app.include_router(workflow_v1.router, prefix="/v1")
app.include_router(codec_server.router, prefix="/v1")

instrumentator = Instrumentator(
    should_group_status_codes=group_fastapi_status_codes(),
    excluded_handlers=["/healthcheck", "/metrics"],
)
instrumentator.add(
    instrumentator_metrics.default(
        metric_namespace="nv-config-manager",
        metric_subsystem="temporal_api",
    )
)
instrumentator.instrument(app)
instrumentator.expose(app, include_in_schema=False)


@app.get("/healthcheck")
def healthcheck() -> Literal["OK"]:
    """Execute healthcheck."""
    return "OK"


# Starlette executes the last-added HTTP middleware first. Install identity
# second so it populates request.state before audit logging reads those fields.
install_workflow_audit_logging(app)
install_identity_probe(app)


class WorkflowRoles(BaseModel):
    """Roles for a workflow."""

    read_roles: list[str]
    execute_roles: list[str]


class RBACConfigResponse(BaseModel):
    """RBAC configuration status response."""

    status: Literal["ok", "error"]
    file_exists: bool
    workflows_count: int | None = None
    workflows: dict[str, WorkflowRoles] | None = None
    error: str | None = None


@app.get(
    "/rbac-config-status",
    response_model=RBACConfigResponse,
    summary="RBAC Config Status",
)
def rbac_config_status() -> RBACConfigResponse:
    """Check the status of the RBAC configuration and return the mapping."""
    config_path = "/etc/rbac/rbac.yaml"
    file_exists = os.path.exists(config_path)

    if file_exists:
        rbac_config = RBACConfig()
        config = rbac_config.get_all_workflows()

        # Convert to JSON serializable format
        workflows = {}
        for workflow_name, roles in config.items():
            workflows[workflow_name] = WorkflowRoles(
                read_roles=list(roles["read_roles"]),
                execute_roles=list(roles["execute_roles"]),
            )

        return RBACConfigResponse(
            status="ok",
            file_exists=file_exists,
            workflows_count=len(workflows),
            workflows=workflows,
        )
    else:
        return RBACConfigResponse(
            status="error",
            file_exists=file_exists,
            error="RBAC configuration file not found",
        )


def main() -> None:
    """CLI entrypoint for Temporal API."""

    uvicorn.run(
        "nv_config_manager.temporal.api.main:app",
        host="0.0.0.0",
        port=9000,
        proxy_headers=True,
        log_config=None,
        loop="asyncio",
    )
