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
"""No shipped configuration may switch on dynamic template plugins.

Dynamic template plugins run uploaded Python inside the render service. The two
``unsafeDynamicTemplatePlugins`` flags default to false everywhere and are enabled only by an
operator on purpose. This test fails the build if any values file in the repository enables them,
other than the explicit local-development overlay listed in ``LOCAL_DEVELOPMENT_OVERLAYS``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HELM_DIR = REPO_ROOT / "deploy" / "helm"

FLAG_BLOCK = "unsafeDynamicTemplatePlugins"
FLAGS = ("enabled", "acknowledgeArbitraryCodeExecution")

# Values overlays that exist only to turn the feature on for a local kind cluster. They are never
# part of a release; adding to this list is a deliberate, reviewable act.
LOCAL_DEVELOPMENT_OVERLAYS = {"values-template-preview.yaml"}

SCANNED_ROOTS = [HELM_DIR, REPO_ROOT / "installer", REPO_ROOT / "docs"]


def _values_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        if root.is_dir():
            for pattern in ("values*.yaml", "values*.yml"):
                files.extend(p for p in root.rglob(pattern) if "charts" not in p.parts)
    return sorted(set(files))


def _enabled_flags(node, path="") -> list[str]:
    """Dotted paths of every unsafeDynamicTemplatePlugins flag that is set to true."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key == FLAG_BLOCK and isinstance(value, dict):
                found.extend(f"{here}.{flag}" for flag in FLAGS if value.get(flag) is True)
            else:
                found.extend(_enabled_flags(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_enabled_flags(item, f"{path}[{index}]"))
    return found


def test_the_scan_finds_values_files():
    assert HELM_DIR / "values.yaml" in _values_files()


@pytest.mark.parametrize(
    "values_file",
    [p for p in _values_files() if p.name not in LOCAL_DEVELOPMENT_OVERLAYS],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_shipped_values_file_enables_dynamic_plugins(values_file):
    enabled = _enabled_flags(yaml.safe_load(values_file.read_text()) or {})
    assert enabled == [], f"{values_file.relative_to(REPO_ROOT)} enables {enabled}"


def test_the_local_overlay_is_the_only_exception_and_really_enables_the_feature():
    # If the overlay stops enabling anything it no longer needs an exemption; keep the list honest.
    overlay = HELM_DIR / "values-template-preview.yaml"
    assert overlay.name in LOCAL_DEVELOPMENT_OVERLAYS
    assert _enabled_flags(yaml.safe_load(overlay.read_text())) != []


def test_the_scanner_detects_an_enabled_flag():
    document = {
        "templatePreview": {
            FLAG_BLOCK: {"enabled": True, "acknowledgeArbitraryCodeExecution": False}
        }
    }
    assert _enabled_flags(document) == [f"templatePreview.{FLAG_BLOCK}.enabled"]
    assert _enabled_flags({"renderService": {FLAG_BLOCK: {"enabled": False}}}) == []


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_the_ci_profile_renders_no_dynamic_plugin_machinery():
    rendered = subprocess.run(
        ["helm", "template", "test", ".", "--values", "values-ci.yaml"],
        cwd=HELM_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "NVCM_DYNAMIC_PLUGINS_DIR" not in rendered
    assert "fetch-plugin-artifact" not in rendered
