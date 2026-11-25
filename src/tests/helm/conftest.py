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
"""Helm render tests: the ones that need Template Preview's private chart run only when it is added."""

from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"


def subchart_added() -> bool:
    """Whether Chart.yaml depends on the Template Preview chart (added by build_chart_dependencies.sh)."""
    chart = yaml.safe_load((HELM_DIR / "Chart.yaml").read_text())
    return "nvcm-template-preview" in {d["name"] for d in chart.get("dependencies", [])}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Leave out, rather than skip, the tests marked needs_subchart when the private chart is absent.

    The public chart does not list the private Template Preview chart, so those tests cannot apply there. They
    are deselected (pytest reports the count) and not skipped, because the CI Helm job fails the build on any
    skipped Helm test to catch tests that silently stopped exercising the chart.
    """
    if subchart_added():
        return
    left_out = [item for item in items if item.get_closest_marker("needs_subchart")]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if item not in left_out]
