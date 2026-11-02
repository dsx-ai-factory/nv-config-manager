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
"""Helm rendering tests for custom monitoring labels."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"


def _render_monitoring(custom_values: Path) -> list[dict[str, Any]]:
    """Render monitoring resources with custom values."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(_CHART_DIR),
            "--values",
            str(_CHART_DIR / "values-ci.yaml"),
            "--values",
            str(custom_values),
            "--show-only",
            "templates/monitoring.yaml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def test_ambiguous_custom_label_keys_remain_strings(tmp_path: Path) -> None:
    """YAML-like label keys must remain strings in monitoring CR fields."""
    custom_values = tmp_path / "custom-values.yaml"
    custom_values.write_text('global:\n  customLabels:\n    "true": "123"\n')

    pod_monitors = [
        doc for doc in _render_monitoring(custom_values) if doc.get("kind") == "PodMonitor"
    ]

    assert pod_monitors
    for pod_monitor in pod_monitors:
        assert "true" in pod_monitor["spec"]["podTargetLabels"]
        for endpoint in pod_monitor["spec"]["podMetricsEndpoints"]:
            relabelings = endpoint.get("metricRelabelings", [])
            assert any(rule.get("targetLabel") == "true" for rule in relabelings)
