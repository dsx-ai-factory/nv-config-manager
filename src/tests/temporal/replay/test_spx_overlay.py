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
"""Replay contracts for SpX overlay workflows."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.spx_overlay import (
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
)
from tests.temporal.replay.history import load_history

_SPX_WORKFLOWS = [
    SpXOverlayAssignmentWorkflow,
    SpXOverlayCreationWorkflow,
    SpXOverlayDeletionWorkflow,
    SpXOverlayTenantChangeWorkflow,
]
_HISTORY_FILENAMES = (
    "spx_overlay_creation.json",
    "spx_overlay_deletion.json",
    "spx_overlay_assignment.json",
    "spx_overlay_tenant_change.json",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _HISTORY_FILENAMES)
async def test_spx_overlay_histories_replay(history_filename: str) -> None:
    """The extracted activities remain deterministic against main-era histories."""
    replayer = Replayer(workflows=_SPX_WORKFLOWS, data_converter=get_data_converter())

    await replayer.replay_workflow(load_history(history_filename))
