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
"""Helm rendering tests for the DCIM change-event consumer and its NATS settings.

The bundled Nautobot provider keeps its historical workload names and INI keys;
other providers get the provider-neutral ``dcim`` names. These tests skip
cleanly when ``helm`` is unavailable or chart dependencies are not vendored.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_RENDER = "test-nv-config-manager-render"
_NON_NAUTOBOT = (
    "dcim.provider=synthetic",
    "dcim.server=https://dcim.example.com",
    "secrets.vault.paths.dcim.path=ci/dcim",
    "secrets.vault.paths.nats.path=ci/nats",
)
_INI_KEY = (
    r"^\s*((?:nautobot|dcim)_(?:stream|subjects?|api_prefix|consumer_name)"
    r"|dcim_change_(?:stream|subjects?)) = (.*)$"
)
_REFERENCE_INI_KEYS = [
    "nautobot_stream",
    "nautobot_api_prefix",
    "nautobot_consumer_name",
    "nautobot_subjects",
    "nautobot_subject",
    "dcim_change_stream",
    "dcim_change_subject",
]


def _render(*set_args: str, values: tuple[str, ...] = ()) -> str:
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
    for name in values:
        cmd += ["--values", str(_CHART_DIR / name)]
    for pair in set_args:
        cmd += ["--set", pair]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    return result.stdout


def _docs(text: str) -> list[dict[str, Any]]:
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = True
    return [doc for doc in yaml.load_all(io.StringIO(text)) if doc]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    for doc in docs:
        if doc.get("kind") == kind and doc["metadata"]["name"] == name:
            return doc
    return None


def _ini_nats_keys(text: str) -> dict[str, str]:
    """Return the DCIM stream keys of the first rendered nv-config-manager.ini."""
    keys: dict[str, str] = {}
    for match in re.finditer(_INI_KEY, text, flags=re.M):
        if match.group(1) in keys:
            break
        keys[match.group(1)] = match.group(2).strip()
    return keys


def test_nautobot_keeps_the_historical_consumer_and_wait_names() -> None:
    text = _render()
    docs = _docs(text)

    consumer = _find(docs, "Deployment", f"{_RENDER}-consumer-nautobot")
    assert consumer is not None
    assert consumer["metadata"]["labels"]["component"] == "consumer-nautobot"
    assert _find(docs, "Deployment", f"{_RENDER}-consumer-dcim") is None
    assert "wait-for-dcim" not in text
    assert (
        'NAUTOBOT_HEALTH_URL="${NAUTOBOT_SCHEME}://${NAUTOBOT_HOST}:${NAUTOBOT_PORT}/health"'
        in text
    )


def test_other_providers_get_provider_neutral_names() -> None:
    text = _render(*_NON_NAUTOBOT)
    docs = _docs(text)

    assert _find(docs, "Deployment", f"{_RENDER}-consumer-dcim") is not None
    assert _find(docs, "Deployment", f"{_RENDER}-consumer-nautobot") is None
    assert "- name: wait-for-dcim" in text


def test_observability_profile_keeps_dcim_consumer_autoscaling() -> None:
    docs = _docs(_render(values=("values-observability.yaml",)))

    scaled = _find(docs, "ScaledObject", f"{_RENDER}-consumer-nautobot")
    assert scaled is not None
    assert scaled["spec"]["minReplicaCount"] == 1
    assert scaled["spec"]["maxReplicaCount"] == 2
    consumer = _find(docs, "Deployment", f"{_RENDER}-consumer-nautobot")
    assert consumer is not None
    assert "replicas" not in consumer["spec"]


def test_partial_legacy_consumer_overrides_merge_over_the_dcim_defaults() -> None:
    docs = _docs(
        _render(
            "renderService.autoscaling.enabled=true",
            "renderService.autoscaling.prometheus.serverAddress=http://prom:9090",
            "renderService.autoscaling.prometheus.natsMetricsNamespace=nats",
            "renderService.autoscaling.consumers.nautobot.maxReplicas=7",
            "renderService.consumers.nautobot.nodeSelector.pool=render",
        )
    )

    scaled = _find(docs, "ScaledObject", f"{_RENDER}-consumer-nautobot")
    assert scaled is not None
    assert scaled["spec"]["maxReplicaCount"] == 7
    consumer = _find(docs, "Deployment", f"{_RENDER}-consumer-nautobot")
    assert consumer is not None
    pod = consumer["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"pool": "render"}
    assert pod["containers"][0]["resources"]["requests"]


def test_nautobot_ini_keeps_the_historical_nats_keys() -> None:
    keys = _ini_nats_keys(_render())

    assert list(keys) == _REFERENCE_INI_KEYS
    assert keys["nautobot_consumer_name"] == "nv-config-manager-nautobot"


def test_dcim_events_override_a_legacy_stream_block() -> None:
    keys = _ini_nats_keys(
        _render(
            "externalServices.nats.streams.nautobot.name=legacy-stream",
            "dcim.events.stream=events-stream",
        )
    )

    assert keys["nautobot_stream"] == "legacy-stream"
    assert keys["dcim_change_stream"] == "events-stream"


def test_other_providers_get_the_dcim_ini_keys() -> None:
    keys = _ini_nats_keys(_render(*_NON_NAUTOBOT))

    assert keys["dcim_consumer_name"] == "nv-config-manager-nautobot"
    assert "dcim_change_subjects" in keys
    assert "dcim_api_prefix" in keys
