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
"""Replay contracts for inventory and network validation workflows."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.workflows.connected_host import (
    ConnectedHostMetadataWorkflow,
)
from nv_config_manager_workflows.workflows.infiniband_cable_validation import (
    InfinibandCableValidationWorkflow,
)
from nv_config_manager_workflows.workflows.lldp import PortLLDPInfoWorkflow

from .history import load_history

_WORKFLOWS = [
    ConnectedHostMetadataWorkflow,
    InfinibandCableValidationWorkflow,
    PortLLDPInfoWorkflow,
]
_HISTORY_FILENAMES = (
    "connected_host_metadata.json",
    "infiniband_cable_validation.json",
    "port_lldp_info.json",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _HISTORY_FILENAMES)
async def test_inventory_and_validation_history_replays(history_filename: str) -> None:
    """Activity extraction remains deterministic against histories captured from main."""
    replayer = Replayer(
        workflows=_WORKFLOWS,
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(load_history(history_filename))
