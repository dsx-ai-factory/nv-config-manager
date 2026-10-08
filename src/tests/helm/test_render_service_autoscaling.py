# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rendering contracts for NATS metric isolation in shared backends."""

import shutil
import subprocess
from pathlib import Path

import pytest

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"


def _queries(cluster: str | None = None) -> list[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored")
    command = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--show-only",
        "templates/render-service-scaledobject.yaml",
        "--set",
        "renderService.autoscaling.enabled=true",
        "--set-string",
        "renderService.autoscaling.prometheus.serverAddress=https://metrics.example.com",
        "--set-string",
        "renderService.autoscaling.prometheus.natsMetricsNamespace=cerebro-prod-001",
    ]
    if cluster is not None:
        command += [
            "--set-string",
            f"renderService.autoscaling.prometheus.natsMetricsCluster={cluster}",
        ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if "max(nats_consumer_num_pending{" in line
    ]


def test_shared_namespace_queries_identify_nats_cluster() -> None:
    queries = _queries("nv-pdx04-gni-wl-prd-001")
    assert len(queries) == 2
    for query in queries:
        assert 'namespace="cerebro-prod-001"' in query
        assert 'cluster="nv-pdx04-gni-wl-prd-001"' in query


@pytest.mark.parametrize("cluster", [None, ""])
def test_cluster_selector_is_optional(cluster: str | None) -> None:
    queries = _queries(cluster)
    assert len(queries) == 2
    assert all('namespace="cerebro-prod-001"' in query for query in queries)
    assert all("cluster=" not in query for query in queries)


def test_cluster_label_is_escaped_for_promql() -> None:
    queries = _queries('cluster"quoted')
    assert len(queries) == 2
    assert all('cluster="cluster\\"quoted"' in query for query in queries)
