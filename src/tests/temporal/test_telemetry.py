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
"""Tests for the Temporal telemetry layer."""

import socket
import time
import urllib.request
from datetime import timedelta
from unittest import mock

import pytest

from nv_config_manager.temporal import telemetry

OTLP_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
PROMETHEUS_ENV = "TEMPORAL_METRICS_PROMETHEUS_ADDRESS"


@pytest.fixture(autouse=True)
def _reset_runtime():
    """Reset the module-level cached runtime around every test."""
    telemetry._runtime = None
    yield
    telemetry._runtime = None


@pytest.fixture
def _clean_env(monkeypatch):
    """Remove the OTLP endpoint and Prometheus address so each test controls them explicitly."""
    monkeypatch.delenv(OTLP_ENV, raising=False)
    monkeypatch.delenv(PROMETHEUS_ENV, raising=False)
    return monkeypatch


@pytest.fixture
def otel_mocks():
    """Patch the Temporal Runtime constructors and the shared tracer setup."""
    with (
        mock.patch.object(telemetry, "setup_tracing") as setup_tracing,
        mock.patch.object(telemetry, "Runtime") as runtime_cls,
        mock.patch.object(telemetry, "TelemetryConfig") as telemetry_config_cls,
        mock.patch.object(telemetry, "OpenTelemetryConfig") as otel_config_cls,
        mock.patch.object(telemetry, "PrometheusConfig") as prometheus_config_cls,
    ):
        yield {
            "setup_tracing": setup_tracing,
            "runtime_cls": runtime_cls,
            "telemetry_config_cls": telemetry_config_cls,
            "otel_config_cls": otel_config_cls,
            "prometheus_config_cls": prometheus_config_cls,
        }


class TestSetupTelemetryDisabled:
    """setup_telemetry() must return a plain Runtime when no endpoint is set."""

    def test_no_endpoint_returns_plain_runtime(self, _clean_env, otel_mocks):
        """Missing endpoint skips the metrics config and returns a plain Runtime."""
        result = telemetry.setup_telemetry("svc")

        otel_mocks["setup_tracing"].assert_called_once_with("svc")
        otel_mocks["otel_config_cls"].assert_not_called()
        otel_mocks["telemetry_config_cls"].assert_called_once_with()
        assert result is otel_mocks["runtime_cls"].return_value
        assert telemetry._runtime is result

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_endpoint_is_disabled(self, _clean_env, otel_mocks, value):
        """Empty or whitespace endpoint is treated as disabled."""
        _clean_env.setenv(OTLP_ENV, value)

        telemetry.setup_telemetry("svc")

        otel_mocks["otel_config_cls"].assert_not_called()


class TestSetupTelemetryEnabled:
    """setup_telemetry() wires OTel metrics when an endpoint is configured."""

    def test_endpoint_configures_metrics_runtime(self, _clean_env, otel_mocks):
        """A configured endpoint sets up tracing and a metrics-exporting Runtime."""
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        result = telemetry.setup_telemetry("svc")

        otel_mocks["setup_tracing"].assert_called_once_with("svc")
        otel_mocks["otel_config_cls"].assert_called_once_with(url="http://collector:4317")
        otel_mocks["telemetry_config_cls"].assert_called_once_with(
            metrics=otel_mocks["otel_config_cls"].return_value
        )
        assert result is otel_mocks["runtime_cls"].return_value
        assert telemetry._runtime is result

    def test_endpoint_is_stripped(self, _clean_env, otel_mocks):
        """Surrounding whitespace on the endpoint is trimmed before use."""
        _clean_env.setenv(OTLP_ENV, "  http://collector:4317  ")

        telemetry.setup_telemetry("svc")

        otel_mocks["otel_config_cls"].assert_called_once_with(url="http://collector:4317")


