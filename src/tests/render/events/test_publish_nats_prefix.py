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
"""Tests that render publishers address JetStream through the configured API prefix.

Publishing crosses the same account boundary the consumers do, so a publisher that
opens a JetStream context without a prefix would talk to ``$JS.API`` regardless of
configuration and fail once the stream is owned by another account.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager.render.events.util import queue_render, queue_render_batch

BASE_NATS_CONFIG = """
[nats]
server = nats://nats.example.local:4222
queue = nv-config-manager
auth_method = none
config_manager_stream = nv-config-manager
render_change_subject = nv-config-manager.nautobotchange
"""

PREFIXED_NATS_CONFIG = BASE_NATS_CONFIG + "config_manager_api_prefix = $JS.CUSTOM.API\n"


@pytest.fixture
def mock_nats_conn():
    """Patch the shared connection manager so publishing never touches a real server."""
    conn = MagicMock()
    conn.is_closed = False
    conn.jetstream = MagicMock(return_value=MagicMock())
    conn.close = AsyncMock()

    with (
        patch("nv_config_manager.render.events.util.NATSConnectionManager") as manager,
        patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None),
        patch("nv_config_manager.render.events.util._close_queue_redis_client", AsyncMock()),
    ):
        manager.return_value.get_connection.return_value = conn
        yield conn


def _prefix_of(conn: MagicMock) -> str:
    """Return the prefix the publisher opened its JetStream context with."""
    return conn.jetstream.call_args.kwargs["prefix"]


@pytest.mark.asyncio
async def test_queue_render_defaults_to_standard_prefix(custom_ini, mock_nats_conn):
    """An unset prefix leaves the publisher on the JetStream default."""
    custom_ini(BASE_NATS_CONFIG)

    with patch(
        "nv_config_manager.render.events.util._process_single_device_enqueue",
        AsyncMock(return_value=None),
    ):
        await queue_render(
            "device-uuid",
            "commit",
            "user",
            "2026-01-01T00:00:00Z",
            dcim_client=AsyncMock(),
        )

    assert _prefix_of(mock_nats_conn) == "$JS.API"


@pytest.mark.asyncio
async def test_queue_render_follows_config_manager_prefix(custom_ini, mock_nats_conn):
    """Render jobs are a subject on the config-manager stream, so they share its prefix."""
    custom_ini(PREFIXED_NATS_CONFIG)

    with patch(
        "nv_config_manager.render.events.util._process_single_device_enqueue",
        AsyncMock(return_value=None),
    ):
        await queue_render(
            "device-uuid",
            "commit",
            "user",
            "2026-01-01T00:00:00Z",
            dcim_client=AsyncMock(),
        )

    assert _prefix_of(mock_nats_conn) == "$JS.CUSTOM.API"


@pytest.mark.asyncio
async def test_queue_render_batch_follows_config_manager_prefix(custom_ini, mock_nats_conn):
    """The batch path opens its own JetStream context and must carry the prefix too."""
    custom_ini(PREFIXED_NATS_CONFIG)

    with patch(
        "nv_config_manager.render.events.util._process_single_device_enqueue",
        AsyncMock(return_value=None),
    ):
        queued, failed = await queue_render_batch(
            ["device-a", "device-b"],
            "commit",
            "user",
            "2026-01-01T00:00:00Z",
            dcim_client=AsyncMock(),
        )

    assert _prefix_of(mock_nats_conn) == "$JS.CUSTOM.API"
    assert queued == 2
    assert failed == []
