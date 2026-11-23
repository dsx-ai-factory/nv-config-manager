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
"""The dynamic plugin gate opens only when enabled AND acknowledged come from the same block.

Plugins run as arbitrary code in the render service, so the acknowledgement is not optional. The chart
used to turn the whole plugin wiring (the artifact-fetch init container and NVCM_DYNAMIC_PLUGINS_DIR) on
from ``enabled`` alone, and let ``enabled`` come from one block (templatePreview.unsafeDynamicTemplatePlugins
or the render service's own) while ``acknowledgeArbitraryCodeExecution`` came from the other.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HELM_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

RS = "renderService.unsafeDynamicTemplatePlugins"
TP = "templatePreview.unsafeDynamicTemplatePlugins"
TP_ON = ["templatePreview.enabled=true"]


def _render(*sets: str) -> str:
    command = ["helm", "template", "test", ".", "--values", "values-ci.yaml"]
    for setting in sets:
        command += ["--set", setting]
    return subprocess.run(command, cwd=HELM_DIR, capture_output=True, text=True, check=True).stdout


CASES = {
    "render service enabled only": ([f"{RS}.enabled=true"], False),
    "render service acknowledged only": ([f"{RS}.acknowledgeArbitraryCodeExecution=true"], False),
    "render service both": (
        [f"{RS}.enabled=true", f"{RS}.acknowledgeArbitraryCodeExecution=true"],
        True,
    ),
    "template preview enabled only": ([*TP_ON, f"{TP}.enabled=true"], False),
    "template preview acknowledged only": (
        [*TP_ON, f"{TP}.acknowledgeArbitraryCodeExecution=true"],
        False,
    ),
    "template preview both": (
        [*TP_ON, f"{TP}.enabled=true", f"{TP}.acknowledgeArbitraryCodeExecution=true"],
        True,
    ),
    "template preview both but the component is off": (
        [f"{TP}.enabled=true", f"{TP}.acknowledgeArbitraryCodeExecution=true"],
        False,
    ),
    "enabled from the render service, acknowledged from template preview": (
        [f"{RS}.enabled=true", *TP_ON, f"{TP}.acknowledgeArbitraryCodeExecution=true"],
        False,
    ),
    "acknowledged from the render service, enabled from template preview": (
        [f"{RS}.acknowledgeArbitraryCodeExecution=true", *TP_ON, f"{TP}.enabled=true"],
        False,
    ),
}


@pytest.mark.parametrize("sets, open_", CASES.values(), ids=CASES.keys())
def test_the_plugin_machinery_exists_only_when_one_block_has_both_flags(sets, open_):
    rendered = _render(*sets)

    assert ("NVCM_DYNAMIC_PLUGINS_DIR" in rendered) is open_
    assert ("fetch-plugin-artifact" in rendered) is open_


@pytest.mark.parametrize("sets, open_", CASES.values(), ids=CASES.keys())
def test_the_flags_written_into_the_service_config_agree_with_the_gate(sets, open_):
    rendered = _render(*sets)

    expected = str(open_).lower()
    for name in ("enabled", "acknowledge_arbitrary_code_execution"):
        values = re.findall(rf"unsafe_dynamic_template_plugins_{name} = (\w+)", rendered)
        assert set(values) <= {expected}, (name, values)
