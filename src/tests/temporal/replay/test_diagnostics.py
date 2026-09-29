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
"""Replay contracts for diagnostics and ticketing workflows."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.diagnostics import DiagnosticsWorkflow
from tests.temporal.replay.history import load_history

_HISTORY_FILENAMES = (
    "diagnostics_ticketed.json",
    "diagnostics_ticketless.json",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_filename", _HISTORY_FILENAMES)
async def test_diagnostics_histories_replay(history_filename: str) -> None:
    """Package extraction remains deterministic against captured diagnostics histories."""
    replayer = Replayer(workflows=[DiagnosticsWorkflow], data_converter=get_data_converter())

    await replayer.replay_workflow(load_history(history_filename))
