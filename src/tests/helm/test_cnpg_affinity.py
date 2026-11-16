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
"""Helm rendering tests for CloudNativePG Cluster pod anti-affinity.

CloudNativePG enables preferred pod anti-affinity unless
``enablePodAntiAffinity`` is explicitly ``false``, so the chart must render an
explicit ``false`` rather than dropping it. These tests skip cleanly when
``helm`` is unavailable or chart dependencies have not been vendored.
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

_CLUSTERS = {
    "temporal": "cluster-temporal",
    "temporalVisibility": "cluster-temporal-visibility",
    "configStore": "cluster-config-store",
    "dhcp": "cluster-dhcp",
    "nautobot": "cluster-nautobot",
}


def _cluster_affinity(cluster_key: str, *set_args: str) -> dict[str, Any]:
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
        "templates/cnpg-clusters.yaml",
        "--set",
        "cnpg.dhcp.enabled=true",
        "--set",
        "cnpg.nautobot.enabled=true",
    ]
    for pair in set_args:
        cmd += ["--set", pair]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")

    name = _CLUSTERS[cluster_key]
    for doc in YAML(typ="safe", pure=True).load_all(io.StringIO(result.stdout)):
        if doc and doc.get("kind") == "Cluster" and doc["metadata"]["name"] == name:
            return doc["spec"].get("affinity") or {}
    pytest.fail(f"Cluster {name} not rendered")


@pytest.mark.parametrize("cluster_key", _CLUSTERS)
def test_default_renders_preferred_anti_affinity(cluster_key: str) -> None:
    affinity = _cluster_affinity(cluster_key)
    assert affinity["enablePodAntiAffinity"] is True
    assert affinity["podAntiAffinityType"] == "preferred"
    assert affinity["topologyKey"] == "kubernetes.io/hostname"


@pytest.mark.parametrize("cluster_key", _CLUSTERS)
def test_false_disables_anti_affinity(cluster_key: str) -> None:
    affinity = _cluster_affinity(
        cluster_key, f"cnpg.{cluster_key}.affinity.enablePodAntiAffinity=false"
    )
    assert affinity["enablePodAntiAffinity"] is False


@pytest.mark.parametrize("cluster_key", _CLUSTERS)
def test_null_omits_fields_for_operator_defaults(cluster_key: str) -> None:
    affinity = _cluster_affinity(
        cluster_key,
        f"cnpg.{cluster_key}.affinity.enablePodAntiAffinity=null",
        f"cnpg.{cluster_key}.affinity.podAntiAffinityType=null",
    )
    assert "enablePodAntiAffinity" not in affinity
    assert "podAntiAffinityType" not in affinity


@pytest.mark.parametrize("cluster_key", _CLUSTERS)
def test_required_anti_affinity_type(cluster_key: str) -> None:
    affinity = _cluster_affinity(
        cluster_key, f"cnpg.{cluster_key}.affinity.podAntiAffinityType=required"
    )
    assert affinity["podAntiAffinityType"] == "required"
