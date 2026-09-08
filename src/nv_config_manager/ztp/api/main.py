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
"""FastAPI Main App."""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from nv_config_manager.common.auth import install_identity_probe
from nv_config_manager.common.log import configure_logging
from nv_config_manager.common.telemetry import (
    group_fastapi_status_codes,
    instrument_fastapi_app,
    setup_tracing,
)
from nv_config_manager.ztp.api import device_v1, files_v1, firmware_v1
from nv_config_manager.ztp.api.metrics import device_http_requests

configure_logging(service="ztp")
setup_tracing("ztp")


def main() -> None:
    """CLI entrypoint for ZTP API."""
    parser = argparse.ArgumentParser(description="ZTP API Server")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=9000, help="Port to listen on (default: 9000)")
    args = parser.parse_args()

    uvicorn.run(
        "nv_config_manager.ztp.api.main:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
        log_config=None,
        timeout_keep_alive=75,
        limit_concurrency=1000,
        backlog=2048,
        loop="asyncio",
    )


app = FastAPI()
instrument_fastapi_app(app)

# Include routers
app.include_router(device_v1.router, prefix="/v1")
app.include_router(firmware_v1.router, prefix="/v1")
app.include_router(files_v1.router, prefix="/v1")

instrumentator = Instrumentator(
    should_group_status_codes=group_fastapi_status_codes(),
    excluded_handlers=["/healthcheck", "/metrics"],
)
instrumentator.add(
    metrics.default(
        metric_namespace="nv-config-manager",
        metric_subsystem="ztp",
    )
)
instrumentator.add(
    device_http_requests(
        metric_namespace="nv-config-manager",
        metric_subsystem="ztp",
    )
)
instrumentator.instrument(app)
instrumentator.expose(app, include_in_schema=False)


@app.get("/healthcheck")
async def healthcheck() -> str:
    """Execute healthcheck."""
    # Async so the probe completes in a single event-loop callback. A sync
    # handler is dispatched to the anyio threadpool and needs a second trip
    # through the ready queue to deliver its result, which measures at ~2x the
    # latency of an async one under load -- enough to blow the probe timeout and
    # trigger a spurious liveness kill. Note this only halves the probe's own
    # latency; it does not address the queueing that makes the loop slow.
    return "OK"


install_identity_probe(app, deferred_auth_prefixes=("/v1/device/",))
