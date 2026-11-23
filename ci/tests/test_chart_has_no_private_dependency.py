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
"""The public chart must not depend on a chart from a private registry.

Template Preview's chart is published to a private registry. Trusted internal CI jobs add it to
``Chart.yaml`` just before packaging (``.gitlab/ci/scripts/build_chart_dependencies.sh``); the
checked-in ``Chart.yaml`` and ``Chart.lock`` must never name it, or a clone of this public
repository could not run ``helm dependency build``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm"
PRIVATE_CHART = "nvcm-template-preview"


def _dependencies(filename: str) -> list[dict]:
    return yaml.safe_load((HELM_DIR / filename).read_text()).get("dependencies", [])


def test_chart_yaml_does_not_depend_on_the_private_chart() -> None:
    assert PRIVATE_CHART not in {d["name"] for d in _dependencies("Chart.yaml")}


def test_chart_lock_does_not_list_the_private_chart() -> None:
    assert PRIVATE_CHART not in {d["name"] for d in _dependencies("Chart.lock")}


def test_every_chart_dependency_comes_from_a_public_repository() -> None:
    for dep in _dependencies("Chart.yaml"):
        repository = dep.get("repository", "")
        assert repository.startswith("https://"), (
            f"{dep['name']}: {repository!r} is not a public https repository"
        )
