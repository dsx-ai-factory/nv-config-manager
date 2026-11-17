# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Helm rendering contracts for the archive consumer's progress metrics."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_ARCHIVE = "test-nv-config-manager-temporal-archive"


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


def _named(docs: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next(
        (d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == _ARCHIVE), None
    )


def test_archive_metrics_are_scraped_without_a_probe() -> None:
    """The archive pod exposes its metrics port to a PodMonitor and has no liveness probe."""
    docs = _render(
        "temporal.archive.enabled=true",
        "monitoring.enabled=true",
        "monitoring.podMonitors.enabled=true",
    )

    deployment = _named(docs, "Deployment")
    assert deployment is not None
    template = deployment["spec"]["template"]
    container = template["spec"]["containers"][0]
    assert {"name": "metrics", "containerPort": 8000} in container["ports"]
    assert "livenessProbe" not in container

    monitor = _named(docs, "PodMonitor")
    assert monitor is not None
    selector = monitor["spec"]["selector"]["matchLabels"]
    assert selector.items() <= template["metadata"]["labels"].items()
    assert monitor["spec"]["podMetricsEndpoints"][0]["port"] == "metrics"


def test_archive_metrics_port_value_moves_the_listener_and_the_scraped_port() -> None:
    """The container port and the port the process binds come from one value."""
    docs = _render("temporal.archive.enabled=true", "temporal.archive.metricsPort=8002")

    deployment = _named(docs, "Deployment")
    assert deployment is not None
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert {"name": "metrics", "containerPort": 8002} in container["ports"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["ARCHIVE_METRICS_PORT"] == "8002"


def test_archive_pod_monitor_follows_the_archive_deployment() -> None:
    """No PodMonitor is rendered for an archive consumer that is not deployed."""
    docs = _render("monitoring.enabled=true", "monitoring.podMonitors.enabled=true")

    assert _named(docs, "Deployment") is None
    assert _named(docs, "PodMonitor") is None
