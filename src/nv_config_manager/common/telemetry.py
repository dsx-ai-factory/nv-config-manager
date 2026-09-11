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
"""Backend-agnostic OpenTelemetry tracing bootstrap shared by every service."""

import importlib
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

HTTP_TRACE_EXCLUDED_URLS = r"/(?:healthcheck|livez|metrics)/?$"
HTTP_TRACE_EXCLUDED_PATHS = frozenset(
    {
        "/healthcheck",
        "/healthcheck/",
        "/livez",
        "/livez/",
        "/metrics",
        "/metrics/",
    }
)
GROUP_FASTAPI_STATUS_CODES_ENV = "NV_CONFIG_MANAGER_GROUP_FASTAPI_STATUS_CODES"


def group_fastapi_status_codes() -> bool:
    """Return whether Prometheus FastAPI metrics should group status codes."""
    configured = os.getenv(GROUP_FASTAPI_STATUS_CODES_ENV, "true")
    return configured.strip().lower() in {"1", "true", "yes", "on"}


class _SuppressOperationalTracingMiddleware:
    """Suppress all auto-instrumentation within probe and scrape requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in HTTP_TRACE_EXCLUDED_PATHS:
            with suppress_instrumentation():
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


def instrument_fastapi_app(app: FastAPI) -> None:
    """Instrument a FastAPI app without tracing probe and scrape endpoints."""
    FastAPIInstrumentor.instrument_app(app, excluded_urls=HTTP_TRACE_EXCLUDED_URLS)
    app.add_middleware(_SuppressOperationalTracingMiddleware)


def instrument_asgi_app(app: ASGIApp) -> ASGIApp:
    """Instrument an ASGI app without tracing probe and scrape endpoints."""
    instrumented_app = OpenTelemetryMiddleware(app, excluded_urls=HTTP_TRACE_EXCLUDED_URLS)
    return _SuppressOperationalTracingMiddleware(instrumented_app)


def _optional_instrumentor(module_path: str, class_name: str) -> type[Any] | None:
    """Return an instrumentor class, or None when its package is not installed."""
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    return getattr(module, class_name, None)


# Optional outbound-client instrumentors resolved once at import. A trimmed
# dependency set degrades gracefully: a missing package leaves its symbol None.
AioHttpClientInstrumentor = _optional_instrumentor(
    "opentelemetry.instrumentation.aiohttp_client", "AioHttpClientInstrumentor"
)
HTTPXClientInstrumentor = _optional_instrumentor(
    "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"
)
RequestsInstrumentor = _optional_instrumentor(
    "opentelemetry.instrumentation.requests", "RequestsInstrumentor"
)

_tracing_configured = False
_tracing_lock = threading.Lock()


def setup_tracing(service_name: str) -> bool:
    """Register the global OTel TracerProvider for OTLP span export.

    Reads:
      OTEL_EXPORTER_OTLP_ENDPOINT  — OTLP gRPC endpoint; tracing is disabled
                                     (no exporters) when unset/empty
      OTEL_SERVICE_NAME            — overrides service_name argument when set
      ENVIRONMENT                  — deployment.environment resource attribute

    Returns True when tracing was configured, False when disabled. Idempotent:
    repeated calls after the first configuration are no-ops.
    """
    global _tracing_configured
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not otlp_endpoint:
        return False

    with _tracing_lock:
        if _tracing_configured:
            return True

        resolved_name = os.getenv("OTEL_SERVICE_NAME", service_name)
        resource = Resource.create(
            {
                "service.name": resolved_name,
                "deployment.environment": os.getenv("ENVIRONMENT", "unknown"),
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
        trace.set_tracer_provider(provider)
        _instrument_http_clients()
        _tracing_configured = True
        return True


def _instrument_http_clients() -> None:
    """Patch outbound HTTP client libs so trace context crosses service hops.

    A failure to patch any one client never blocks process startup.
    """
    for name, instrument in _http_client_instrumentors():
        try:
            instrument()
        except Exception:  # noqa: BLE001 - telemetry must never crash the service
            logger.warning("Could not instrument %s client for tracing", name, exc_info=True)


def _http_client_instrumentors() -> list[tuple[str, Callable[[], None]]]:
    """Collect installed outbound-client instrumentors (aiohttp, httpx, requests)."""
    candidates = (
        ("aiohttp", AioHttpClientInstrumentor),
        ("httpx", HTTPXClientInstrumentor),
        ("requests", RequestsInstrumentor),
    )
    return [(name, cls().instrument) for name, cls in candidates if cls is not None]
