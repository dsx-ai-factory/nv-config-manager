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
"""Tests for the cache refresh service entrypoint."""

import asyncio
import configparser
import os
import signal
from unittest.mock import AsyncMock, MagicMock

from pytest_mock import MockerFixture

from nv_config_manager.config_store import cache_refresh_service


async def test_sigterm_wakes_the_service_and_closes_its_clients(mocker: MockerFixture) -> None:
    """A shutdown request must end the wait at once, not at the next refresh."""
    config = configparser.ConfigParser()
    config.read_string("[redis]\nhost = redis\nport = 6379\ndb = 0\n[dcim]\n")
    mocker.patch.object(cache_refresh_service, "settings", MagicMock(config=config))
    cache_service = MagicMock()
    cache_service.dcim_client.close = AsyncMock()
    cache_service.redis_client.close = AsyncMock()
    mocker.patch.object(
        cache_refresh_service.DeviceCacheService,
        "from_config",
        new=AsyncMock(return_value=cache_service),
    )
    refreshing = asyncio.Event()

    async def refresh_forever(**_kwargs: object) -> None:
        refreshing.set()
        await asyncio.Event().wait()

    mocker.patch.object(cache_refresh_service, "background_cache_refresh_loop", refresh_forever)
    mocker.patch.object(cache_refresh_service, "shutdown_event", asyncio.Event())

    running = asyncio.create_task(cache_refresh_service.main())
    await asyncio.wait_for(refreshing.wait(), timeout=5.0)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(running, timeout=5.0)

    cache_service.dcim_client.close.assert_awaited_once()
    cache_service.redis_client.close.assert_awaited_once()
