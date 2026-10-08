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
"""Tests for the shared, backend-agnostic OpenTelemetry tracing bootstrap."""

import concurrent.futures
import re
from unittest import mock

import pytest
from opentelemetry.instrumentation.utils import is_instrumentation_enabled

from nv_config_manager.common import telemetry

OTLP_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_ENV = "OTEL_SERVICE_NAME"
ENVIRONMENT_ENV = "ENVIRONMENT"
GROUP_FASTAPI_STATUS_CODES_ENV = "NV_CONFIG_MANAGER_GROUP_FASTAPI_STATUS_CODES"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, True),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("  true  ", True),
        ("false", False),
        ("FALSE", False),
    ],
)
def test_fastapi_status_code_grouping_setting(monkeypatch, configured, expected):
    """FastAPI metrics grouping follows its environment setting and defaults on."""
    if configured is None:
        monkeypatch.delenv(GROUP_FASTAPI_STATUS_CODES_ENV, raising=False)
    else:
        monkeypatch.setenv(GROUP_FASTAPI_STATUS_CODES_ENV, configured)

    assert telemetry.group_fastapi_status_codes() is expected


@pytest.mark.parametrize(
    "url",
    [
        "http://service/healthcheck",
        "http://service/healthcheck/",
        "http://service/livez",
        "http://service/livez/",
        "http://service/metrics",
        "http://service/metrics/",
    ],
)
def test_operational_endpoint_urls_are_excluded(url):
    """The shared exclusion regex matches only operational endpoint URLs."""
    assert re.search(telemetry.HTTP_TRACE_EXCLUDED_URLS, url)


@pytest.mark.parametrize(
    "url",
    [
        "http://service/v1/healthcheck/status",
        "http://service/v1/metrics/query",
        "http://service/v1/config",
    ],
)
def test_application_endpoint_urls_are_not_excluded(url):
    """Application routes remain eligible for tracing."""
    assert not re.search(telemetry.HTTP_TRACE_EXCLUDED_URLS, url)


def test_fastapi_instrumentation_excludes_healthchecks_and_metrics():
    """FastAPI instrumentation filters high-frequency operational endpoints."""
    app = mock.Mock()

    with mock.patch.object(telemetry.FastAPIInstrumentor, "instrument_app") as instrument_app:
        telemetry.instrument_fastapi_app(app)

    instrument_app.assert_called_once_with(
        app,
        excluded_urls=telemetry.HTTP_TRACE_EXCLUDED_URLS,
    )
    app.add_middleware.assert_called_once_with(telemetry._SuppressOperationalTracingMiddleware)


def test_asgi_instrumentation_excludes_healthchecks_and_metrics():
    """Generic ASGI instrumentation applies the same operational endpoint filter."""
    app = mock.sentinel.asgi_app

    with (
        mock.patch.object(telemetry, "OpenTelemetryMiddleware") as middleware,
        mock.patch.object(
            telemetry, "_SuppressOperationalTracingMiddleware"
        ) as suppression_middleware,
    ):
        result = telemetry.instrument_asgi_app(app)

    middleware.assert_called_once_with(
        app,
        excluded_urls=telemetry.HTTP_TRACE_EXCLUDED_URLS,
    )
    suppression_middleware.assert_called_once_with(middleware.return_value)
    assert result is suppression_middleware.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(telemetry.HTTP_TRACE_EXCLUDED_PATHS))
async def test_operational_request_suppresses_descendant_instrumentation(path):
    """Excluded requests cannot create auto-instrumented child spans."""
    instrumentation_states = []

    async def app(scope, receive, send):
        instrumentation_states.append(is_instrumentation_enabled())

    middleware = telemetry._SuppressOperationalTracingMiddleware(app)
    await middleware(
        {"type": "http", "path": path},
        mock.AsyncMock(),
        mock.AsyncMock(),
    )

    assert instrumentation_states == [False]


@pytest.mark.asyncio
async def test_application_request_keeps_descendant_instrumentation_enabled():
    """Normal application requests retain auto-instrumentation."""
    instrumentation_states = []

    async def app(scope, receive, send):
        instrumentation_states.append(is_instrumentation_enabled())

    middleware = telemetry._SuppressOperationalTracingMiddleware(app)
    await middleware(
        {"type": "http", "path": "/v1/config"},
        mock.AsyncMock(),
        mock.AsyncMock(),
    )

    assert instrumentation_states == [True]


@pytest.fixture(autouse=True)
def _reset_configured():
    """Reset the module-level idempotency flag around every test."""
    telemetry._tracing_configured = False
    yield
    telemetry._tracing_configured = False


@pytest.fixture
def _clean_env(monkeypatch):
    """Remove telemetry env vars so each test controls them explicitly."""
    for name in (OTLP_ENV, SERVICE_ENV, ENVIRONMENT_ENV):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def otel_mocks():
    """Patch the OTel constructors used by setup_tracing()."""
    with (
        mock.patch.object(telemetry, "TracerProvider") as provider_cls,
        mock.patch.object(telemetry, "BatchSpanProcessor") as span_processor_cls,
        mock.patch.object(telemetry, "OTLPSpanExporter") as exporter_cls,
        mock.patch.object(telemetry, "Resource") as resource_cls,
        # Patch the real function on opentelemetry.trace (not the whole module) so a
        # wrong attribute name fails at patch time instead of silently auto-mocking.
        mock.patch.object(telemetry.trace, "set_tracer_provider") as set_tracer_provider,
        # Keep unit tests from patching the real aiohttp/httpx/requests libraries.
        mock.patch.object(telemetry, "_instrument_http_clients") as instrument_http_clients,
    ):
        yield {
            "provider_cls": provider_cls,
            "span_processor_cls": span_processor_cls,
            "exporter_cls": exporter_cls,
            "resource_cls": resource_cls,
            "set_tracer_provider": set_tracer_provider,
            "instrument_http_clients": instrument_http_clients,
        }


