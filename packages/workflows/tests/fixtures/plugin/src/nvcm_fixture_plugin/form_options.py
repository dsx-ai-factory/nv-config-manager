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
"""API-only form option provider used by the fixture workflow plugin."""

from pydantic import BaseModel, ConfigDict

from nv_config_manager_workflows.ui import OptionItem, OptionSourceResponse


class FixtureMessageQuery(BaseModel):
    """Validated query parameters for the fixture's generated option route."""

    model_config = ConfigDict(extra="forbid")

    prefix: str


async def list_fixture_messages(query: FixtureMessageQuery) -> OptionSourceResponse:
    """Return deterministic example choices without depending on FastAPI."""
    return OptionSourceResponse(
        items=[
            OptionItem(label="Hello", value=f"{query.prefix} hello"),
            OptionItem(label="Goodbye", value=f"{query.prefix} goodbye"),
        ]
    )


__all__ = ["FixtureMessageQuery", "list_fixture_messages"]
