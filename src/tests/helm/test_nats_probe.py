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
"""Helm rendering tests for the bundled NATS blackbox Probe.

An exporter outside the cluster cannot resolve the in-cluster NATS service, so
the Probe goes through a /healthz-only gateway route when one is rendered. These
tests skip cleanly when ``helm`` is unavailable or chart dependencies have not
been vendored.
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
_NATS_NAME = "test-nv-config-manager-nats"
_BASE_SET = (
    "nautobotNats.enabled=true",
    "networkPolicy.enabled=true",
    "gateway.enabled=true",
    "gateway.baseHostname=cell.example.test",
    "monitoring.enabled=true",
    "monitoring.prometheus.namespace=prom",
    "monitoring.probes.enabled=true",
    "monitoring.probes.blackboxExporterUrl=exporter.example:443",
)


def _render(*set_args: str) -> list[dict[str, Any]]:
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
    ]
    for pair in (*_BASE_SET, *set_args):
        cmd += ["--set", pair]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe")
    # The Nautobot Celery Deployment renders a duplicate label key.
    yaml.allow_duplicate_keys = True
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if doc]


def _nats_probe_target(docs: list[dict[str, Any]]) -> str | None:
    for doc in docs:
        if doc["kind"] == "Probe" and doc["metadata"]["name"] == _NATS_NAME:
            return doc["spec"]["targets"]["staticConfig"]["static"][0]
    return None


def _health_route(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for doc in docs:
        if doc["kind"] == "HTTPRoute" and doc["metadata"]["name"] == f"{_NATS_NAME}-health":
            return doc
    return None


def _monitor_port_open_to_prometheus(docs: list[dict[str, Any]]) -> bool:
    for doc in docs:
        if doc["kind"] == "NetworkPolicy" and doc["metadata"]["name"] == "namespace-isolation":
            for rule in doc["spec"]["ingress"]:
                if any(p.get("port") == 8222 for p in rule.get("ports", [])):
                    return True
    return False


def test_probe_uses_gateway_health_route_by_default() -> None:
    docs = _render()
    assert _nats_probe_target(docs) == "https://nats-health.cell.example.test/healthz"
    assert not _monitor_port_open_to_prometheus(docs)


def test_health_route_exposes_only_exact_get_healthz() -> None:
    route = _health_route(_render())
    assert route is not None
    assert route["spec"]["hostnames"] == ["nats-health.cell.example.test"]
    [rule] = route["spec"]["rules"]
    assert rule["matches"] == [{"path": {"type": "Exact", "value": "/healthz"}, "method": "GET"}]
    assert rule["backendRefs"] == [{"name": _NATS_NAME, "port": 8222}]


def test_probe_falls_back_to_in_cluster_without_route() -> None:
    docs = _render("nautobotNats.gateway.healthRoute.enabled=false")
    assert _health_route(docs) is None
    assert _nats_probe_target(docs) == f"http://{_NATS_NAME}.nv-config-manager-ci.svc:8222/healthz"
    assert _monitor_port_open_to_prometheus(docs)


def test_explicit_target_replaces_route() -> None:
    docs = _render("monitoring.probes.nats.target=http://nats.example/healthz")
    assert _nats_probe_target(docs) == "http://nats.example/healthz"
    assert _health_route(docs) is None
    assert not _monitor_port_open_to_prometheus(docs)


@pytest.mark.parametrize(
    "set_arg",
    [
        "monitoring.probes.nats.enabled=false",
        "monitoring.probes.enabled=false",
        "monitoring.probes.blackboxExporterUrl=",
        "monitoring.enabled=false",
    ],
)
def test_no_route_without_nats_probe(set_arg: str) -> None:
    docs = _render(set_arg)
    assert _nats_probe_target(docs) is None
    assert _health_route(docs) is None


def test_no_route_or_probe_without_bundled_nats() -> None:
    docs = _render("nautobotNats.enabled=false", "externalServices.nats.local=false")
    assert _health_route(docs) is None
    assert _nats_probe_target(docs) is None
