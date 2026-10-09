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
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.models import DCIMDeviceSelection

from nv_config_manager_workflows.form_option_providers.diagnostics import (
    DiagnosticsCommandOptionsQuery,
    resolve_diagnostics_command_options,
)
from nv_config_manager_workflows.runtime import configure_dcim_client


async def test_resolver_uses_the_provider_neutral_runtime_client() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_devices = AsyncMock(
        return_value=[
            DCIMDeviceSelection(
                id="device-1",
                name="leaf-1",
                platform="cumulus-linux",
            )
        ]
    )
    configure_dcim_client(lambda: cast(DCIMClient, client))

    response = await resolve_diagnostics_command_options(
        DiagnosticsCommandOptionsQuery(device_id=["device-1", "missing-device"])
    )

    assert response.items
    assert response.meta is not None
    assert response.meta.warnings == ["Some selected devices could not be resolved."]


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
