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
"""Replay contracts for device validation, password, and Redfish workflows."""

from unittest.mock import AsyncMock, patch

import pytest
from temporalio.worker import Replayer

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.workflows.bmc import RedfishProvisioningWorkflow
from nv_config_manager_workflows.workflows.cable_validation import (
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.cumulus_hardware_validation import (
    ValidateHardwareWorkflow,
)
from nv_config_manager_workflows.workflows.device_password_rotation import (
    DevicePasswordRotationWorkflow,
)
from nv_config_manager_workflows.workflows.site_password_rotation import (
    SitePasswordRotationWorkflow,
)

from .history import load_history

_DEVICE_OPERATION_WORKFLOWS = [
    DeviceCableValidationWorkflow,
    SiteCableValidationWorkflow,
    ValidateHardwareWorkflow,
    DevicePasswordRotationWorkflow,
    SitePasswordRotationWorkflow,
    RedfishProvisioningWorkflow,
]
_HISTORY_FILENAMES = (
    "device_cable_validation.json",
    "site_cable_validation.json",
    "hardware_validation.json",
    "device_password_rotation.json",
    "site_password_rotation.json",
    "redfish_provisioning.json",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _HISTORY_FILENAMES)
async def test_device_operation_histories_replay(history_filename: str) -> None:
    """Package extraction remains deterministic against captured device histories."""
    replayer = Replayer(
        workflows=_DEVICE_OPERATION_WORKFLOWS,
        data_converter=get_data_converter(),
    )

    if history_filename == "redfish_provisioning.json":
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await replayer.replay_workflow(load_history(history_filename))
    else:
        await replayer.replay_workflow(load_history(history_filename))
