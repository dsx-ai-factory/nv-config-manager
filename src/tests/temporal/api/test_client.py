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
"""The workflow API's Temporal client must record the configured retry window."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from temporalio.contrib.opentelemetry import TracingInterceptor

from nv_config_manager.temporal.api import workflow_v1
from nv_config_manager_workflows.retry_timeout import FailedStageRetryTimeoutInterceptor


@pytest.mark.asyncio
async def test_api_client_records_the_configured_retry_window(
    monkeypatch: pytest.MonkeyPatch, custom_ini: Callable[[str], None]
) -> None:
    """Workflows started from the API carry the configured window, not the default."""
    custom_ini("[temporal.worker]\nfailed_stage_retry_timeout_seconds = 7200\n")
    client = object()
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(workflow_v1.Client, "connect", connect)
    monkeypatch.setattr(workflow_v1, "temporal_address", lambda: "temporal.example:7233")
    monkeypatch.setattr(workflow_v1, "client_connect_options", lambda: {})
    monkeypatch.setattr(workflow_v1, "get_data_converter", lambda: None)
    monkeypatch.setattr(workflow_v1, "get_runtime", lambda: None)

    assert await workflow_v1.get_client() is client

    assert connect.await_args is not None
    tracing, retry_window = connect.await_args.kwargs["interceptors"]
    assert isinstance(tracing, TracingInterceptor)
    assert isinstance(retry_window, FailedStageRetryTimeoutInterceptor)
    assert retry_window.seconds == 7200
