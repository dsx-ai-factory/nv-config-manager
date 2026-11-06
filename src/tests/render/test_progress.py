# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for progress that render work reports to the consumer handling its message."""

import asyncio
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nv_config_manager.render.events.util import queue_render_batch
from nv_config_manager.render.progress import note_progress, track_progress

NATS_CONFIG = """
[nats]
server = nats://nats.example.local:4222
queue = nv-config-manager
auth_method = none
config_manager_stream = nv-config-manager
render_change_subject = nv-config-manager.nautobotchange
"""


@pytest.fixture
def mock_nats_conn() -> Iterator[MagicMock]:
    """Patch the shared connection manager so publishing never touches a real server."""
    conn = MagicMock()
    conn.is_closed = False
    conn.close = AsyncMock()

    with (
        patch("nv_config_manager.render.events.util.NATSConnectionManager") as manager,
        patch("nv_config_manager.render.events.util._get_queue_redis_client", return_value=None),
        patch("nv_config_manager.render.events.util._close_queue_redis_client", AsyncMock()),
    ):
        manager.return_value.get_connection.return_value = conn
        yield conn


def test_progress_outside_a_tracked_handler_is_ignored() -> None:
    """Render work also runs from the API, where no consumer is waiting on it."""
    note_progress()

    with track_progress() as progress:
        pass

    assert progress.steps == 0


@pytest.mark.asyncio
async def test_progress_from_tasks_started_by_the_handler_is_counted() -> None:
    """Batch work runs in child tasks, which must report to the handler that started them."""
    with track_progress() as progress:
        await asyncio.gather(*(asyncio.create_task(_step()) for _ in range(3)))

    assert progress.steps == 3


async def _step() -> None:
    note_progress()


@pytest.mark.asyncio
async def test_batch_notes_progress_for_every_device(
    custom_ini: Callable[[str], None], mock_nats_conn: MagicMock
) -> None:
    """Each device queued, or failed, is a step the consumer can report as progress."""
    custom_ini(NATS_CONFIG)

    with (
        patch(
            "nv_config_manager.render.events.util._process_single_device_enqueue",
            AsyncMock(side_effect=[None, {"device_uuid": "device-b", "error": "disabled"}, None]),
        ),
        track_progress() as progress,
    ):
        await queue_render_batch(
            ["device-a", "device-b", "device-c"],
            "commit",
            "user",
            "2026-01-01T00:00:00Z",
            dcim_client=AsyncMock(),
        )

    assert progress.steps == 3
