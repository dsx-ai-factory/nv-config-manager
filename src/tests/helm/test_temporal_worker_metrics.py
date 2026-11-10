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
"""Helm rendering contracts for the Temporal worker's Prometheus SDK metrics."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_WORKER_DEPLOYMENT = "test-nv-config-manager-temporal-nv-config-manager-worker"
_WORKER_POD_MONITOR = _WORKER_DEPLOYMENT
_SERVER_WORKER_POD_MONITOR = "test-nv-config-manager-temporal-worker"
_METRICS_ENV = "TEMPORAL_METRICS_PROMETHEUS_ADDRESS"


def _render(*set_args: str) -> list[dict[str, Any]]:
    """Render the chart with the CI values plus any ``--set`` overrides."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    command = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--show-only",
        "templates/temporal.yaml",
        "--show-only",
        "templates/monitoring.yaml",
    ]
    for set_arg in set_args:
        command += ["--set", set_arg]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    """Return the rendered resource with the given kind and name, if any."""
    for doc in docs:
        if doc.get("kind") == kind and doc["metadata"]["name"] == name:
            return doc
    return None


def _worker_container(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the ``worker`` container of the Config Manager worker Deployment."""
    deployment = _find(docs, "Deployment", _WORKER_DEPLOYMENT)
    assert deployment is not None
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return next(container for container in containers if container["name"] == "worker")


def _selects(selector: dict[str, str], labels: dict[str, str]) -> bool:
    """Return whether a ``matchLabels`` selector matches the given pod labels."""
    return all(labels.get(key) == value for key, value in selector.items())


def test_worker_serves_metrics_and_is_scraped_by_default() -> None:
    """With pod monitors on, the worker exposes SDK metrics and a PodMonitor targets it."""
    docs = _render()

    container = _worker_container(docs)
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env[_METRICS_ENV] == "0.0.0.0:8000"
    assert {"name": "metrics", "containerPort": 8000} in container["ports"]

    pod_monitor = _find(docs, "PodMonitor", _WORKER_POD_MONITOR)
    assert pod_monitor is not None
    assert [e["port"] for e in pod_monitor["spec"]["podMetricsEndpoints"]] == ["metrics"]
    assert "honorLabels" not in pod_monitor["spec"]["podMetricsEndpoints"][0]


def test_pod_monitor_selects_only_the_config_manager_worker() -> None:
    """The new PodMonitor matches the worker pod and not the Temporal server worker."""
    docs = _render()
    deployment = _find(docs, "Deployment", _WORKER_DEPLOYMENT)
    assert deployment is not None
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]

    pod_monitor = _find(docs, "PodMonitor", _WORKER_POD_MONITOR)
    assert pod_monitor is not None
    assert _selects(pod_monitor["spec"]["selector"]["matchLabels"], pod_labels)

    server_monitor = _find(docs, "PodMonitor", _SERVER_WORKER_POD_MONITOR)
    assert server_monitor is not None
    assert not _selects(server_monitor["spec"]["selector"]["matchLabels"], pod_labels)


@pytest.mark.parametrize(
    "override",
    ["monitoring.podMonitors.enabled=false", "monitoring.enabled=false"],
)
def test_worker_metrics_follow_pod_monitor_settings(override: str) -> None:
    """Without a PodMonitor there is nothing to scrape, so no port or env var is added."""
    docs = _render(override)

    container = _worker_container(docs)
    assert _METRICS_ENV not in {item["name"] for item in container["env"]}
    assert "ports" not in container
    assert _find(docs, "PodMonitor", _WORKER_POD_MONITOR) is None
