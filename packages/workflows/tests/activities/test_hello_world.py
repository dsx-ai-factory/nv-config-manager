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
"""Tests for the reusable hello-world activities."""

import inspect

from nv_config_manager_workflows.activities.hello_world import (
    HELLO_WORLD_ACTIVITIES,
    hello_world_activity,
    hello_world_prompt_activity,
    hello_world_reject_activity,
)


def test_hello_world_activity_signatures_are_stable() -> None:
    """Callable signatures remain compatible with existing workflow histories."""
    assert str(inspect.signature(hello_world_activity)) == "(name: str) -> str"
    assert str(inspect.signature(hello_world_prompt_activity)) == "() -> str"
    assert str(inspect.signature(hello_world_reject_activity)) == "() -> str"


async def test_hello_world_activity_results_are_stable() -> None:
    """Greeting, prompt, and rejection text remain unchanged."""
    assert await hello_world_activity("Ada") == "Hello, Ada!"
    assert await hello_world_prompt_activity() == "Would you like to be greeted?"
    assert await hello_world_reject_activity() == "Goodbye!"


def test_hello_world_activity_catalog_is_stable() -> None:
    """The domain catalog contains each reusable function exactly once."""
    assert HELLO_WORLD_ACTIVITIES == (
        hello_world_activity,
        hello_world_prompt_activity,
        hello_world_reject_activity,
    )
    assert len(HELLO_WORLD_ACTIVITIES) == len(set(HELLO_WORLD_ACTIVITIES))
