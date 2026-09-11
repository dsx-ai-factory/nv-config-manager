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
"""Tests for the reusable Render client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager_workflows.clients.render import (
    FileCommit,
    RenderClient,
    RenderClientException,
)


def _session_returning(payload: dict[str, object]) -> MagicMock:
    response = AsyncMock()
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_constructs_from_explicit_settings_with_unchanged_policy() -> None:
    settings = {
        "base_url": "https://render.example/",
        "client_certificate": None,
        "headers": {"Authorization": "Bearer token"},
    }

    client = RenderClient(**settings)
    try:
        assert client.base_url == "https://render.example"
        assert client.timeout.total == 30
        assert client.retry_options.attempts == 5
        assert client.retry_options.get_timeout(0) == 1.0
        assert client.retry_options.get_timeout(4) == 10.0
        assert client.retry_options.statuses == {409, 500, 502, 503, 504}
    finally:
        await client.connector.close()


@pytest.mark.asyncio
async def test_execute_render_preserves_request_and_response_contract() -> None:
    session = _session_returning(
        {"updated_files": [{"filename": "startup.yaml", "commit": "abc123"}]}
    )
    client = RenderClient(base_url="https://render.example")

    try:
        with patch(
            "nv_config_manager_workflows.clients._http.RetryClient",
            return_value=session,
        ):
            result = await client.execute_render("device/id", "workflow-1")

        session.post.assert_called_once_with(
            "https://render.example/v1/render/device/id/render",
            json={"commit_message": "Render triggered by workflow workflow-1"},
        )
        assert result == [FileCommit(filename="startup.yaml", commit="abc123")]
    finally:
        await client.connector.close()


@pytest.mark.asyncio
async def test_execute_render_wraps_http_failures() -> None:
    session = _session_returning({})
    response = session.post.return_value
    response.raise_for_status.side_effect = RuntimeError("request failed")
    client = RenderClient(base_url="https://render.example")

    try:
        with (
            patch(
                "nv_config_manager_workflows.clients._http.RetryClient",
                return_value=session,
            ),
            pytest.raises(RenderClientException, match="Failed to render device: request failed"),
        ):
            await client.execute_render("device-1", "workflow-1")
    finally:
        await client.connector.close()
