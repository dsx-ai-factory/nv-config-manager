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
"""Tests for diagnostics form options owned by the workflows package."""

import subprocess
import sys

from nv_config_manager_workflows.form_option_providers.diagnostics import (
    DiagnosticsCommandOptionsQuery,
    resolve_diagnostics_command_options,
)


async def test_resolver_builds_commands_from_selected_device_platforms() -> None:
    response = await resolve_diagnostics_command_options(
        DiagnosticsCommandOptionsQuery(platform=["cumulus-linux", "unsupported-os"])
    )

    assert response.items
    assert response.meta is not None
    assert response.meta.warnings == [
        "Diagnostic command options are unavailable for unsupported platforms: unsupported-os."
    ]


def test_importing_the_workflow_does_not_import_its_api_only_resolver() -> None:
    script = "\n".join(
        [
            "import sys",
            "import nv_config_manager_workflows.workflows.diagnostics",
            (
                "assert 'nv_config_manager_workflows.form_option_providers.diagnostics' "
                "not in sys.modules"
            ),
        ]
    )

    subprocess.run([sys.executable, "-c", script], check=True)
