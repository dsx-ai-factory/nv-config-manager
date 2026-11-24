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
"""Temporal OpenTelemetry bootstrap (SDK Runtime + built-in metrics).

Call setup_telemetry() once at process startup. When an OTLP endpoint is
configured (OTEL_EXPORTER_OTLP_ENDPOINT, injected by the Helm chart only when
observability.enabled), it:
  1. Configures the global Python OTel TracerProvider via common.telemetry to
     export spans via OTLP/gRPC to the configured collector (in-cluster Grafana
     Alloy by default, or a managed/external collector when otlpEndpoint is set).
  2. Returns a Temporal Runtime whose SDK core pushes built-in Temporal
     metrics (workflow_completed, activity_execution_latency, etc.) via
     OTLP to the same endpoint.

When no endpoint is configured, observability is disabled: no exporters are
created and a plain Runtime is returned, so deployments without a collector
do not attempt to export to a nonexistent endpoint.

Alternatively, when TEMPORAL_METRICS_PROMETHEUS_ADDRESS is set (injected by the
Helm chart on the NVIDIA Config Manager worker when its PodMonitor is
rendered), the SDK core serves the same built-in metrics on a Prometheus
/metrics endpoint instead of pushing them over OTLP. A Prometheus scrape labels
each sample with the pod's Kubernetes namespace, which OTLP-pushed series do
not carry. A Runtime has a single metrics exporter, so this replaces OTLP
metric export for the process; tracing is unaffected. PrometheusConfig keeps its
default of no unit suffix (``temporal_request_latency_bucket``); a collector that
translates OTLP units, such as the bundled Alloy, stores
``temporal_request_latency_milliseconds_bucket`` instead. See
docs/overview/observability.mdx.

After setup_telemetry() returns, call get_runtime() to retrieve the
Runtime for passing to Client.connect(runtime=...).
"""

import os

from temporalio.runtime import OpenTelemetryConfig, PrometheusConfig, Runtime, TelemetryConfig

from nv_config_manager.common.telemetry import setup_tracing

_runtime: Runtime | None = None


def setup_telemetry(service_name: str) -> Runtime:
    """Initialize OTel tracing and build a Temporal Runtime with SDK metrics.

    Reads:
      TEMPORAL_METRICS_PROMETHEUS_ADDRESS — host:port to serve SDK metrics on a
                                     Prometheus endpoint; takes precedence over
                                     OTLP for metrics when set
      OTEL_EXPORTER_OTLP_ENDPOINT  — OTLP gRPC endpoint; observability is
                                     disabled (no exporters) when unset/empty
      OTEL_SERVICE_NAME            — overrides service_name argument when set
      ENVIRONMENT                  — used as deployment.environment resource attribute
    """
    global _runtime
    prometheus_address = os.getenv("TEMPORAL_METRICS_PROMETHEUS_ADDRESS", "").strip()
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    setup_tracing(service_name)
    if prometheus_address:
        _runtime = Runtime(
            telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=prometheus_address))
        )
        return _runtime

    if not otlp_endpoint:
        _runtime = Runtime(telemetry=TelemetryConfig())
        return _runtime

    _runtime = Runtime(telemetry=TelemetryConfig(metrics=OpenTelemetryConfig(url=otlp_endpoint)))
    return _runtime


def get_runtime() -> Runtime:
    """Return the process-level Temporal Runtime initialized by setup_telemetry().

    Falls back to a no-telemetry Runtime when setup_telemetry() was not called
    (e.g. in unit tests that don't boot the full stack).
    """
    if _runtime is not None:
        return _runtime
    return Runtime(telemetry=TelemetryConfig())
