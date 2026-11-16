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
"""Template Preview's metrics are scraped and shown on a dashboard.

A PodMonitor makes Prometheus (through Alloy) scrape the backend's /metrics, which exists only
when the dynamic plugin control plane is on, and a ConfigMap carries the Grafana dashboard for the
sidecar. Both exist only when the monitoring and Grafana switches ask for them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
TEMPLATE_PREVIEW_ON = (
    "templatePreview.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.acknowledgeArbitraryCodeExecution=true",
)
MONITORING = ("monitoring.enabled=true", "monitoring.podMonitors.enabled=true")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> list[dict]:
    command = ["helm", "template", "test", ".", "--values", "values-ci.yaml"]
    for value in sets:
        command += ["--set", value]
    output = subprocess.run(
        command, cwd=HELM_DIR, capture_output=True, text=True, check=True
    ).stdout
    return [doc for doc in yaml.safe_load_all(output) if doc]


def _named(docs: list[dict], kind: str, name_part: str) -> dict | None:
    return next((d for d in docs if d["kind"] == kind and name_part in d["metadata"]["name"]), None)


def test_the_backend_is_scraped_when_monitoring_and_the_control_plane_are_on():
    monitor = _named(
        _render(*MONITORING, *TEMPLATE_PREVIEW_ON), "PodMonitor", "template-preview-backend"
    )

    assert monitor is not None
    spec = monitor["spec"]
    assert spec["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "templatePreview",
        "app.kubernetes.io/component": "backend",
    }
    assert [(e["port"], e["path"]) for e in spec["podMetricsEndpoints"]] == [("http", "/metrics")]


@pytest.mark.parametrize(
    "sets",
    [
        MONITORING,  # Template Preview off
        (*MONITORING, "templatePreview.enabled=true"),  # control plane closed: no /metrics
        (*TEMPLATE_PREVIEW_ON, "monitoring.podMonitors.enabled=false"),  # monitoring off
    ],
)
def test_nothing_is_scraped_otherwise(sets):
    assert _named(_render(*sets), "PodMonitor", "template-preview-backend") is None


def test_the_dashboard_is_provided_to_grafana_only_when_grafana_is_on():
    assert _named(_render(), "ConfigMap", "grafana-dashboard-template-preview") is None

    config_map = _named(
        _render("grafana.enabled=true"), "ConfigMap", "grafana-dashboard-template-preview"
    )

    assert config_map is not None and config_map["metadata"]["labels"]["grafana_dashboard"] == "1"
    dashboard = json.loads(config_map["data"]["template-preview.json"])
    assert dashboard["uid"] == "template-preview"
    assert "${DS_PROMETHEUS}" not in config_map["data"]["template-preview.json"]
