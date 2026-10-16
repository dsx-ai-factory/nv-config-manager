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
"""Legacy factory clients use the shared generated response adapter."""

from nv_config_manager.common.client.temporal import TemporalClient


async def test_response_payload_falls_back_to_text_for_invalid_json(aioresponses) -> None:
    aioresponses.get("http://service.example/v1/workflow/wf", body="not-json")
    async with TemporalClient(
        base_url="http://service.example", user_domain="example.com"
    ) as client:
        assert await client.get_workflow("wf") == "not-json"
