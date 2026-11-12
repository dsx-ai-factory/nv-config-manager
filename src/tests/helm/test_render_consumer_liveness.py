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
"""Helm rendering tests for the render consumer /healthz budgets.

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
_TEMPLATE = "templates/render-service.yaml"
_CONSUMERS = ("consumer-nautobot", "consumer-device")
_BUDGET_ENV = (
    "CONSUMER_LIVENESS_TIMEOUT_SECONDS",
    "CONSUMER_DISCONNECTED_TIMEOUT_SECONDS",
    "CONSUMER_HANDLER_MAX_AGE_SECONDS",
)


def _render(*set_args: str) -> list[dict[str, Any]]:
    """Render the render-service template with values-ci.yaml, or skip if unrenderable."""
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
    ]
    for pair in set_args:
        cmd += ["--set", pair]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def _consumer_env(docs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Return each render consumer container's env as a name -> value map."""
    envs: dict[str, dict[str, str]] = {}
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        name = doc["metadata"]["name"]
        component = next((c for c in _CONSUMERS if name.endswith(c)), None)
        if component is None:
            continue
        container = next(
            c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == "consumer"
        )
        envs[component] = {e["name"]: e.get("value") for e in container.get("env", [])}
    assert set(envs) == set(_CONSUMERS), f"missing consumer deployments: {sorted(envs)}"
    return envs


def test_budgets_default_to_the_consumer_built_ins() -> None:
    """Without overrides the chart sets nothing, so the consumer's validated defaults apply."""
    for component, env in _consumer_env(_render()).items():
        for name in _BUDGET_ENV:
            assert name not in env, f"{component} unexpectedly sets {name}"


def test_budget_overrides_reach_both_consumers() -> None:
    """Operators can lengthen the budgets without editing the rendered manifest."""
    docs = _render(
        "renderService.consumers.liveness.timeoutSeconds=900",
        "renderService.consumers.liveness.disconnectedTimeoutSeconds=60",
        "renderService.consumers.liveness.handlerMaxAgeSeconds=14400",
    )
    for env in _consumer_env(docs).values():
        assert env["CONSUMER_LIVENESS_TIMEOUT_SECONDS"] == "900"
        assert env["CONSUMER_DISCONNECTED_TIMEOUT_SECONDS"] == "60"
        assert env["CONSUMER_HANDLER_MAX_AGE_SECONDS"] == "14400"


def test_explicit_zero_is_passed_through_for_the_consumer_to_reject() -> None:
    """Helm treats 0 as empty; dropping it would silently swap an invalid override for the default."""
    docs = _render("renderService.consumers.liveness.disconnectedTimeoutSeconds=0")
    for env in _consumer_env(docs).values():
        assert env["CONSUMER_DISCONNECTED_TIMEOUT_SECONDS"] == "0"
