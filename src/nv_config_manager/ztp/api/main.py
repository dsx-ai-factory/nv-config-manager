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
import os
import signal
import threading
import time
from pathlib import Path

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


def _tls_material_state(certfile: str, keyfile: str) -> tuple[int, int, int, int]:
    """Return a cheap change detector for the projected TLS Secret files."""
    cert = Path(certfile).stat()
    key = Path(keyfile).stat()
    return cert.st_mtime_ns, cert.st_size, key.st_mtime_ns, key.st_size


def _watch_tls_material(certfile: str, keyfile: str, interval: float) -> None:
    """Restart the container after cert-manager rotates the mounted Secret."""
    initial = _tls_material_state(certfile, keyfile)
    while True:
        time.sleep(interval)
        try:
            current = _tls_material_state(certfile, keyfile)
        except OSError:
            continue
        if current != initial:
            os.kill(os.getpid(), signal.SIGTERM)
            return


def main() -> None:
    """CLI entrypoint for ZTP API."""
    parser = argparse.ArgumentParser(description="ZTP API Server")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=9000, help="Port to listen on (default: 9000)")
    parser.add_argument("--ssl-certfile", default="", help="PEM TLS certificate chain")
    parser.add_argument("--ssl-keyfile", default="", help="PEM TLS private key")
    parser.add_argument(
        "--tls-reload-interval",
        type=float,
        default=30.0,
        help="Seconds between mounted TLS Secret rotation checks",
    )
    args = parser.parse_args()
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile and --ssl-keyfile must be provided together")
    if args.tls_reload_interval <= 0:
        parser.error("--tls-reload-interval must be positive")

    if args.ssl_certfile:
        watcher = threading.Thread(
            target=_watch_tls_material,
            args=(args.ssl_certfile, args.ssl_keyfile, args.tls_reload_interval),
            name="ztp-tls-certificate-watcher",
            daemon=True,
        )
        watcher.start()

    uvicorn.run(
        "nv_config_manager.ztp.api.main:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ssl_certfile=args.ssl_certfile or None,
        ssl_keyfile=args.ssl_keyfile or None,
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
