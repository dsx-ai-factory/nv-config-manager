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
"""Tests for Temporal worker startup composition."""

from typing import Any
from unittest.mock import AsyncMock

from pytest_mock import MockerFixture

from nv_config_manager.temporal.worker import main as worker_main


async def test_runtime_is_configured_before_worker_construction(mocker: MockerFixture) -> None:
    """Activity dependencies are installed before the worker registers activities."""
    startup_events: list[str] = []
    mocker.patch.object(
        worker_main,
        "configure_workflow_runtime",
        side_effect=lambda: startup_events.append("configure-runtime"),
    )
    mocker.patch.object(worker_main, "setup_telemetry", return_value=mocker.sentinel.runtime)
    mocker.patch.object(worker_main, "temporal_address", return_value="temporal.example:7233")
    mocker.patch.object(worker_main, "client_connect_options", return_value={})
    mocker.patch.object(worker_main, "get_data_converter", return_value=mocker.sentinel.converter)

    async def connect(*args: Any, **kwargs: Any) -> Any:
        startup_events.append("connect-client")
        return mocker.sentinel.client

    mocker.patch.object(worker_main.Client, "connect", side_effect=connect)
    worker = mocker.Mock()
    worker.run = AsyncMock(side_effect=lambda: startup_events.append("run-worker"))

    def build_worker(*args: Any, **kwargs: Any) -> Any:
        startup_events.append("construct-worker")
        return worker

    mocker.patch.object(worker_main, "Worker", side_effect=build_worker)

    await worker_main.main()

    assert startup_events == [
        "configure-runtime",
        "connect-client",
        "construct-worker",
        "run-worker",
    ]