class TestSetupTelemetryPrometheus:
    """setup_telemetry() serves SDK metrics on a Prometheus endpoint when asked."""

    def test_address_configures_prometheus_runtime(self, _clean_env, otel_mocks):
        """A configured address exposes SDK metrics for a PodMonitor to scrape."""
        _clean_env.setenv(PROMETHEUS_ENV, "0.0.0.0:8000")

        result = telemetry.setup_telemetry("svc")

        otel_mocks["setup_tracing"].assert_called_once_with("svc")
        otel_mocks["prometheus_config_cls"].assert_called_once_with(bind_address="0.0.0.0:8000")
        otel_mocks["otel_config_cls"].assert_not_called()
        otel_mocks["telemetry_config_cls"].assert_called_once_with(
            metrics=otel_mocks["prometheus_config_cls"].return_value
        )
        assert result is otel_mocks["runtime_cls"].return_value
        assert telemetry._runtime is result

    def test_prometheus_wins_over_otlp_for_metrics(self, _clean_env, otel_mocks):
        """A Runtime has one metrics exporter, so Prometheus takes precedence over OTLP."""
        _clean_env.setenv(PROMETHEUS_ENV, "0.0.0.0:8000")
        _clean_env.setenv(OTLP_ENV, "http://collector:4317")

        telemetry.setup_telemetry("svc")

        otel_mocks["prometheus_config_cls"].assert_called_once_with(bind_address="0.0.0.0:8000")
        otel_mocks["otel_config_cls"].assert_not_called()
        otel_mocks["setup_tracing"].assert_called_once_with("svc")

    def test_address_is_stripped(self, _clean_env, otel_mocks):
        """Surrounding whitespace on the address is trimmed before use."""
        _clean_env.setenv(PROMETHEUS_ENV, "  0.0.0.0:8000  ")

        telemetry.setup_telemetry("svc")

        otel_mocks["prometheus_config_cls"].assert_called_once_with(bind_address="0.0.0.0:8000")

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_address_is_disabled(self, _clean_env, otel_mocks, value):
        """Empty or whitespace address leaves the Prometheus endpoint off."""
        _clean_env.setenv(PROMETHEUS_ENV, value)

        telemetry.setup_telemetry("svc")

        otel_mocks["prometheus_config_cls"].assert_not_called()
        otel_mocks["telemetry_config_cls"].assert_called_once_with()


class TestPrometheusExporterOutput:
    """Pin the metric names the real SDK exporter serves; no mocks are involved."""

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _scrape(port: int, prefix: str) -> list[str]:
        """Return the sample names starting with ``prefix`` once the exporter has any."""
        deadline = time.monotonic() + 10
        while True:
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics").read().decode()  # noqa: S310
            names = sorted({line.split("{")[0].split(" ")[0] for line in body.splitlines()})
            matching = [name for name in names if name.startswith(prefix)]
            if matching or time.monotonic() > deadline:
                return matching
            time.sleep(0.1)

    def test_unit_is_not_appended_to_metric_names(self, _clean_env):
        """A millisecond histogram is served as ``<name>_bucket``, not ``<name>_milliseconds_bucket``.

        These are the names the OTLP path produces behind a collector that does not
        translate units (the kiwi pipeline), so existing dashboards keep matching. A
        collector that does add unit suffixes, such as the bundled Alloy, stores the
        same series as ``temporal_request_latency_milliseconds_*``; the observability
        docs list that rename. Enabling PrometheusConfig(unit_suffix=True) would flip
        this, so this test fails if that default changes.
        """
        port = self._free_port()
        _clean_env.setenv(PROMETHEUS_ENV, f"127.0.0.1:{port}")

        runtime = telemetry.setup_telemetry("svc")
        histogram = runtime.metric_meter.create_histogram_timedelta(
            "temporal_request_latency", "request latency", unit="ms"
        )
        histogram.record(timedelta(milliseconds=12))

        assert self._scrape(port, "temporal_request_latency") == [
            "temporal_request_latency_bucket",
            "temporal_request_latency_count",
            "temporal_request_latency_sum",
        ]


class TestGetRuntime:
    """get_runtime() returns the cached runtime or a safe fallback."""

    def test_returns_cached_runtime_after_setup(self, _clean_env, otel_mocks):
        """get_runtime() returns the runtime built by setup_telemetry()."""
        created = telemetry.setup_telemetry("svc")
        assert telemetry.get_runtime() is created

    def test_fallback_runtime_when_not_initialized(self, otel_mocks):
        """get_runtime() builds a no-telemetry Runtime when setup was skipped."""
        telemetry._runtime = None

        result = telemetry.get_runtime()

        otel_mocks["telemetry_config_cls"].assert_called_once_with()
        assert result is otel_mocks["runtime_cls"].return_value
