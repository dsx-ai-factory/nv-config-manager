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
"""Helm rendering tests for the Temporal archive consumer /healthz probe and budgets.

Tests skip cleanly when ``helm`` is unavailable or chart dependencies have not
been vendored (``helm dependency build``).
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_TEMPLATE = "templates/temporal.yaml"
_BUDGET_ENV = "CONSUMER_LIVENESS_TIMEOUT_SECONDS"


def _archive_container(*set_args: str) -> dict[str, Any]:
    """Render the archive Deployment with values-ci.yaml and return its container."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    cmd = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--show-only",
        _TEMPLATE,
        "--set",
        "temporal.archive.enabled=true",
    ]
    for pair in set_args:
        cmd += ["--set", pair]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    for doc in yaml.load_all(io.StringIO(result.stdout)):
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            if container["name"] == "archive":
                return container
    pytest.fail("archive Deployment not rendered")


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def test_archive_probes_healthz_on_the_health_port() -> None:
    container = _archive_container()

    assert _env(container)["CONSUMER_LIVENESS_PORT"] == "8001"
    assert {"name": "health", "containerPort": 8001} in container["ports"]
    probe = container["livenessProbe"]
    assert probe["httpGet"] == {"path": "/healthz", "port": "health"}
    assert (probe["initialDelaySeconds"], probe["periodSeconds"], probe["failureThreshold"]) == (
        30,
        15,
        4,
    )


def test_budget_defaults_to_the_consumer_built_in() -> None:
    """Without an override the chart sets nothing, so the consumer's validated default applies."""
    assert _BUDGET_ENV not in _env(_archive_container())


def test_budget_override_reaches_the_archive() -> None:
    env = _env(_archive_container("temporal.archive.liveness.timeoutSeconds=900"))
    assert env[_BUDGET_ENV] == "900"


def test_explicit_zero_is_passed_through_for_the_consumer_to_reject() -> None:
    """Helm treats 0 as empty; dropping it would silently swap an invalid override for the default."""
    env = _env(_archive_container("temporal.archive.liveness.timeoutSeconds=0"))
    assert env[_BUDGET_ENV] == "0"