class TestSetupTracingDisabled:
    """setup_tracing() must no-op when no endpoint is configured."""

    def test_no_endpoint_returns_false(self, _clean_env, otel_mocks):
        """Missing endpoint skips exporter wiring and reports disabled."""
        assert telemetry.setup_tracing("svc") is False

        otel_mocks["exporter_cls"].assert_not_called()
        otel_mocks["provider_cls"].assert_not_called()
        otel_mocks["set_tracer_provider"].assert_not_called()
        otel_mocks["instrument_http_clients"].assert_not_called()

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_endpoint_is_disabled(self, _clean_env, otel_mocks, value):
        """Empty or whitespace endpoint is treated as disabled."""
        _clean_env.setenv(OTLP_ENV, value)

        assert telemetry.setup_tracing("svc") is False
        otel_mocks["provider_cls"].assert_not_called()


class TestSetupTracingEnabled:
    """setup_tracing() wires an OTLP span exporter when an endpoint is set."""

    def test_endpoint_configures_provider(self, _clean_env, otel_mocks):
        """A configured endpoint builds the span exporter and registers a provider."""
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        assert telemetry.setup_tracing("svc") is True

        otel_mocks["exporter_cls"].assert_called_once_with(endpoint="http://collector:4317")
        otel_mocks["provider_cls"].assert_called_once_with(
            resource=otel_mocks["resource_cls"].create.return_value
        )
        otel_mocks["provider_cls"].return_value.add_span_processor.assert_called_once_with(
            otel_mocks["span_processor_cls"].return_value
        )
        otel_mocks["set_tracer_provider"].assert_called_once_with(
            otel_mocks["provider_cls"].return_value
        )
        otel_mocks["instrument_http_clients"].assert_called_once_with()

    def test_endpoint_is_stripped(self, _clean_env, otel_mocks):
        """Surrounding whitespace on the endpoint is trimmed before use."""
        _clean_env.setenv(OTLP_ENV, "  http://collector:4317  ")

        telemetry.setup_tracing("svc")

        otel_mocks["exporter_cls"].assert_called_once_with(endpoint="http://collector:4317")

    def test_service_name_argument_used_as_default(self, _clean_env, otel_mocks):
        """The service_name argument seeds the service.name resource attribute."""
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        telemetry.setup_tracing("worker-svc")

        attrs = otel_mocks["resource_cls"].create.call_args.args[0]
        assert attrs["service.name"] == "worker-svc"
        assert attrs["deployment.environment"] == "unknown"

    def test_env_overrides_service_name_and_environment(self, _clean_env, otel_mocks):
        """OTEL_SERVICE_NAME and ENVIRONMENT override defaults."""
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")
        _clean_env.setenv(SERVICE_ENV, "override-svc")
        _clean_env.setenv(ENVIRONMENT_ENV, "staging")

        telemetry.setup_tracing("worker-svc")

        attrs = otel_mocks["resource_cls"].create.call_args.args[0]
        assert attrs["service.name"] == "override-svc"
        assert attrs["deployment.environment"] == "staging"

    def test_idempotent_second_call_is_noop(self, _clean_env, otel_mocks):
        """A second call after configuration does not re-register a provider."""
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        assert telemetry.setup_tracing("svc") is True
        assert telemetry.setup_tracing("svc") is True

        otel_mocks["set_tracer_provider"].assert_called_once()
        otel_mocks["provider_cls"].assert_called_once()
        otel_mocks["instrument_http_clients"].assert_called_once()

    def test_concurrent_calls_configure_once(self, _clean_env, otel_mocks):
        """Concurrent startup calls register the provider only once."""

        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: telemetry.setup_tracing("svc"), range(16)))

        assert all(results)
        otel_mocks["set_tracer_provider"].assert_called_once()
        otel_mocks["provider_cls"].assert_called_once()
        otel_mocks["instrument_http_clients"].assert_called_once()


class TestHttpClientInstrumentation:
    """Outbound client instrumentation makes trace context cross service hops."""

    def test_collects_installed_instrumentors(self):
        """aiohttp, httpx, and requests instrumentors are all available."""
        names = [name for name, _ in telemetry._http_client_instrumentors()]

        assert {"aiohttp", "httpx", "requests"} <= set(names)

    def test_instrument_invokes_every_collected_instrumentor(self):
        """_instrument_http_clients calls each collected instrument() once."""
        aiohttp_instr = mock.Mock()
        httpx_instr = mock.Mock()
        with mock.patch.object(
            telemetry,
            "_http_client_instrumentors",
            return_value=[("aiohttp", aiohttp_instr), ("httpx", httpx_instr)],
        ):
            telemetry._instrument_http_clients()

        aiohttp_instr.assert_called_once_with()
        httpx_instr.assert_called_once_with()

    def test_instrument_failure_is_swallowed(self):
        """A failing instrumentor must not propagate and block startup."""
        boom = mock.Mock(side_effect=RuntimeError("patch failed"))
        healthy = mock.Mock()
        with mock.patch.object(
            telemetry,
            "_http_client_instrumentors",
            return_value=[("aiohttp", boom), ("httpx", healthy)],
        ):
            telemetry._instrument_http_clients()

        boom.assert_called_once_with()
        # A failure in one instrumentor does not stop the others from running.
        healthy.assert_called_once_with()
