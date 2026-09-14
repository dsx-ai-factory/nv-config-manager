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
"""Compatibility contracts for the service-owned Temporal Redis cache helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

from nv_config_manager.common.client import RedisClient as CommonRedisClient
from nv_config_manager.temporal.client.redis import RedisClient
from nv_config_manager_workflows.clients import RedisClient as WorkflowRedisClient


def _client() -> RedisClient:
    with patch(
        "nv_config_manager_workflows.clients.redis.redis_asyncio.Redis",
        return_value=MagicMock(),
    ):
        return RedisClient(host="redis.example.com")


def test_legacy_redis_paths_share_the_extracted_base() -> None:
    """Common and Temporal imports continue to use the package-owned implementation."""
    assert CommonRedisClient is WorkflowRedisClient
    assert issubclass(RedisClient, WorkflowRedisClient)


def test_temporal_cache_key_formats_are_unchanged() -> None:
    """Existing API cache entries remain readable after client refactoring."""
    client = _client()

    assert client.query_key("workflow-123", "pending_approval") == (
        "workflow:workflow-123:query:pending_approval"
    )
    assert client.result_key("workflow-123") == "workflow:workflow-123:result"


async def test_query_cache_helpers_delegate_to_generic_redis_operations() -> None:
    """Query helpers preserve their keys and serialization behavior."""
    client = _client()
    client.set = AsyncMock()
    client.get = AsyncMock(return_value={"approved": True})
    client.delete = AsyncMock()

    await client.cache_query("workflow-123", "pending_approval", {"approved": True})
    result = await client.get_cached_query("workflow-123", "pending_approval")
    await client.delete_cached_query("workflow-123", "pending_approval")

    key = "workflow:workflow-123:query:pending_approval"
    client.set.assert_awaited_once_with(key, {"approved": True})
    client.get.assert_awaited_once_with(key)
    client.delete.assert_awaited_once_with(key)
    assert result == {"approved": True}


async def test_result_cache_helpers_delegate_to_generic_redis_operations() -> None:
    """Result helpers preserve their keys and serialization behavior."""
    client = _client()
    client.set = AsyncMock()
    client.get = AsyncMock(return_value={"status": "complete"})

    await client.cache_result("workflow-123", {"status": "complete"})
    result = await client.get_cached_result("workflow-123")

    key = "workflow:workflow-123:result"
    client.set.assert_awaited_once_with(key, {"status": "complete"})
    client.get.assert_awaited_once_with(key)
    assert result == {"status": "complete"}
