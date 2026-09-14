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
"""Tests for shared reusable HTTP client behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager_workflows.clients.render import RenderClient


def _session_returning(payload: dict[str, object]) -> MagicMock:
    response = AsyncMock()
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_whoami_uses_retry_client_and_refreshes_callable_headers() -> None:
    tokens = iter(("token-v1", "token-v2"))

    def headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {next(tokens)}"}

    first_session = _session_returning({"user": "render", "roles": ["all"]})
    second_session = _session_returning({"user": "render", "roles": ["all"]})
    client = RenderClient(base_url="https://render.example/", headers=headers)

    try:
        with patch(
            "nv_config_manager_workflows.clients._http.RetryClient",
            side_effect=(first_session, second_session),
        ) as retry_client:
            first = await client.whoami()
            second = await client.whoami()

        assert first == second == {"user": "render", "roles": ["all"]}
        first_session.get.assert_called_once_with("https://render.example/whoami")
        second_session.get.assert_called_once_with("https://render.example/whoami")
        assert retry_client.call_args_list[0].kwargs["headers"] == {
            "Authorization": "Bearer token-v1"
        }
        assert retry_client.call_args_list[1].kwargs["headers"] == {
            "Authorization": "Bearer token-v2"
        }
        assert retry_client.call_args_list[0].kwargs["retry_options"] is client.retry_options
        assert retry_client.call_args_list[0].kwargs["connector_owner"] is False
    finally:
        await client.connector.close()
