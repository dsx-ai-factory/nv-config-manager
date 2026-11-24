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
"""Helm rendering tests for the vault-agent INI checksum annotation.

The Vault Agent Injector reads the agent ConfigMap at pod admission. Pods that
use it must carry a checksum of the rendered INI template so a change to that
template rolls them. Tests skip cleanly when ``helm`` is unavailable or chart
dependencies have not been vendored (``helm dependency build``).
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
_RELEASE = "test"
_CHECKSUM_KEY = "checksum/vault-agent-ini"
_INJECT_KEY = "vault.hashicorp.com/agent-inject"
_POD_OWNER_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job"}


def _render(secrets_method: str, mock_devices: bool = False) -> list[dict[str, Any]]:
    """Render the chart with values-ci.yaml, or skip if unrenderable."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    cmd = [
        "helm",
        "template",
        _RELEASE,
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--set",
        f"secrets.method={secrets_method}",
        "--set",
        f"localDev.mockDevices={str(mock_devices).lower()}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    # Unrelated Nautobot manifests repeat a label key, which this test does not read.
    yaml.allow_duplicate_keys = True
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def _pod_annotations(docs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Return pod template annotations keyed by ``Kind/name`` for every pod owner."""
    annotations: dict[str, dict[str, str]] = {}
    for doc in docs:
        if doc.get("kind") not in _POD_OWNER_KINDS:
            continue
        template = doc.get("spec", {}).get("template", {})
        annotations[f"{doc['kind']}/{doc['metadata']['name']}"] = (
            template.get("metadata", {}).get("annotations") or {}
        )
    return annotations


def _injected_checksums(docs: list[dict[str, Any]]) -> dict[str, str | None]:
    """Return the INI checksum of every pod template that uses the vault-agent injector."""
    return {
        name: annotations.get(_CHECKSUM_KEY)
        for name, annotations in _pod_annotations(docs).items()
        if annotations.get(_INJECT_KEY) == "true"
    }


def test_every_injected_pod_template_carries_the_ini_checksum() -> None:
    checksums = _injected_checksums(_render("vault-agent"))

    assert checksums
    assert all(checksums.values()), f"pods without checksum: {checksums}"


def test_injected_pods_share_one_ini_checksum() -> None:
    checksums = _injected_checksums(_render("vault-agent"))

    assert len(set(checksums.values())) == 1


def test_ini_checksum_changes_when_rendered_ini_changes() -> None:
    plain = set(_injected_checksums(_render("vault-agent", mock_devices=False)).values())
    mocked = set(_injected_checksums(_render("vault-agent", mock_devices=True)).values())

    assert plain.isdisjoint(mocked)


def test_ini_checksum_is_stable_across_renders() -> None:
    first = _injected_checksums(_render("vault-agent"))
    second = _injected_checksums(_render("vault-agent"))

    assert first == second


@pytest.mark.parametrize("secrets_method", ["eso", "kubernetes"])
def test_ini_checksum_is_absent_without_the_injector(secrets_method: str) -> None:
    annotations = _pod_annotations(_render(secrets_method))

    assert not any(_CHECKSUM_KEY in pod for pod in annotations.values())
