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
"""Tests that the archive consumer never forwards device secrets to a backend."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager.temporal.archive import main as archive_main


@pytest.mark.asyncio
async def test_handle_archive_msg_redacts_stage_diffs_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret values in stage outputs are redacted before the record reaches NVDataflow."""
    secret_diff = (
        "nv set system aaa user admin hashed-password $6$salt$hashvalue\n"
        "nv set vrf default router bgp neighbor 10.0.0.1 password BgpSecret"
    )
    workflow_response = MagicMock()
    workflow_response.model_dump.return_value = {
        "id": "wf-1",
        "stages": [{"output": {"diff": secret_diff}}],
    }
    archive_nvdataflow = AsyncMock()
    monkeypatch.setattr(archive_main, "ARCHIVE_BACKEND", "nvdataflow")

    msg = MagicMock()
    msg.data = json.dumps({"workflow_id": "wf-1"}).encode("utf-8")

    with (
        patch.object(archive_main, "get_client", AsyncMock(return_value=MagicMock())),
        patch.object(
            archive_main.WorkflowDetailResponse,
            "from_handle",
            AsyncMock(return_value=workflow_response),
        ),
        patch.object(archive_main, "_archive_nvdataflow", archive_nvdataflow),
    ):
        await archive_main.handle_archive_msg(msg)

    posted = json.dumps(archive_nvdataflow.await_args.args[0])
    assert "hashvalue" not in posted
    assert "BgpSecret" not in posted
    assert "hashed-password <redacted>" in posted
    assert "password <redacted>" in posted
