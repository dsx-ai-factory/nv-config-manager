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
"""Replay histories captured before the IB/DCIM package extraction."""

import pytest
from temporalio.worker import Replayer

from nv_config_manager.temporal.converter import get_data_converter
from nv_config_manager.temporal.ngc.workflows.ib_pkey_creation import IBPKeyCreationWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_add import IBPKeyMemberAddWorkflow
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_delete import (
    IBPKeyMemberDeleteWorkflow,
)
from nv_config_manager.temporal.ngc.workflows.ib_pkey_member_update import (
    IBPKeyMemberUpdateWorkflow,
)
from tests.temporal.replay.history import load_history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_class", "history_filename"),
    [
        (IBPKeyCreationWorkflow, "ib_pkey_creation.json"),
        (IBPKeyMemberAddWorkflow, "ib_pkey_member_add.json"),
        (IBPKeyMemberDeleteWorkflow, "ib_pkey_member_delete.json"),
        (IBPKeyMemberUpdateWorkflow, "ib_pkey_member_update.json"),
    ],
)
async def test_pre_extraction_history_replays(
    workflow_class: type,
    history_filename: str,
) -> None:
    """Package moves must not change commands recorded by existing IB workflows."""
    history = load_history(history_filename)
    replayer = Replayer(
        workflows=[workflow_class],
        data_converter=get_data_converter(),
    )

    await replayer.replay_workflow(history)
