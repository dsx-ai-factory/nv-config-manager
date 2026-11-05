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
"""Tests that archive events for workflows Temporal no longer has are dropped."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from nv_config_manager.temporal.archive import main as archive_main


def _archive_msg(workflow_id: str) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps({"workflow_id": workflow_id}).encode("utf-8")
    return msg


@pytest.mark.asyncio
async def test_handle_archive_msg_returns_when_workflow_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow past retention is skipped so the subscription acks the event."""
    archive_nvdataflow = AsyncMock()
    monkeypatch.setattr(archive_main, "ARCHIVE_BACKEND", "nvdataflow")
    missing = HTTPException(status_code=404, detail="Workflow with ID 'wf-old' not found")

    with (
        patch.object(archive_main, "get_client", AsyncMock(return_value=MagicMock())),
        patch.object(
            archive_main.WorkflowDetailResponse, "from_handle", AsyncMock(side_effect=missing)
        ),
        patch.object(archive_main, "_archive_nvdataflow", archive_nvdataflow),
    ):
        await archive_main.handle_archive_msg(_archive_msg("wf-old"))

    archive_nvdataflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_archive_msg_raises_other_temporal_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors other than a missing workflow still leave the event for redelivery."""
    monkeypatch.setattr(archive_main, "ARCHIVE_BACKEND", "nvdataflow")
    failure = HTTPException(status_code=503, detail="Temporal unavailable")

    with (
        patch.object(archive_main, "get_client", AsyncMock(return_value=MagicMock())),
        patch.object(
            archive_main.WorkflowDetailResponse, "from_handle", AsyncMock(side_effect=failure)
        ),
        patch.object(archive_main, "_archive_nvdataflow", AsyncMock()),
        pytest.raises(HTTPException) as raised,
    ):
        await archive_main.handle_archive_msg(_archive_msg("wf-1"))

    assert raised.value.status_code == 503
