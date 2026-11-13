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
"""Render Service On Demand API."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as instrumentator_metrics

from nv_config_manager.common.auth import install_identity_probe
from nv_config_manager.common.config_watch import restart_on_config_change
from nv_config_manager.common.log import configure_logging
from nv_config_manager.common.telemetry import (
    group_fastapi_status_codes,
    instrument_fastapi_app,
    setup_tracing,
)
from nv_config_manager.render.api import admin_v1, render_v1

configure_logging(service="render")
setup_tracing("render")

app = FastAPI()
instrument_fastapi_app(app)


def main() -> None:
    """CLI entrypoint for render API."""
    restart_on_config_change()
    uvicorn.run(
        "nv_config_manager.render.api.main:app",
        host="0.0.0.0",
        port=9000,
        proxy_headers=True,
        log_config=None,
        loop="asyncio",
    )


app.include_router(render_v1.router, prefix="/v1", tags=["render"])
app.include_router(admin_v1.router, prefix="/v1", tags=["admin"])


instrumentator = Instrumentator(
    should_group_status_codes=group_fastapi_status_codes(),
    excluded_handlers=["/healthcheck", "/metrics"],
)
instrumentator.add(
    instrumentator_metrics.default(
        metric_namespace="nv-config-manager",
        metric_subsystem="render",
    )
)
instrumentator.instrument(app)
instrumentator.expose(app, include_in_schema=False)


@app.get("/healthcheck")
def healthcheck() -> str:
    """Execute healthcheck."""
    return "OK"


install_identity_probe(app)
