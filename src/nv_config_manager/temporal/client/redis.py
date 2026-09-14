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
"""Application-side Redis cache for Temporal API responses.

Extends the reusable client with HTTP query and workflow-result caching methods.
"""

from __future__ import annotations

from typing import Any

from nv_config_manager.common.client import RedisClient as BaseRedisClient


class RedisClient(BaseRedisClient):
    """Redis client for application-side Temporal API caching.

    The query and result helpers remain in the application because reusable
    activities use only the generic Redis client.
    """

    def query_key(self, workflow_id: str, query: str) -> str:
        """Generate cache key for a workflow query."""
        return f"workflow:{workflow_id}:query:{query}"

    async def cache_query(self, workflow_id: str, query: str, data: Any) -> None:
        """Cache workflow query data."""
        key = self.query_key(workflow_id, query)
        await self.set(key, data)

    async def get_cached_query(self, workflow_id: str, query: str) -> Any | None:
        """Get cached workflow query data."""
        key = self.query_key(workflow_id, query)
        return await self.get(key)

    async def delete_cached_query(self, workflow_id: str, query: str) -> None:
        """Delete cached workflow query data."""
        key = self.query_key(workflow_id, query)
        await self.delete(key)

    def result_key(self, workflow_id: str) -> str:
        """Generate cache key for workflow result."""
        return f"workflow:{workflow_id}:result"

    async def cache_result(self, workflow_id: str, data: Any) -> None:
        """Cache workflow result data."""
        key = self.result_key(workflow_id)
        await self.set(key, data)

    async def get_cached_result(self, workflow_id: str) -> Any | None:
        """Get cached workflow result data."""
        key = self.result_key(workflow_id)
        return await self.get(key)
